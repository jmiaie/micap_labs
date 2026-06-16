"""Read-only reader for Apple's Messages database (``chat.db`` / ``sms.db``).

Design goals:

* **Never mutate the source.** The database is opened with SQLite's ``mode=ro``
  and ``immutable=1`` flags, which also prevents creation of ``-wal``/``-shm``
  side files. This matters for evidentiary integrity: the hash of the file must
  not change because we read it.
* **Tolerate schema drift.** Column and table names have shifted across iOS and
  macOS releases. Every query is built from the columns that actually exist.
"""

from __future__ import annotations

import base64
import os
import sqlite3
from datetime import timedelta

from .attributed_body import decode_attributed_body
from .models import (
    APPLE_EPOCH,
    Attachment,
    Conversation,
    Message,
    Participant,
)

# Newer databases store dates in nanoseconds since the Apple epoch; older ones
# use whole seconds. Anything past this threshold is treated as nanoseconds.
_NS_THRESHOLD = 10**12


class ChatDBError(RuntimeError):
    pass


class ChatDB:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        if not os.path.exists(self.path):
            raise ChatDBError(f"database not found: {self.path}")
        self._conn: sqlite3.Connection | None = None

    # -- connection ---------------------------------------------------------
    def __enter__(self) -> "ChatDB":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            uri = f"file:{self.path}?mode=ro&immutable=1"
            self._conn = sqlite3.connect(uri, uri=True)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- schema helpers -----------------------------------------------------
    def _tables(self) -> set[str]:
        cur = self.connect().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        return {r[0] for r in cur.fetchall()}

    def _columns(self, table: str) -> set[str]:
        cur = self.connect().execute(f"PRAGMA table_info({table})")
        return {r[1] for r in cur.fetchall()}

    def _require_messages_schema(self) -> None:
        needed = {"message", "chat", "chat_message_join", "handle"}
        missing = needed - self._tables()
        if missing:
            raise ChatDBError(
                "this does not look like a Messages database; missing tables: "
                + ", ".join(sorted(missing))
            )

    # -- date conversion ----------------------------------------------------
    @staticmethod
    def _to_datetime(value):
        if not value:  # None or 0 -> unknown
            return None
        seconds = value / 1_000_000_000 if abs(value) > _NS_THRESHOLD else value
        return APPLE_EPOCH + timedelta(seconds=seconds)

    # -- public API ---------------------------------------------------------
    def summary(self) -> dict:
        """High-level counts and schema markers for the forensic manifest."""
        self._require_messages_schema()
        conn = self.connect()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        n_messages = conn.execute("SELECT COUNT(*) FROM message").fetchone()[0]
        n_chats = conn.execute("SELECT COUNT(*) FROM chat").fetchone()[0]
        n_handles = conn.execute("SELECT COUNT(*) FROM handle").fetchone()[0]
        return {
            "schema_user_version": version,
            "tables": sorted(self._tables()),
            "total_messages": n_messages,
            "total_chats": n_chats,
            "total_handles": n_handles,
        }

    def list_conversations(self) -> list[dict]:
        """Lightweight listing used by the ``list`` command."""
        self._require_messages_schema()
        conn = self.connect()
        chat_cols = self._columns("chat")
        display = "display_name" if "display_name" in chat_cols else "NULL"
        ident = "chat_identifier" if "chat_identifier" in chat_cols else "NULL"
        rows = conn.execute(
            f"""
            SELECT c.ROWID AS rowid,
                   {ident} AS chat_identifier,
                   {display} AS display_name,
                   COUNT(cmj.message_id) AS message_count,
                   MIN(m.date) AS first_date,
                   MAX(m.date) AS last_date
            FROM chat c
            LEFT JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
            LEFT JOIN message m ON m.ROWID = cmj.message_id
            GROUP BY c.ROWID
            ORDER BY last_date DESC
            """
        ).fetchall()
        out = []
        for r in rows:
            participants = self._participants(r["rowid"])
            out.append(
                {
                    "chat_rowid": r["rowid"],
                    "chat_identifier": r["chat_identifier"],
                    "display_name": r["display_name"],
                    "participants": [p.handle for p in participants],
                    "message_count": r["message_count"] or 0,
                    "first_date_utc": _iso(self._to_datetime(r["first_date"])),
                    "last_date_utc": _iso(self._to_datetime(r["last_date"])),
                }
            )
        return out

    def find_chats_by_contact(self, needle: str) -> list[int]:
        """Return chat ROWIDs that include a handle containing *needle*."""
        digits = "".join(ch for ch in needle if ch.isdigit())
        matches: list[int] = []
        for chat in self.list_conversations():
            for handle in chat["participants"]:
                handle_digits = "".join(ch for ch in handle if ch.isdigit())
                if needle.lower() in handle.lower() or (
                    digits and len(digits) >= 5 and digits in handle_digits
                ):
                    matches.append(chat["chat_rowid"])
                    break
        return matches

    def _participants(self, chat_rowid: int) -> list[Participant]:
        conn = self.connect()
        if "chat_handle_join" not in self._tables():
            return []
        handle_cols = self._columns("handle")
        service = "h.service" if "service" in handle_cols else "NULL"
        rows = conn.execute(
            f"""
            SELECT h.ROWID AS rowid, h.id AS handle, {service} AS service
            FROM chat_handle_join chj
            JOIN handle h ON h.ROWID = chj.handle_id
            WHERE chj.chat_id = ?
            ORDER BY h.id
            """,
            (chat_rowid,),
        ).fetchall()
        return [
            Participant(rowid=r["rowid"], handle=r["handle"], service=r["service"])
            for r in rows
        ]

    def _attachments(self, message_rowid: int) -> list[Attachment]:
        conn = self.connect()
        tables = self._tables()
        if "message_attachment_join" not in tables or "attachment" not in tables:
            return []
        cols = self._columns("attachment")

        def col(name: str) -> str:
            return f"a.{name}" if name in cols else "NULL"

        rows = conn.execute(
            f"""
            SELECT a.ROWID AS rowid,
                   {col('filename')} AS filename,
                   {col('mime_type')} AS mime_type,
                   {col('transfer_name')} AS transfer_name,
                   {col('total_bytes')} AS total_bytes
            FROM message_attachment_join maj
            JOIN attachment a ON a.ROWID = maj.attachment_id
            WHERE maj.message_id = ?
            ORDER BY a.ROWID
            """,
            (message_rowid,),
        ).fetchall()
        result = []
        for r in rows:
            resolved = None
            if r["filename"]:
                expanded = os.path.expanduser(r["filename"])
                if os.path.exists(expanded):
                    resolved = expanded
            result.append(
                Attachment(
                    rowid=r["rowid"],
                    filename=r["filename"],
                    mime_type=r["mime_type"],
                    transfer_name=r["transfer_name"],
                    total_bytes=r["total_bytes"],
                    resolved_path=resolved,
                )
            )
        return result

    def load_conversation(self, chat_rowid: int) -> Conversation:
        self._require_messages_schema()
        conn = self.connect()
        chat_cols = self._columns("chat")

        def ccol(name: str) -> str:
            return name if name in chat_cols else "NULL"

        chat_row = conn.execute(
            f"""
            SELECT ROWID AS rowid,
                   {ccol('guid')} AS guid,
                   {ccol('chat_identifier')} AS chat_identifier,
                   {ccol('display_name')} AS display_name,
                   {ccol('service_name')} AS service_name,
                   {ccol('style')} AS style,
                   {ccol('room_name')} AS room_name
            FROM chat WHERE ROWID = ?
            """,
            (chat_rowid,),
        ).fetchone()
        if chat_row is None:
            raise ChatDBError(f"no chat with ROWID {chat_rowid}")

        participants = self._participants(chat_rowid)
        is_group = bool(
            (chat_row["style"] == 43)
            or chat_row["room_name"]
            or len(participants) > 1
        )

        msg_cols = self._columns("message")

        def mcol(name: str) -> str:
            return f"m.{name}" if name in msg_cols else "NULL"

        rows = conn.execute(
            f"""
            SELECT m.ROWID AS rowid,
                   {mcol('guid')} AS guid,
                   {mcol('text')} AS text,
                   {mcol('attributedBody')} AS attributed_body,
                   {mcol('service')} AS service,
                   {mcol('date')} AS date,
                   {mcol('date_read')} AS date_read,
                   {mcol('date_delivered')} AS date_delivered,
                   {mcol('is_from_me')} AS is_from_me,
                   h.id AS handle
            FROM chat_message_join cmj
            JOIN message m ON m.ROWID = cmj.message_id
            LEFT JOIN handle h ON h.ROWID = m.handle_id
            WHERE cmj.chat_id = ?
            ORDER BY m.date ASC, m.ROWID ASC
            """,
            (chat_rowid,),
        ).fetchall()

        messages = [self._build_message(r) for r in rows]

        return Conversation(
            chat_rowid=chat_row["rowid"],
            guid=chat_row["guid"],
            chat_identifier=chat_row["chat_identifier"],
            display_name=chat_row["display_name"],
            service=chat_row["service_name"],
            is_group=is_group,
            participants=participants,
            messages=messages,
        )

    def _build_message(self, r: sqlite3.Row) -> Message:
        is_from_me = bool(r["is_from_me"])
        sender = "Me" if is_from_me else (r["handle"] or "Unknown")

        text = r["text"]
        attributed_b64 = None
        if text:
            text_source = "text"
        else:
            blob = r["attributed_body"]
            decoded = decode_attributed_body(blob)
            if decoded is not None:
                text = decoded
                text_source = "attributedBody"
            elif blob:
                text = ""
                text_source = "attributedBody(undecoded)"
                attributed_b64 = base64.b64encode(blob).decode("ascii")
            else:
                text = ""
                text_source = "none"

        return Message(
            rowid=r["rowid"],
            guid=r["guid"],
            date=self._to_datetime(r["date"]),
            date_read=self._to_datetime(r["date_read"]),
            date_delivered=self._to_datetime(r["date_delivered"]),
            is_from_me=is_from_me,
            sender=sender,
            service=r["service"],
            text=text or "",
            text_source=text_source,
            attributed_body_b64=attributed_b64,
            attachments=self._attachments(r["rowid"]),
        )


def _iso(dt):
    return dt.isoformat() if dt else None
