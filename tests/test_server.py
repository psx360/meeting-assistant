import importlib.util
import gc
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(
            os.environ,
            {
                "MEETING_API_TOKEN": "test-secret",
                "MEETING_DB_PATH": str(Path(self.temporary.name) / "meetings.sqlite3"),
                "MEETING_INCOMING_DIR": str(Path(self.temporary.name) / "incoming"),
            },
            clear=False,
        )
        self.environment.start()
        spec = importlib.util.spec_from_file_location("meeting_assistant_server_test", ROOT / "meeting-assistant-server.py")
        self.server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.server)
        self.server.init_db()

    def tearDown(self):
        self.environment.stop()
        self.server = None
        gc.collect()
        self.temporary.cleanup()

    def test_signed_meeting_payload_rejects_tampering(self):
        payload = self.server.join_payload("2026-09-03_12-30-00")
        self.assertEqual(self.server.meeting_id_from_payload(payload), "2026-09-03_12-30-00")
        self.assertEqual(self.server.meeting_id_from_payload(payload[:-1] + "x"), "")
        self.assertLessEqual(len(payload), 64)

    def test_signature_may_contain_underscore(self):
        for suffix in range(1000):
            meeting_id = f"2026-09-03_13-55-{suffix}"
            payload = self.server.join_payload(meeting_id)
            if "_" in payload[-self.server.JOIN_SIGNATURE_CHARS:]:
                break
        else:
            self.fail("test setup did not produce an underscore in a base64url signature")
        self.assertEqual(self.server.meeting_id_from_payload(payload), meeting_id)

    def test_existing_database_gets_transcript_column(self):
        with self.server.db_connect() as db:
            db.execute("DROP TABLE broadcasts")
            db.execute(
                """CREATE TABLE broadcasts (
                    meeting_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    protocol TEXT NOT NULL, created_at INTEGER NOT NULL
                )"""
            )
        self.server.init_db()
        with self.server.db_connect() as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(broadcasts)")}
        self.assertIn("transcript", columns)

    def test_qr_telegram_start_creates_only_scoped_subscription(self):
        meeting_id = "2026-09-03_12-30-00"
        update = {
            "message": {
                "chat": {"id": 42},
                "text": f"/start {self.server.join_payload(meeting_id)}",
            }
        }
        with mock.patch.object(self.server, "telegram_send"):
            self.server.handle_telegram(update)
        with self.server.db_connect() as db:
            scoped = db.execute(
                "SELECT active FROM meeting_subscriptions WHERE meeting_id=? AND platform='telegram' AND recipient_id='42'",
                (meeting_id,),
            ).fetchone()
            global_subscription = db.execute(
                "SELECT active FROM subscribers WHERE platform='telegram' AND recipient_id='42'"
            ).fetchone()
        self.assertEqual(scoped, (1,))
        self.assertIsNone(global_subscription)

    def test_meeting_page_contains_scoped_bot_links(self):
        payload = self.server.join_payload("2026-09-03_12-30-00")
        self.server.VK_GROUP_ID = "12345"
        with mock.patch.object(self.server, "telegram_bot_username", return_value="meeting_test_bot"):
            status, page = self.server.meeting_page(payload)
        self.assertEqual(status, 200)
        self.assertIn(f"https://t.me/meeting_test_bot?start={payload}", page)
        self.assertIn("https://vk.me/club12345?", page)
        self.assertIn(f"ref={payload}", page)

    def test_existing_global_subscription_is_not_changed(self):
        self.server.set_subscription("telegram", "owner", True)
        self.server.set_meeting_subscription("one-meeting", "telegram", "guest")
        with self.server.db_connect() as db:
            owner = db.execute(
                "SELECT active FROM subscribers WHERE platform='telegram' AND recipient_id='owner'"
            ).fetchone()
        self.assertEqual(owner, (1,))

    def test_vk_document_message_has_attachments_and_no_text(self):
        with mock.patch.object(
            self.server, "vk_document_attachment", side_effect=["doc-1_1", "doc-1_2"]
        ), mock.patch.object(self.server, "vk") as vk_method:
            self.server.vk_send_documents("55", [("one.docx", b"one"), ("two.docx", b"two")])
        method, fields = vk_method.call_args.args
        self.assertEqual(method, "messages.send")
        self.assertEqual(fields["attachment"], "doc-1_1,doc-1_2")
        self.assertNotIn("message", fields)

    def test_vk_document_upload_retries_missing_file_token(self):
        def vk_method(method, _fields):
            if method == "docs.getMessagesUploadServer":
                return {"response": {"upload_url": "https://upload.test"}}
            if method == "docs.save":
                return {"response": {"doc": {"owner_id": -1, "id": 2}}}
            self.fail(f"unexpected VK method: {method}")

        with mock.patch.object(self.server, "vk", side_effect=vk_method), mock.patch.object(
            self.server, "post_multipart", side_effect=[{}, {"file": "token"}]
        ) as upload, mock.patch.object(self.server.time, "sleep"):
            attachment = self.server.vk_document_attachment("55", "protocol.docx", b"docx")
        self.assertEqual(attachment, "doc-1_2")
        self.assertEqual(upload.call_count, 2)

    def test_any_vk_text_enables_global_subscription(self):
        self.server.set_subscription("vk", "55", False)
        event = {
            "type": "message_new",
            "object": {"message": {"peer_id": 55, "text": "Любой текст"}},
        }
        with mock.patch.object(self.server, "vk_send") as send:
            self.server.handle_vk(event)
        with self.server.db_connect() as db:
            subscription = db.execute(
                "SELECT active FROM subscribers WHERE platform='vk' AND recipient_id='55'"
            ).fetchone()
        self.assertEqual(subscription, (1,))
        send.assert_called_once_with(55, "Вы подписаны на протоколы собраний.")

    def test_vk_unsubscribe_word_no_longer_disables_subscription(self):
        event = {
            "type": "message_new",
            "object": {"message": {"peer_id": 55, "text": "Отписаться"}},
        }
        with mock.patch.object(self.server, "vk_send"):
            self.server.handle_vk(event)
        with self.server.db_connect() as db:
            subscription = db.execute(
                "SELECT active FROM subscribers WHERE platform='vk' AND recipient_id='55'"
            ).fetchone()
        self.assertEqual(subscription, (1,))

    def test_telegram_broadcast_sends_only_two_documents(self):
        self.server.set_subscription("telegram", "owner", True)
        with mock.patch.object(self.server, "telegram_document") as send_document, mock.patch.object(
            self.server, "telegram_send"
        ) as send_text:
            result = self.server.broadcast("meeting-documents-only", "Собрание", "Транскрибация", "Протокол")
        self.assertEqual(result["sent"], 1)
        self.assertEqual(send_document.call_count, 2)
        send_text.assert_not_called()

    def test_subscription_after_broadcast_delivers_once(self):
        self.server.broadcast("late-meeting", "Собрание", "Транскрибация", "Протокол")
        with mock.patch.object(self.server, "telegram_document") as send_document:
            self.server.set_meeting_subscription("late-meeting", "telegram", "guest")
            self.server.set_meeting_subscription("late-meeting", "telegram", "guest")
        self.assertEqual(send_document.call_count, 2)
        with self.server.db_connect() as db:
            status = db.execute(
                "SELECT status FROM deliveries WHERE meeting_id='late-meeting' AND platform='telegram' AND recipient_id='guest'"
            ).fetchone()
        self.assertEqual(status, ("sent",))


if __name__ == "__main__":
    unittest.main()
