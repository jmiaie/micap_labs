import os
import tempfile
import unittest
from datetime import datetime, timezone

import fixture_db  # noqa: E402  (sets sys.path to src)
from fixture_db import build_fixture

from imessage_export.chatdb import ChatDB


class ChatDBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmp, "chat.db")
        cls.info = build_fixture(cls.db_path)

    def test_summary(self):
        with ChatDB(self.db_path) as db:
            s = db.summary()
        self.assertEqual(s["total_messages"], 5)
        self.assertEqual(s["total_chats"], 2)
        self.assertEqual(s["schema_user_version"], 7)

    def test_list_conversations(self):
        with ChatDB(self.db_path) as db:
            convos = {c["chat_rowid"]: c for c in db.list_conversations()}
        self.assertEqual(convos[1]["message_count"], 4)
        self.assertEqual(convos[2]["message_count"], 1)
        self.assertEqual(convos[2]["display_name"], "Weekend Plans")
        self.assertIn("+15551234567", convos[1]["participants"])

    def test_find_by_contact(self):
        with ChatDB(self.db_path) as db:
            self.assertEqual(db.find_chats_by_contact("+15557654321"), [2])
            self.assertIn(1, db.find_chats_by_contact("5551234567"))

    def test_load_conversation_text_and_metadata(self):
        with ChatDB(self.db_path) as db:
            conv = db.load_conversation(1)
        self.assertFalse(conv.is_group)
        self.assertEqual(len(conv.messages), 4)

        m1, m2, m3, m4 = conv.messages
        self.assertEqual(m1.text, "Hey, are we still on for tomorrow?")
        self.assertEqual(m1.text_source, "text")
        self.assertFalse(m1.is_from_me)
        self.assertEqual(m1.sender, "+15551234567")
        self.assertTrue(m1.is_imessage)

        self.assertTrue(m2.is_from_me)
        self.assertEqual(m2.sender, "Me")
        self.assertIsNotNone(m2.date_read)

        # text came from attributedBody, with Unicode intact
        self.assertEqual(m3.text, self.info["attributed_text"])
        self.assertEqual(m3.text_source, "attributedBody")

        # SMS message with an attachment
        self.assertFalse(m4.is_imessage)
        self.assertEqual(len(m4.attachments), 1)
        self.assertEqual(m4.attachments[0].mime_type, "application/pdf")

    def test_date_conversion(self):
        with ChatDB(self.db_path) as db:
            conv = db.load_conversation(1)
        expected = datetime(2024, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
        delta = abs((conv.messages[0].date - expected).total_seconds())
        self.assertLess(delta, 1.0)

    def test_group_chat_detected(self):
        with ChatDB(self.db_path) as db:
            conv = db.load_conversation(2)
        self.assertTrue(conv.is_group)
        self.assertEqual(len(conv.participants), 2)

    def test_source_not_mutated(self):
        before = os.path.getsize(self.db_path)
        with ChatDB(self.db_path) as db:
            db.load_conversation(1)
        self.assertEqual(os.path.getsize(self.db_path), before)
        # immutable=1 must not create side files
        self.assertFalse(os.path.exists(self.db_path + "-wal"))
        self.assertFalse(os.path.exists(self.db_path + "-shm"))


if __name__ == "__main__":
    unittest.main()
