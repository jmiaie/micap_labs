"""Orchestrates a full export: read -> render -> manifest, in that order.

The source database hash is computed first (before anything else touches the
file), then the read-only reader produces a Conversation, then each artifact is
written and hashed, and finally the manifest records every hash. This ordering
is what makes the manifest a complete chain-of-custody record.
"""

from __future__ import annotations

import os
import shutil

from .chatdb import ChatDB
from .exporters.html_export import write_html
from .exporters.json_export import write_json
from .exporters.manifest import build_manifest, sha256_file, write_manifest
from .exporters.pdf_export import write_pdf
from .models import Conversation


def _safe_name(text: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_+. " else "_" for c in text)
    return keep.strip().replace(" ", "_")[:60] or "conversation"


def _enrich_attachments(conversation: Conversation, out_dir: str,
                        copy: bool) -> list[dict]:
    """Hash (and optionally copy) attachment files that exist on disk."""
    copied: list[dict] = []
    att_dir = os.path.join(out_dir, "attachments")
    for msg in conversation.messages:
        for att in msg.attachments:
            if not att.resolved_path or not os.path.exists(att.resolved_path):
                continue
            digest, size = sha256_file(att.resolved_path)
            att.sha256 = digest
            if copy:
                os.makedirs(att_dir, exist_ok=True)
                base = os.path.basename(att.resolved_path)
                dest = os.path.join(att_dir, f"{att.rowid}_{base}")
                shutil.copy2(att.resolved_path, dest)
                copied.append({
                    "name": os.path.relpath(dest, out_dir),
                    "sha256": digest,
                    "size_bytes": size,
                })
    return copied


def run_export(*, db_path: str, chat_rowid: int, out_dir: str,
               operator: str | None = None, case_reference: str | None = None,
               copy_attachments: bool = False) -> dict:
    """Export one conversation. Returns the manifest dict."""
    os.makedirs(out_dir, exist_ok=True)

    source_sha256, source_size = sha256_file(db_path)

    with ChatDB(db_path) as db:
        db_summary = db.summary()
        conversation = db.load_conversation(chat_rowid)

    copied_outputs = _enrich_attachments(conversation, out_dir, copy_attachments)

    json_path = os.path.join(out_dir, "conversation.json")
    html_path = os.path.join(out_dir, "conversation.html")
    pdf_path = os.path.join(out_dir, "conversation.pdf")

    common = dict(source_sha256=source_sha256, operator=operator,
                  case_reference=case_reference)
    write_json(json_path, conversation, **common)
    write_html(html_path, conversation, **common)
    write_pdf(pdf_path, conversation, **common)

    outputs = []
    for path in (json_path, html_path, pdf_path):
        digest, size = sha256_file(path)
        outputs.append({"name": os.path.basename(path), "sha256": digest,
                        "size_bytes": size})
    outputs.extend(copied_outputs)

    manifest = build_manifest(
        source_path=os.path.abspath(db_path),
        source_sha256=source_sha256,
        source_size=source_size,
        db_summary=db_summary,
        conversation=conversation,
        outputs=outputs,
        operator=operator,
        case_reference=case_reference,
    )
    write_manifest(out_dir, manifest)
    return manifest


def export_target_dir(base_out: str, conversation_title: str,
                      chat_rowid: int) -> str:
    return os.path.join(base_out, f"{chat_rowid}_{_safe_name(conversation_title)}")
