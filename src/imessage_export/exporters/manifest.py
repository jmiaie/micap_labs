"""Forensic manifest: the chain-of-custody record for an export.

The manifest ties the produced artifacts back to the source database with
SHA-256 hashes, records exactly how and when the extraction was performed, and
documents the method and its known limitations so the export can be defended
(or impeached) on its merits.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import time
from datetime import datetime, timezone

from ..models import Conversation
from ..version import TOOL_NAME, __version__

_METHOD = (
    "Messages were read directly from the Apple Messages SQLite database "
    "(chat.db / sms.db). The source file was opened strictly read-only "
    "(SQLite mode=ro, immutable=1) so its contents and hash were not altered. "
    "Timestamps were converted from Apple absolute time (epoch 2001-01-01 UTC) "
    "to UTC. Message text was taken from the 'text' column where present, "
    "otherwise decoded from the 'attributedBody' field; the source of each "
    "message's text is recorded per-message in the JSON export."
)

_LIMITATIONS = [
    "This tool performs logical extraction of message records; it is not a "
    "certified forensic acquisition suite and does not by itself establish "
    "how the source database was obtained.",
    "Deleted messages are not recovered unless still present in the database.",
    "The PDF exhibit uses base PDF fonts; characters outside the Latin/cp1252 "
    "range (most emoji, many non-Latin scripts) are shown as [U+XXXX] "
    "placeholders. The JSON and HTML exports preserve full original text.",
    "Attachment binaries are only hashed/copied when their files are present "
    "and resolvable on the machine running the export.",
]


def sha256_file(path: str, _chunk: int = 1 << 20) -> tuple[str, int]:
    """Return ``(hex_digest, size_bytes)`` for *path* without loading it whole."""
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            block = f.read(_chunk)
            if not block:
                break
            size += len(block)
            h.update(block)
    return h.hexdigest(), size


def _utc_iso(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)
    return dt.isoformat()


def build_manifest(
    *,
    source_path: str,
    source_sha256: str,
    source_size: int,
    db_summary: dict,
    conversation: Conversation,
    outputs: list[dict],
    operator: str | None,
    case_reference: str | None,
) -> dict:
    start, end = conversation.date_range
    src_stat = os.stat(source_path)
    return {
        "tool": {"name": TOOL_NAME, "version": __version__},
        "generated_at_utc": _utc_iso(),
        "operator": operator,
        "case_reference": case_reference,
        "method": _METHOD,
        "limitations": _LIMITATIONS,
        "source": {
            "path": source_path,
            "sha256": source_sha256,
            "size_bytes": source_size,
            "modified_utc": _utc_iso(src_stat.st_mtime),
        },
        "environment": {
            "tool_host": socket.gethostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "local_timezone": "/".join(t for t in time.tzname if t),
        },
        "database_summary": db_summary,
        "conversation": {
            "chat_rowid": conversation.chat_rowid,
            "guid": conversation.guid,
            "title": conversation.title,
            "is_group": conversation.is_group,
            "participants": [p.to_dict() for p in conversation.participants],
            "message_count": len(conversation.messages),
            "date_range_utc": {
                "start": start.astimezone(timezone.utc).isoformat() if start else None,
                "end": end.astimezone(timezone.utc).isoformat() if end else None,
            },
        },
        "outputs": outputs,
    }


def write_manifest(out_dir: str, manifest: dict) -> tuple[str, str]:
    json_path = os.path.join(out_dir, "manifest.json")
    txt_path = os.path.join(out_dir, "manifest.txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(_render_text(manifest))
    return json_path, txt_path


def _render_text(m: dict) -> str:
    L: list[str] = []
    L.append("=" * 72)
    L.append("MESSAGE CONVERSATION EXPORT - FORENSIC MANIFEST")
    L.append("=" * 72)
    L.append(f"Tool            : {m['tool']['name']} v{m['tool']['version']}")
    L.append(f"Generated (UTC) : {m['generated_at_utc']}")
    L.append(f"Operator        : {m.get('operator') or '(not provided)'}")
    L.append(f"Case reference  : {m.get('case_reference') or '(not provided)'}")
    L.append("")
    L.append("SOURCE DATABASE")
    L.append("-" * 72)
    src = m["source"]
    L.append(f"Path            : {src['path']}")
    L.append(f"SHA-256         : {src['sha256']}")
    L.append(f"Size (bytes)    : {src['size_bytes']}")
    L.append(f"Modified (UTC)  : {src['modified_utc']}")
    L.append("")
    L.append("CONVERSATION")
    L.append("-" * 72)
    c = m["conversation"]
    L.append(f"Title           : {c['title']}")
    L.append(f"Group chat      : {c['is_group']}")
    L.append(f"Participants    : {', '.join(p['handle'] for p in c['participants']) or '(none)'}")
    L.append(f"Messages        : {c['message_count']}")
    L.append(f"Date range (UTC): {c['date_range_utc']['start']} .. {c['date_range_utc']['end']}")
    L.append("")
    L.append("OUTPUT FILES (SHA-256)")
    L.append("-" * 72)
    for o in m["outputs"]:
        L.append(f"{o['sha256']}  {o['name']}  ({o['size_bytes']} bytes)")
    L.append("")
    L.append("ENVIRONMENT")
    L.append("-" * 72)
    env = m["environment"]
    L.append(f"Tool host       : {env['tool_host']}")
    L.append(f"Platform        : {env['platform']}")
    L.append(f"Python          : {env['python']}")
    L.append("")
    L.append("METHOD")
    L.append("-" * 72)
    L.append(_wrap_para(m["method"]))
    L.append("")
    L.append("LIMITATIONS")
    L.append("-" * 72)
    for item in m["limitations"]:
        L.append("- " + _wrap_para(item, hang=2))
    L.append("")
    L.append("=" * 72)
    L.append(_wrap_para(
        "Integrity note: re-running this tool against the source database "
        "above should reproduce identical JSON output. Verify each output file "
        "by recomputing its SHA-256 (e.g. `shasum -a 256 <file>`)."
    ))
    L.append("=" * 72)
    return "\n".join(L) + "\n"


def _wrap_para(text: str, width: int = 72, hang: int = 0) -> str:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = w if not cur else cur + " " + w
        if len(cand) > width:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    pad = " " * hang
    return ("\n" + pad).join(lines)
