#!/usr/bin/python3
import base64
import hashlib
import hmac
import html
import json
import importlib.util
import logging
import os
import re
import secrets
import sqlite3
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HOST = "127.0.0.1"
PORT = int(os.environ.get("MEETING_ASSISTANT_PORT", "8090"))
DB_PATH = os.environ.get("MEETING_DB_PATH", "/var/lib/meeting-assistant/meeting-assistant.sqlite3")
INCOMING_DIR = os.environ.get("MEETING_INCOMING_DIR", "/var/lib/meeting-assistant/incoming")
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
TELEGRAM_KEY = os.environ.get("TELEGRAM_KEY", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
MEETING_API_TOKEN = os.environ.get("MEETING_API_TOKEN", "")
VK_CONFIRMATION_CODE = os.environ.get("VK_CONFIRMATION_CODE", "")
VK_SECRET = os.environ.get("VK_SECRET", "")
VK_GROUP_ID = os.environ.get("VK_GROUP_ID", "")
VK_GROUP_TOKEN = os.environ.get("VK_GROUP_TOKEN", "")
VK_GROUP_SCREEN_NAME = os.environ.get("VK_GROUP_SCREEN_NAME", "")
VK_API_VERSION = "5.199"
JOIN_SIGNATURE_BYTES = 12
JOIN_SIGNATURE_CHARS = len(base64.urlsafe_b64encode(b"\0" * JOIN_SIGNATURE_BYTES).decode().rstrip("="))

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("meeting-assistant")

_documents_spec = importlib.util.spec_from_file_location(
    "meeting_documents", os.path.join(os.path.dirname(__file__), "meeting-documents.py")
)
meeting_documents = importlib.util.module_from_spec(_documents_spec)
_documents_spec.loader.exec_module(meeting_documents)


def db_connect():
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def init_db():
    os.makedirs(INCOMING_DIR, exist_ok=True)
    with db_connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                platform TEXT NOT NULL,
                recipient_id TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                subscribed_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (platform, recipient_id)
            );
            CREATE TABLE IF NOT EXISTS broadcasts (
                meeting_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                transcript TEXT NOT NULL DEFAULT '',
                protocol TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                meeting_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                recipient_id TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (meeting_id, platform, recipient_id)
            );
            CREATE TABLE IF NOT EXISTS meeting_subscriptions (
                meeting_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                recipient_id TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                subscribed_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (meeting_id, platform, recipient_id)
            );
            """
        )
        broadcast_columns = {row[1] for row in db.execute("PRAGMA table_info(broadcasts)")}
        if "transcript" not in broadcast_columns:
            db.execute("ALTER TABLE broadcasts ADD COLUMN transcript TEXT NOT NULL DEFAULT ''")


def set_subscription(platform, recipient_id, active):
    now = int(time.time())
    with db_connect() as db:
        db.execute(
            """INSERT INTO subscribers(platform, recipient_id, active, subscribed_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(platform, recipient_id) DO UPDATE
               SET active=excluded.active, updated_at=excluded.updated_at""",
            (platform, str(recipient_id), int(active), now, now),
        )
    log.info("SUBSCRIPTION platform=%s recipient=%s active=%s", platform, recipient_id, str(active).lower())


def set_meeting_subscription(meeting_id, platform, recipient_id, active=True):
    now = int(time.time())
    with db_connect() as db:
        db.execute(
            """INSERT INTO meeting_subscriptions(meeting_id, platform, recipient_id, active, subscribed_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(meeting_id, platform, recipient_id) DO UPDATE
               SET active=excluded.active, updated_at=excluded.updated_at""",
            (meeting_id, platform, str(recipient_id), int(active), now, now),
        )
    log.info(
        "MEETING_SUBSCRIPTION meeting=%s platform=%s recipient=%s active=%s",
        meeting_id, platform, recipient_id, str(active).lower(),
    )
    if active:
        deliver_existing_meeting(meeting_id, platform, str(recipient_id))


def join_payload(meeting_id):
    if not MEETING_API_TOKEN or not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", meeting_id):
        return ""
    digest = hmac.new(MEETING_API_TOKEN.encode(), meeting_id.encode(), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(digest[:JOIN_SIGNATURE_BYTES]).decode().rstrip("=")
    return f"m_{meeting_id}_{signature}"


def meeting_id_from_payload(payload):
    if not re.fullmatch(r"m_[A-Za-z0-9._-]{1,240}", payload or ""):
        return ""
    value = payload[2:]
    separator = len(value) - JOIN_SIGNATURE_CHARS - 1
    if separator <= 0 or value[separator] != "_":
        return ""
    meeting_id = value[:separator]
    expected = join_payload(meeting_id)
    return meeting_id if expected and secrets.compare_digest(payload, expected) else ""


def chunks(text, limit=3500):
    text = text.strip()
    while text:
        if len(text) <= limit:
            yield text
            return
        split_at = text.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = text.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        yield text[:split_at].rstrip()
        text = text[split_at:].lstrip()


def post_form(url, fields):
    payload = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def post_multipart(url, fields, file_field, filename, content, content_type):
    boundary = "----meeting-" + uuid.uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    body.extend(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
    )
    body.extend(content)
    body.extend(f"\r\n--{boundary}--\r\n".encode("ascii"))
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def telegram(method, fields):
    if not TELEGRAM_KEY:
        raise RuntimeError("Telegram token is not configured")
    result = post_form(f"https://api.telegram.org/bot{TELEGRAM_KEY}/{method}", fields)
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API error"))
    return result


def telegram_send(chat_id, text):
    for part in chunks(text):
        telegram("sendMessage", {"chat_id": chat_id, "text": part})


def telegram_document(chat_id, filename, content):
    if not TELEGRAM_KEY:
        raise RuntimeError("Telegram token is not configured")
    result = post_multipart(
        f"https://api.telegram.org/bot{TELEGRAM_KEY}/sendDocument",
        {"chat_id": str(chat_id)}, "document", filename, content,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API error"))
    return result


_telegram_username = ""


def telegram_bot_username():
    global _telegram_username
    if not _telegram_username:
        result = telegram("getMe", {})
        _telegram_username = str((result.get("result") or {}).get("username", "")).lstrip("@")
    return _telegram_username


def vk(method, fields):
    if not VK_GROUP_TOKEN:
        raise RuntimeError("VK group access token is not configured")
    fields.update({"access_token": VK_GROUP_TOKEN, "v": VK_API_VERSION})
    result = post_form(f"https://api.vk.com/method/{method}", fields)
    if "error" in result:
        raise RuntimeError(result["error"].get("error_msg", "VK API error"))
    return result


def vk_send(peer_id, text):
    for part in chunks(text):
        vk("messages.send", {"peer_id": peer_id, "random_id": secrets.randbelow(2_147_483_647), "message": part})


def vk_document_attachment(peer_id, filename, content):
    upload = vk("docs.getMessagesUploadServer", {"peer_id": peer_id, "type": "doc"}).get("response") or {}
    upload_url = upload.get("upload_url", "")
    if not upload_url:
        raise RuntimeError("VK did not return a document upload URL")
    uploaded = post_multipart(
        upload_url, {}, "file", filename, content,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    file_token = uploaded.get("file", "")
    if not file_token:
        raise RuntimeError("VK document upload did not return a file token")
    saved = vk("docs.save", {"file": file_token, "title": filename}).get("response")
    if isinstance(saved, list):
        document = saved[0] if saved else {}
    elif isinstance(saved, dict) and isinstance(saved.get("doc"), dict):
        document = saved["doc"]
    else:
        document = saved or {}
    owner_id, document_id = document.get("owner_id"), document.get("id")
    if owner_id is None or document_id is None:
        raise RuntimeError("VK docs.save did not return a document identifier")
    attachment = f"doc{owner_id}_{document_id}"
    if document.get("access_key"):
        attachment += f"_{document['access_key']}"
    return attachment


def vk_send_documents(peer_id, documents):
    attachments = [vk_document_attachment(peer_id, filename, content) for filename, content in documents]
    vk("messages.send", {
        "peer_id": peer_id,
        "random_id": secrets.randbelow(2_147_483_647),
        "attachment": ",".join(attachments),
    })


def subscription_command(text):
    command = text.strip().lower().split(maxsplit=1)[0] if text.strip() else ""
    if "@" in command:
        command = command.split("@", 1)[0]
    if command in {"/start", "/subscribe", "подписаться", "старт", "start", "subscribe"}:
        return True
    if command in {"/stop", "/unsubscribe", "отписаться", "стоп", "stop", "unsubscribe"}:
        return False
    return None


def telegram_meeting_payload(text):
    parts = text.strip().split(maxsplit=1)
    if len(parts) != 2:
        return ""
    command = parts[0].lower().split("@", 1)[0]
    return parts[1].strip() if command == "/start" else ""


def handle_telegram(update):
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    if not chat_id:
        return
    text = message.get("text", "")
    payload = telegram_meeting_payload(text)
    if payload:
        meeting_id = meeting_id_from_payload(payload)
        if meeting_id:
            set_meeting_subscription(meeting_id, "telegram", chat_id)
            telegram_send(chat_id, "Вы подписаны только на документы этого собрания.")
        else:
            telegram_send(chat_id, "Ссылка на собрание недействительна.")
        return
    command = subscription_command(text)
    if command is True:
        set_subscription("telegram", chat_id, True)
        telegram_send(chat_id, "Вы подписаны на протоколы собраний. Для отмены: /unsubscribe")
    elif command is False:
        set_subscription("telegram", chat_id, False)
        telegram_send(chat_id, "Подписка на протоколы собраний отключена. Для возобновления: /subscribe")
    else:
        telegram_send(chat_id, "Команды: /subscribe — получать протоколы, /unsubscribe — отключить рассылку.")


def handle_vk(event):
    event_type = event.get("type", "")
    obj = event.get("object") or {}
    if event_type == "message_allow":
        user_id = obj.get("user_id")
        if user_id:
            set_subscription("vk", user_id, True)
        return
    if event_type == "message_deny":
        user_id = obj.get("user_id")
        if user_id:
            set_subscription("vk", user_id, False)
        return
    if event_type != "message_new":
        return
    message = obj.get("message") or {}
    peer_id = message.get("peer_id") or message.get("from_id")
    if not peer_id:
        return
    meeting_id = meeting_id_from_payload(str(message.get("ref", "")))
    if meeting_id:
        set_meeting_subscription(meeting_id, "vk", peer_id)
        vk_send(peer_id, "Вы подписаны только на документы этого собрания.")
        return
    command = subscription_command(message.get("text", ""))
    if command is True:
        set_subscription("vk", peer_id, True)
        vk_send(peer_id, "Вы подписаны на протоколы собраний. Напишите «Отписаться», чтобы отключить рассылку.")
    elif command is False:
        set_subscription("vk", peer_id, False)
        vk_send(peer_id, "Подписка на протоколы собраний отключена. Напишите «Подписаться», чтобы возобновить её.")
    else:
        vk_send(peer_id, "Напишите «Подписаться» или «Отписаться».")


def safe_filename(value):
    value = re.sub(r"[^A-Za-zА-Яа-яЁё0-9._ -]+", "_", value).strip(" ._")
    return value[:100] or "Собрание"


def meeting_page(payload):
    meeting_id = meeting_id_from_payload(payload)
    if not meeting_id:
        return 404, "Ссылка на собрание недействительна."
    try:
        telegram_username = telegram_bot_username()
    except Exception:
        log.exception("TELEGRAM_USERNAME_LOOKUP_FAILED")
        telegram_username = ""
    vk_name = VK_GROUP_SCREEN_NAME.strip().lstrip("@") or (f"club{VK_GROUP_ID}" if VK_GROUP_ID else "")
    telegram_url = f"https://t.me/{telegram_username}?start={urllib.parse.quote(payload)}" if telegram_username else ""
    vk_url = (
        f"https://vk.me/{urllib.parse.quote(vk_name)}?"
        + urllib.parse.urlencode({"ref": payload, "ref_source": "meeting_qr"})
        if vk_name else ""
    )
    buttons = []
    if telegram_url:
        buttons.append(f'<a class="button telegram" href="{html.escape(telegram_url, quote=True)}">Подписаться в Telegram</a>')
    if vk_url:
        buttons.append(f'<a class="button vk" href="{html.escape(vk_url, quote=True)}">Подписаться во ВКонтакте</a>')
    if not buttons:
        buttons.append('<p class="error">Ссылки на ботов пока не настроены.</p>')
    page = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#14213d"><title>Подписка на собрание</title>
<style>
:root{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#152238;background:#eef2f7}}
*{{box-sizing:border-box}}body{{margin:0;padding:24px 16px}}main{{max-width:520px;margin:auto;background:#fff;border-radius:20px;padding:26px;box-shadow:0 12px 35px #1e293b20}}
h1{{margin:0 0 10px;font-size:27px}}p{{line-height:1.5;color:#526174}}.meeting{{font-family:ui-monospace,monospace;background:#f1f5f9;border-radius:10px;padding:10px 12px;word-break:break-all}}
.button{{display:block;margin:13px 0;padding:15px 18px;border-radius:12px;color:#fff;text-decoration:none;text-align:center;font-weight:700}}.telegram{{background:#229ed9}}.vk{{background:#0077ff}}.note{{font-size:13px}}.error{{color:#b91c1c}}
</style></head><body><main><h1>Документы собрания</h1>
<p>Выберите бот. Подписка действует только для этого собрания: после обработки бот пришлёт протокол и транскрибацию в DOCX.</p>
<div class="meeting">{html.escape(meeting_id)}</div>{''.join(buttons)}
<p class="note">В Telegram нажмите «Старт». Во ВКонтакте откройте диалог и отправьте любое сообщение — метка собрания передастся боту автоматически.</p>
</main></body></html>"""
    return 200, page


def document_payloads(title, transcript, summary):
    stem = safe_filename(title)
    return [
        (f"{stem} - транскрибация.docx", meeting_documents.build_docx(f"{title} — транскрибация", transcript)),
        (f"{stem} - протокол и сводка.docx", meeting_documents.build_docx(f"{title} — протокол и сводка", summary, structured=True)),
    ]


def claim_delivery(meeting_id, platform, recipient_id):
    with db_connect() as db:
        result = db.execute(
            """INSERT INTO deliveries(meeting_id, platform, recipient_id, status, error, updated_at)
               VALUES (?, ?, ?, 'sending', NULL, ?)
               ON CONFLICT(meeting_id, platform, recipient_id) DO UPDATE
               SET status='sending', error=NULL, updated_at=excluded.updated_at
               WHERE deliveries.status='failed'""",
            (meeting_id, platform, str(recipient_id), int(time.time())),
        )
        return result.rowcount == 1


def finish_delivery(meeting_id, platform, recipient_id, status, error=None):
    with db_connect() as db:
        db.execute(
            """UPDATE deliveries SET status=?, error=?, updated_at=?
               WHERE meeting_id=? AND platform=? AND recipient_id=?""",
            (status, error, int(time.time()), meeting_id, platform, str(recipient_id)),
        )


def send_document_payloads(platform, recipient_id, documents):
    if platform == "telegram":
        for filename, content in documents:
            telegram_document(recipient_id, filename, content)
    elif platform == "vk":
        vk_send_documents(recipient_id, documents)
    else:
        raise RuntimeError("unknown platform")


def deliver_existing_meeting(meeting_id, platform, recipient_id):
    with db_connect() as db:
        completed = db.execute(
            "SELECT title, transcript, protocol FROM broadcasts WHERE meeting_id=?",
            (meeting_id,),
        ).fetchone()
    if not completed or not completed[1] or not claim_delivery(meeting_id, platform, recipient_id):
        return False
    try:
        send_document_payloads(platform, recipient_id, document_payloads(*completed))
        finish_delivery(meeting_id, platform, recipient_id, "sent")
        log.info("LATE_DELIVERY_FINISHED meeting=%s platform=%s recipient=%s", meeting_id, platform, recipient_id)
        return True
    except Exception as exc:
        error = str(exc)[:500]
        finish_delivery(meeting_id, platform, recipient_id, "failed", error)
        log.error("LATE_DELIVERY_FAILED meeting=%s platform=%s recipient=%s error=%s", meeting_id, platform, recipient_id, error)
        return False


def broadcast(meeting_id, title, transcript, summary):
    now = int(time.time())
    with db_connect() as db:
        existing = db.execute("SELECT 1 FROM broadcasts WHERE meeting_id=?", (meeting_id,)).fetchone()
        if existing:
            return {"duplicate": True, "sent": 0, "failed": 0}
        db.execute(
            "INSERT INTO broadcasts(meeting_id, title, transcript, protocol, created_at) VALUES (?, ?, ?, ?, ?)",
            (meeting_id, title, transcript, summary, now),
        )
        recipients = db.execute(
            """SELECT platform, recipient_id FROM subscribers WHERE active=1
               UNION
               SELECT platform, recipient_id FROM meeting_subscriptions WHERE meeting_id=? AND active=1
               ORDER BY platform, recipient_id""",
            (meeting_id,),
        ).fetchall()

    documents = document_payloads(title, transcript, summary)
    sent = failed = 0
    for platform, recipient_id in recipients:
        if not claim_delivery(meeting_id, platform, recipient_id):
            continue
        try:
            send_document_payloads(platform, recipient_id, documents)
            status, error = "sent", None
            sent += 1
        except Exception as exc:
            status, error = "failed", str(exc)[:500]
            failed += 1
            log.error("DELIVERY_FAILED meeting=%s platform=%s recipient=%s error=%s", meeting_id, platform, recipient_id, error)
        finish_delivery(meeting_id, platform, recipient_id, status, error)
    log.info("BROADCAST_FINISHED meeting=%s sent=%d failed=%d", meeting_id, sent, failed)
    return {"duplicate": False, "sent": sent, "failed": failed}


class Handler(BaseHTTPRequestHandler):
    server_version = "MeetingAssistant/1.0"

    def reply(self, status, body, content_type="text/plain; charset=utf-8"):
        if not isinstance(body, bytes):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/health":
            self.reply(200, json.dumps({"ok": True}), "application/json")
        elif path.startswith("/m/"):
            status, page = meeting_page(urllib.parse.unquote(path[3:]))
            self.reply(status, page, "text/html; charset=utf-8" if status == 200 else "text/plain; charset=utf-8")
        else:
            self.reply(404, "not found")

    def do_POST(self):
        if self.path == "/api/meeting/upload":
            supplied = self.headers.get("Authorization", "")
            expected = f"Bearer {MEETING_API_TOKEN}"
            if not MEETING_API_TOKEN or not secrets.compare_digest(supplied, expected):
                self.reply(403, "forbidden")
                return
            meeting_id = self.headers.get("X-Meeting-ID", "").strip()
            title = self.headers.get("X-Meeting-Title", "Протокол собрания").strip()
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", meeting_id):
                self.reply(400, "invalid meeting id")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_UPLOAD_BYTES:
                self.reply(413, "audio must be between 1 byte and 25 MB")
                return
            audio_path = os.path.join(INCOMING_DIR, f"{meeting_id}.ogg")
            metadata_path = os.path.join(INCOMING_DIR, f"{meeting_id}.json")
            temporary = audio_path + ".part"
            remaining = length
            with open(temporary, "wb") as output:
                while remaining:
                    block = self.rfile.read(min(1024 * 1024, remaining))
                    if not block:
                        break
                    output.write(block)
                    remaining -= len(block)
            if remaining:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                self.reply(400, "incomplete upload")
                return
            os.replace(temporary, audio_path)
            with open(metadata_path + ".part", "w", encoding="utf-8") as output:
                json.dump({"meeting_id": meeting_id, "title": title, "audio": audio_path}, output, ensure_ascii=False)
            os.replace(metadata_path + ".part", metadata_path)
            log.info("MEETING_QUEUED meeting=%s bytes=%d", meeting_id, length)
            self.reply(202, json.dumps({"ok": True, "queued": True, "meeting_id": meeting_id}), "application/json")
            return

        try:
            payload = self.read_json()
        except (ValueError, json.JSONDecodeError):
            self.reply(400, "bad request")
            return

        if self.path == "/telegram/webhook":
            supplied = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not TELEGRAM_WEBHOOK_SECRET or not secrets.compare_digest(supplied, TELEGRAM_WEBHOOK_SECRET):
                self.reply(403, "forbidden")
                return
            try:
                handle_telegram(payload)
            except Exception:
                log.exception("TELEGRAM_UPDATE_FAILED")
                self.reply(500, "failed")
                return
            self.reply(200, "ok")
            return

        if self.path == "/vk/callback":
            event_type = payload.get("type", "")
            if VK_GROUP_ID and str(payload.get("group_id", "")) != VK_GROUP_ID:
                self.reply(403, "forbidden")
                return
            if VK_SECRET and not secrets.compare_digest(str(payload.get("secret", "")), VK_SECRET):
                self.reply(403, "forbidden")
                return
            if event_type == "confirmation":
                self.reply(200, VK_CONFIRMATION_CODE)
                return
            try:
                handle_vk(payload)
            except Exception:
                log.exception("VK_UPDATE_FAILED type=%s", event_type)
            self.reply(200, "ok")
            return

        if self.path == "/api/meeting/protocol":
            supplied = self.headers.get("Authorization", "")
            expected = f"Bearer {MEETING_API_TOKEN}"
            if not MEETING_API_TOKEN or not secrets.compare_digest(supplied, expected):
                self.reply(403, "forbidden")
                return
            meeting_id = str(payload.get("meeting_id", "")).strip()
            title = str(payload.get("title", "Протокол собрания")).strip()
            transcript = str(payload.get("transcript", "")).strip()
            summary = str(payload.get("summary", "")).strip()
            if not meeting_id or not transcript or not summary or len(meeting_id) > 200:
                self.reply(400, "meeting_id, transcript and summary are required")
                return
            result = broadcast(meeting_id, title, transcript, summary)
            self.reply(200, json.dumps({"ok": True, **result}), "application/json")
            return

        self.reply(404, "not found")

    def log_message(self, _format, *_args):
        return


if __name__ == "__main__":
    init_db()
    log.info("MEETING_ASSISTANT_READY address=%s:%d vk_send=%s", HOST, PORT, str(bool(VK_GROUP_TOKEN)).lower())
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
