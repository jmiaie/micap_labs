"""Build a synthetic Messages database that mirrors Apple's schema.

Used by the test-suite so the reader and exporters can be exercised without a
real device. The blob built by :func:`make_attributed_body` matches the
``streamtyped`` layout that :mod:`imessage_export.attributed_body` parses.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone

# Make ``src/`` importable when running tests directly.
_SRC = os.path.join(os.path.dirname(__file__), os.pardir, "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def apple_ns(dt: datetime) -> int:
    return int((dt - APPLE_EPOCH).total_seconds() * 1_000_000_000)


def make_attributed_body(text: str) -> bytes:
    body = bytearray()
    body += b"\x04\x0bstreamtyped"
    body += b"\x81\xe8\x03\x84\x01@\x84\x84\x84"
    body += b"NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84"
    body += b"NSString\x01\x94\x84\x01+"
    data = text.encode("utf-8")
    n = len(data)
    if n < 128:
        body += bytes([n])
    else:
        body += b"\x81" + n.to_bytes(2, "little")
    body += data
    body += b"\x86\x84\x12NSDictionary"  # trailing attribute-run junk
    return bytes(body)


_SCHEMA = """
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT,
                   display_name TEXT, service_name TEXT, style INTEGER,
                   room_name TEXT);
CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT,
                      attributedBody BLOB, handle_id INTEGER, service TEXT,
                      date INTEGER, date_read INTEGER, date_delivered INTEGER,
                      is_from_me INTEGER);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, filename TEXT,
                         mime_type TEXT, transfer_name TEXT, total_bytes INTEGER);
CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
"""

# Text recovered from the attributedBody blob (includes an emoji on purpose, to
# prove Unicode survives in JSON/HTML and is placeholdered in the PDF).
ATTRIBUTED_TEXT = "Sent from attributedBody ✅"


def build_fixture(path: str) -> dict:
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.execute("PRAGMA user_version = 7")

        conn.executemany(
            "INSERT INTO handle (ROWID, id, service) VALUES (?,?,?)",
            [(1, "+15551234567", "iMessage"), (2, "+15557654321", "SMS")],
        )
        conn.executemany(
            "INSERT INTO chat (ROWID, guid, chat_identifier, display_name, "
            "service_name, style, room_name) VALUES (?,?,?,?,?,?,?)",
            [
                (1, "iMessage;-;+15551234567", "+15551234567", None,
                 "iMessage", 45, None),
                (2, "iMessage;+;chat99", "chat99", "Weekend Plans",
                 "iMessage", 43, "chat99"),
            ],
        )
        conn.executemany(
            "INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?,?)",
            [(1, 1), (2, 1), (2, 2)],
        )

        d1 = apple_ns(datetime(2024, 1, 15, 9, 30, 0, tzinfo=timezone.utc))
        d2 = apple_ns(datetime(2024, 1, 15, 9, 31, 0, tzinfo=timezone.utc))
        d3 = apple_ns(datetime(2024, 1, 15, 9, 32, 0, tzinfo=timezone.utc))
        d4 = apple_ns(datetime(2024, 1, 16, 18, 0, 0, tzinfo=timezone.utc))
        d5 = apple_ns(datetime(2024, 1, 17, 12, 0, 0, tzinfo=timezone.utc))

        msgs = [
            # rowid, guid, text, blob, handle, service, date, read, delivered, from_me
            (1, "g1", "Hey, are we still on for tomorrow?", None, 1,
             "iMessage", d1, 0, 0, 0),
            (2, "g2", "Yes! See you at noon.", None, 0,
             "iMessage", d2, d3, d2, 1),
            (3, "g3", None, make_attributed_body(ATTRIBUTED_TEXT), 1,
             "iMessage", d3, 0, 0, 0),
            (4, "g4", "Here is the document.", None, 0,
             "SMS", d4, 0, 0, 1),
            (5, "g5", "Anyone free Saturday?", None, 2,
             "iMessage", d5, 0, 0, 0),
        ]
        conn.executemany(
            "INSERT INTO message (ROWID, guid, text, attributedBody, handle_id, "
            "service, date, date_read, date_delivered, is_from_me) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            msgs,
        )
        conn.executemany(
            "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?,?)",
            [(1, 1), (1, 2), (1, 3), (1, 4), (2, 5)],
        )
        conn.execute(
            "INSERT INTO attachment (ROWID, filename, mime_type, transfer_name, "
            "total_bytes) VALUES (?,?,?,?,?)",
            (1, "~/Library/Messages/Attachments/aa/bb/doc.pdf",
             "application/pdf", "doc.pdf", 12345),
        )
        conn.execute(
            "INSERT INTO message_attachment_join (message_id, attachment_id) "
            "VALUES (?,?)",
            (4, 1),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "chat1_rowid": 1,
        "chat2_rowid": 2,
        "attributed_text": ATTRIBUTED_TEXT,
        "chat1_message_count": 4,
    }
