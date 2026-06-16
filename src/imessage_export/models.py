"""Plain data structures describing an exported conversation.

These are intentionally decoupled from the SQLite schema so that the reader
(:mod:`imessage_export.chatdb`) and the exporters never share Apple-specific
column names. Everything downstream operates on these dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


# Apple stores timestamps as an offset from this epoch ("Mac absolute time").
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


@dataclass
class Attachment:
    """A file transferred within a message (photo, video, audio, document)."""

    rowid: int
    filename: str | None
    mime_type: str | None
    transfer_name: str | None
    total_bytes: int | None
    # Populated only when the underlying file could be located on disk.
    resolved_path: str | None = None
    sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Participant:
    """One handle (phone number or email) taking part in a conversation."""

    rowid: int
    handle: str  # e.g. "+15551234567" or "person@example.com"
    service: str | None  # "iMessage", "SMS", ...

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Message:
    """A single message, normalised from the Apple ``message`` row."""

    rowid: int
    guid: str | None
    # Timestamps are timezone-aware UTC datetimes (or None when unknown / 0).
    date: datetime | None
    date_read: datetime | None
    date_delivered: datetime | None
    is_from_me: bool
    sender: str  # handle of who sent it, or "Me"
    service: str | None  # "iMessage" -> blue, "SMS"/"RCS" -> green
    text: str
    # Where ``text`` came from: the plain ``text`` column or a decoded
    # ``attributedBody`` blob. Recorded so an examiner can see the provenance.
    text_source: str
    # If attributedBody decoding failed, the raw blob is preserved (base64) so
    # that no data is silently lost.
    attributed_body_b64: str | None = None
    attachments: list[Attachment] = field(default_factory=list)

    @property
    def is_imessage(self) -> bool:
        return (self.service or "").lower() == "imessage"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rowid": self.rowid,
            "guid": self.guid,
            "date_utc": _iso(self.date),
            "date_read_utc": _iso(self.date_read),
            "date_delivered_utc": _iso(self.date_delivered),
            "is_from_me": self.is_from_me,
            "sender": self.sender,
            "service": self.service,
            "is_imessage": self.is_imessage,
            "text": self.text,
            "text_source": self.text_source,
            "attributed_body_b64": self.attributed_body_b64,
            "attachments": [a.to_dict() for a in self.attachments],
        }


@dataclass
class Conversation:
    """A chat (1:1 or group) together with its participants and messages."""

    chat_rowid: int
    guid: str | None
    chat_identifier: str | None
    display_name: str | None
    service: str | None
    is_group: bool
    participants: list[Participant] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)

    @property
    def title(self) -> str:
        if self.display_name:
            return self.display_name
        if len(self.participants) == 1:
            return self.participants[0].handle
        if self.participants:
            return ", ".join(p.handle for p in self.participants)
        return self.chat_identifier or f"chat {self.chat_rowid}"

    @property
    def date_range(self) -> tuple[datetime | None, datetime | None]:
        dated = [m.date for m in self.messages if m.date]
        if not dated:
            return (None, None)
        return (min(dated), max(dated))

    def to_dict(self) -> dict[str, Any]:
        start, end = self.date_range
        return {
            "chat_rowid": self.chat_rowid,
            "guid": self.guid,
            "chat_identifier": self.chat_identifier,
            "display_name": self.display_name,
            "service": self.service,
            "is_group": self.is_group,
            "title": self.title,
            "participants": [p.to_dict() for p in self.participants],
            "message_count": len(self.messages),
            "date_range_utc": {"start": _iso(start), "end": _iso(end)},
            "messages": [m.to_dict() for m in self.messages],
        }


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()
