#!/usr/bin/python3
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
VK_API_VERSION = "5.199"

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
            """
        )


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


def subscription_command(text):
    command = text.strip().lower().split(maxsplit=1)[0] if text.strip() else ""
    if "@" in command:
        command = command.split("@", 1)[0]
    if command in {"/start", "/subscribe", "подписаться", "старт", "start", "subscribe"}:
        return True
    if command in {"/stop", "/unsubscribe", "отписаться", "стоп", "stop", "unsubscribe"}:
        return False
    return None


def handle_telegram(update):
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    if not chat_id:
        return
    command = subscription_command(message.get("text", ""))
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


def broadcast(meeting_id, title, transcript, summary):
    now = int(time.time())
    with db_connect() as db:
        existing = db.execute("SELECT 1 FROM broadcasts WHERE meeting_id=?", (meeting_id,)).fetchone()
        if existing:
            return {"duplicate": True, "sent": 0, "failed": 0}
        db.execute(
            "INSERT INTO broadcasts(meeting_id, title, protocol, created_at) VALUES (?, ?, ?, ?)",
            (meeting_id, title, summary, now),
        )
        recipients = db.execute(
            "SELECT platform, recipient_id FROM subscribers WHERE active=1 ORDER BY platform, recipient_id"
        ).fetchall()

    stem = safe_filename(title)
    transcript_docx = meeting_documents.build_docx(f"{title} — транскрибация", transcript)
    summary_docx = meeting_documents.build_docx(f"{title} — протокол и сводка", summary, structured=True)
    sent = failed = 0
    for platform, recipient_id in recipients:
        try:
            if platform == "telegram":
                telegram_document(recipient_id, f"{stem} - транскрибация.docx", transcript_docx)
                telegram_document(recipient_id, f"{stem} - протокол и сводка.docx", summary_docx)
                telegram_send(recipient_id, f"{title}\n\n{transcript}".strip())
            elif platform == "vk":
                vk_send(recipient_id, f"{title}\n\n{summary}\n\nПОЛНАЯ ТРАНСКРИБАЦИЯ\n\n{transcript}".strip())
            else:
                raise RuntimeError("unknown platform")
            status, error = "sent", None
            sent += 1
        except Exception as exc:
            status, error = "failed", str(exc)[:500]
            failed += 1
            log.error("DELIVERY_FAILED meeting=%s platform=%s recipient=%s error=%s", meeting_id, platform, recipient_id, error)
        with db_connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO deliveries(meeting_id, platform, recipient_id, status, error, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (meeting_id, platform, recipient_id, status, error, int(time.time())),
            )
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
        if self.path == "/health":
            self.reply(200, json.dumps({"ok": True}), "application/json")
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
