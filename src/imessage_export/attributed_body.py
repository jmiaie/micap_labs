"""Extract message text from Apple's ``attributedBody`` blob.

Modern macOS / iOS builds frequently leave the ``message.text`` column NULL and
instead store the body inside ``message.attributedBody`` — a serialized
``NSAttributedString`` in the legacy NeXT/Apple *typedstream* ("streamtyped")
format. This module recovers the plain string.

The typedstream format is not a documented public format, so this uses the
well-established community heuristic: locate the ``NSString`` (or
``NSMutableString``) class marker, then read the inline C-string that follows
the ``+`` type tag using typedstream variable-length integer decoding.

Recovery is best-effort by nature. Callers should treat a ``None`` result as
"could not decode" and preserve the raw blob so that nothing is lost.
"""

from __future__ import annotations

STREAMTYPED_HEADER = b"\x04\x0bstreamtyped"
_CLASS_MARKERS = (b"NSMutableString", b"NSString")
_PLUS = 0x2B  # typedstream tag introducing an inline byte string


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read a typedstream length integer.

    A single signed byte holds values up to 127. The escape byte 0x81 means a
    little-endian 2-byte short follows; 0x82 means a 4-byte int follows.
    Returns ``(value, new_pos)``.
    """
    b = data[pos]
    if b == 0x81:
        return int.from_bytes(data[pos + 1 : pos + 3], "little"), pos + 3
    if b == 0x82:
        return int.from_bytes(data[pos + 1 : pos + 5], "little"), pos + 5
    return b, pos + 1


def _looks_like_text(s: str) -> bool:
    """Reject obviously-wrong decodes (mostly control characters)."""
    if s == "":
        return True
    control = sum(1 for ch in s if ord(ch) < 0x20 and ch not in "\n\r\t")
    return control / len(s) < 0.3


def decode_attributed_body(blob: bytes | None) -> str | None:
    """Return the message text contained in *blob*, or ``None`` on failure."""
    if not blob:
        return None

    # Find the earliest string-class marker.
    best_idx = -1
    best_marker = b""
    for marker in _CLASS_MARKERS:
        idx = blob.find(marker)
        if idx != -1 and (best_idx == -1 or idx < best_idx):
            best_idx = idx
            best_marker = marker
    if best_idx == -1:
        return None

    cursor = best_idx + len(best_marker)
    # The next `+` byte introduces the inline string payload.
    plus = blob.find(bytes([_PLUS]), cursor)
    if plus == -1:
        return None

    try:
        length, start = _read_varint(blob, plus + 1)
    except IndexError:
        return None
    if length < 0 or start + length > len(blob):
        return None

    text = blob[start : start + length].decode("utf-8", "replace")
    if not _looks_like_text(text):
        return None
    return text
