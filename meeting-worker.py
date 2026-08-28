#!/usr/bin/python3
import base64
import json
import logging
import mimetypes
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


INCOMING = Path("/var/lib/meeting-assistant/incoming")
COMPLETED = Path("/var/lib/meeting-assistant/completed")
FAILED = Path("/var/lib/meeting-assistant/failed")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
MEETING_API_TOKEN = os.environ["MEETING_API_TOKEN"]
MEETING_MODEL = os.environ.get("OPENAI_MEETING_MODEL", "gpt-5.6-terra")
PROMPT = (
    "Подготовь аналитическую часть протокола производственного совещания на русском языке по транскрипции ниже. "
    "Не повторяй и не переписывай исходную диаризированную транскрипцию: программа добавит её перед твоим ответом дословно. "
    "Не выдумывай имена, должности, сроки и факты. Сохрани ссылки на спикеров и таймкоды. "
    "Структура аналитической части: 1) краткое содержание; 2) обсуждавшиеся производственные проблемы; "
    "3) принятые решения; 4) поручения с полями задача, ответственный, срок, источник; "
    "5) риски по срокам, качеству и безопасности; 6) нерешённые вопросы. "
    "Если ответственный или срок не названы, явно напиши «не указан».\n\nТРАНСКРИПЦИЯ:\n"
)
CHUNK_SECONDS = 1200
MAX_SPEAKER_REFERENCES = 4
RUSSIAN_NORMALIZATION_BATCH = 60

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("meeting-worker")


class NoSpeechError(RuntimeError):
    pass


class PermanentTranscriptionError(RuntimeError):
    pass


def multipart(fields, file_path):
    boundary = "----meeting-" + uuid.uuid4().hex
    body = bytearray()
    items = fields.items() if hasattr(fields, "items") else fields
    for name, value in items:
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    body.extend(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{file_path.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode()
    )
    body.extend(file_path.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    return boundary, body


def transcribe_result(audio_path):
    boundary, body = multipart(
        [
            ("model", "gpt-4o-transcribe"),
            ("response_format", "json"),
            ("language", "ru"),
            ("prompt", "Распознай дословно русскую речь. Не пересказывай, не исправляй и не добавляй сведения."),
        ],
        audio_path,
    )
    request = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=1800) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        log.error("TRANSCRIPTION_HTTP_ERROR code=%s body=%s", exc.code, body[:2000])
        if exc.code == 400:
            raise PermanentTranscriptionError(f"OpenAI HTTP 400: {body[:2000]}") from exc
        raise


def audio_duration(audio_path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(audio_path)],
        check=True, capture_output=True, text=True, timeout=60,
    )
    return float(result.stdout.strip())


def make_chunks(audio_path, directory):
    duration = audio_duration(audio_path)
    chunks = []
    offset = 0.0
    while offset < duration - 0.05:
        length = min(CHUNK_SECONDS, duration - offset)
        target = directory / f"chunk-{len(chunks):03d}.ogg"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(offset), "-t", str(length),
             "-i", str(audio_path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "24k",
             "-application", "voip", str(target)],
            check=True, timeout=1800,
        )
        chunks.append((target, offset))
        offset += length
    return chunks


def reference_audio(chunk, segment, directory, name):
    start = max(0.0, float(segment.get("start", 0)) + 0.15)
    available = float(segment.get("end", 0)) - start - 0.15
    if available < 2.0:
        return None
    length = min(8.0, available)
    target = directory / f"reference-{name}.ogg"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-t", str(length),
         "-i", str(chunk), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "24k", str(target)],
        check=True, timeout=120,
    )
    return "data:audio/ogg;base64," + base64.b64encode(target.read_bytes()).decode("ascii")


def transcribe_long(audio_path):
    lines = []
    raw_results = []
    with tempfile.TemporaryDirectory(prefix="meeting-chunks-") as temporary:
        directory = Path(temporary)
        for chunk_index, (chunk, offset) in enumerate(make_chunks(audio_path, directory)):
            log.info("TRANSCRIPTION_CHUNK_START index=%d offset=%.3f", chunk_index, offset)
            result = transcribe_result(chunk)
            raw_results.append({"offset": offset, "result": result})
            text = str(result.get("text", "")).strip()
            if text:
                lines.append(text)
            log.info("TRANSCRIPTION_CHUNK_FINISHED index=%d chars=%d", chunk_index, len(text))
    return "\n\n".join(lines), raw_results


def response_text(prompt, max_output_tokens=8000, verbosity="medium"):
    payload = json.dumps(
        {
            "model": MEETING_MODEL,
            "input": prompt,
            "reasoning": {"effort": "low"},
            "text": {"verbosity": verbosity},
            "max_output_tokens": max_output_tokens,
        },
        ensure_ascii=False,
    ).encode()
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=payload,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=1800) as response:
        result = json.load(response)
    texts = []
    if result.get("output_text"):
        texts.append(result["output_text"])
    for item in result.get("output", []):
        if item.get("type") == "message":
            texts.extend(c.get("text", "") for c in item.get("content", []) if c.get("type") == "output_text")
    value = "\n\n".join(dict.fromkeys(t.strip() for t in texts if t.strip()))
    if not value:
        raise RuntimeError("OpenAI returned an empty response")
    return value


def parse_json_array(value):
    value = value.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    return json.loads(value)


def normalize_russian_transcript(transcript):
    lines = transcript.splitlines()
    candidates = [
        index for index, line in enumerate(lines)
        if re.search(r"[A-Za-z]", line.partition(": ")[2])
    ]
    if not candidates:
        log.info("RUSSIAN_NORMALIZATION_SKIPPED latin_segments=0")
        return transcript
    log.info("RUSSIAN_NORMALIZATION_STARTED latin_segments=%d", len(candidates))
    for batch_start in range(0, len(candidates), RUSSIAN_NORMALIZATION_BATCH):
        indexes = candidates[batch_start:batch_start + RUSSIAN_NORMALIZATION_BATCH]
        items = []
        for index in indexes:
            items.append({
                "id": index,
                "before": lines[index - 1] if index else "",
                "text": lines[index],
                "after": lines[index + 1] if index + 1 < len(lines) else "",
            })
        instruction = (
            "Ты корректор автоматического распознавания речи. Гарантируется, что вся речь в этой записи "
            "произнесена по-русски. Для каждого объекта исправь только поле text: преобразуй ошибочно "
            "распознанную английскую речь и русскую транслитерацию латиницей в наиболее вероятную русскую "
            "фразу кириллицей. Поля before и after — только контекст. Не пересказывай, не сокращай и не "
            "добавляй сведения. Сохраняй таймкод и speaker_N в начале text без изменений. Имена, числа, "
            "аббревиатуры и общеупотребительные технические термины сохраняй по смыслу. Верни только JSON-массив "
            "объектов {\"id\": целое, \"text\": строка}; те же id, тот же порядок, без Markdown.\n\nВХОД:\n"
            + json.dumps(items, ensure_ascii=False)
        )
        expected = set(indexes)
        last_error = None
        for attempt in range(2):
            try:
                normalized = parse_json_array(response_text(instruction, max_output_tokens=6000, verbosity="low"))
                replacements = {int(item["id"]): str(item["text"]).strip() for item in normalized}
                if set(replacements) != expected or any(not value for value in replacements.values()):
                    raise ValueError("normalizer returned unexpected ids or empty text")
                for index in indexes:
                    lines[index] = replacements[index]
                break
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                log.warning("RUSSIAN_NORMALIZATION_RETRY batch=%d attempt=%d error=%s", batch_start // RUSSIAN_NORMALIZATION_BATCH, attempt + 1, exc)
        else:
            raise RuntimeError(f"Russian normalization failed: {last_error}")
    log.info("RUSSIAN_NORMALIZATION_FINISHED latin_segments=%d", len(candidates))
    return "\n".join(lines)


def format_timestamped(result):
    lines = []
    for segment in result.get("segments", []):
        start = max(0, int(float(segment.get("start", 0))))
        hours, remainder = divmod(start, 3600)
        minutes, seconds = divmod(remainder, 60)
        timestamp = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
        text = str(segment.get("text", "")).strip()
        if text:
            lines.append(f"[{timestamp}] Спикер {segment.get('speaker') or '?'}: {text}")
    return "\n".join(lines) or str(result.get("text", "")).strip()


def protocol(transcript):
    return response_text(PROMPT + transcript)


def publish(metadata, result):
    payload = json.dumps(
        {"meeting_id": metadata["meeting_id"], "title": metadata["title"], "protocol": result},
        ensure_ascii=False,
    ).encode()
    request = urllib.request.Request(
        "http://127.0.0.1:8090/api/meeting/protocol",
        data=payload,
        headers={"Authorization": f"Bearer {MEETING_API_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.load(response)


def process(metadata_path):
    working = metadata_path.with_suffix(".processing")
    metadata_path.replace(working)
    metadata = json.loads(working.read_text(encoding="utf-8"))
    audio_path = Path(metadata["audio"])
    audio_size = audio_path.stat().st_size
    log.info("PROCESSING_STARTED meeting=%s bytes=%d", metadata["meeting_id"], audio_size)
    if audio_size < 4096:
        raise NoSpeechError(f"audio file is too small: {audio_size} bytes")
    transcript, chunk_results = transcribe_long(audio_path)
    if not transcript:
        raise NoSpeechError("empty transcript")
    response = publish(metadata, transcript)
    destination = COMPLETED / metadata["meeting_id"]
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "transcript.txt").write_text(transcript, encoding="utf-8")
    (destination / "transcription-chunks.json").write_text(json.dumps(chunk_results, ensure_ascii=False, indent=2), encoding="utf-8")
    audio_path.replace(destination / audio_path.name)
    working.unlink()
    log.info("PROCESSING_FINISHED meeting=%s sent=%s failed=%s", metadata["meeting_id"], response.get("sent"), response.get("failed"))


def main():
    for directory in (INCOMING, COMPLETED, FAILED):
        directory.mkdir(parents=True, exist_ok=True)
    log.info("MEETING_WORKER_READY")
    while True:
        jobs = sorted(INCOMING.glob("*.json"))
        if not jobs:
            time.sleep(2)
            continue
        job = jobs[0]
        try:
            process(job)
        except (NoSpeechError, PermanentTranscriptionError) as exc:
            processing = job.with_suffix(".processing")
            metadata_path = processing if processing.exists() else job
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            destination = FAILED / metadata["meeting_id"]
            destination.mkdir(parents=True, exist_ok=True)
            audio_path = Path(metadata["audio"])
            if audio_path.exists():
                audio_path.replace(destination / audio_path.name)
            metadata_path.replace(destination / "metadata.json")
            (destination / "reason.txt").write_text(str(exc), encoding="utf-8")
            log.warning("PROCESSING_SKIPPED meeting=%s reason=%s", metadata["meeting_id"], type(exc).__name__)
        except Exception as exc:
            log.exception("PROCESSING_FAILED job=%s error=%s", job.name, exc)
            processing = job.with_suffix(".processing")
            if processing.exists():
                processing.replace(job)
            time.sleep(60)


if __name__ == "__main__":
    main()
