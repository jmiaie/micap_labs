"""JSON export — the structured data of record.

This is the highest-fidelity output: full Unicode text, every timestamp in ISO
UTC, per-message provenance (``text_source``), and any undecodable
``attributedBody`` preserved as base64. It is what should be hashed and treated
as authoritative; the HTML and PDF are human-readable renderings of it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..models import Conversation
from ..version import TOOL_NAME, __version__


def write_json(path: str, conversation: Conversation, *,
               source_sha256: str, operator: str | None,
               case_reference: str | None) -> None:
    payload = {
        "export_metadata": {
            "tool": TOOL_NAME,
            "version": __version__,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_sha256": source_sha256,
            "operator": operator,
            "case_reference": case_reference,
        },
        "conversation": conversation.to_dict(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
