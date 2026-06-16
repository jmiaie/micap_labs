"""HTML export — a faithful, human-readable rendering of the conversation.

Bubbles are colored to match the Messages app (blue = iMessage, green =
SMS/MMS/RCS) and aligned right for sent / left for received. Full Unicode is
preserved. Every bubble carries ``data-*`` attributes (database ROWID, GUID,
UTC timestamp, text source) so an examiner can tie any line on screen back to
the underlying record.
"""

from __future__ import annotations

from datetime import timezone
from html import escape

from ..models import Conversation, Message

_CSS = """
:root { --blue:#0b78ff; --green:#34c759; --bg:#f3f3f7; --ink:#111; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       background: var(--bg); color: var(--ink); margin: 0; padding: 0 0 60px; }
header.case { background:#fff; border-bottom:1px solid #ddd; padding:16px 20px;
              position:sticky; top:0; }
header.case h1 { font-size:18px; margin:0 0 6px; }
header.case .meta { font-size:12px; color:#555; line-height:1.5;
                    font-family: ui-monospace, "SF Mono", Menlo, monospace;
                    word-break: break-all; }
.thread { max-width: 760px; margin: 0 auto; padding: 16px; }
.daysep { text-align:center; color:#888; font-size:12px; margin:18px 0 8px; }
.row { display:flex; margin:2px 0; }
.row.me { justify-content:flex-end; }
.row.them { justify-content:flex-start; }
.bubble { max-width:75%; padding:8px 12px; border-radius:18px; font-size:15px;
          line-height:1.35; white-space:pre-wrap; word-wrap:break-word; }
.me .bubble.imessage { background:var(--blue); color:#fff; border-bottom-right-radius:5px; }
.me .bubble.sms      { background:var(--green); color:#fff; border-bottom-right-radius:5px; }
.them .bubble        { background:#e9e9eb; color:#000; border-bottom-left-radius:5px; }
.sender { font-size:11px; color:#888; margin:6px 0 1px; }
.me .sender { text-align:right; }
.stamp { font-size:10px; color:#999; margin:1px 4px 8px; }
.me .stamp { text-align:right; }
.attachment { font-size:12px; opacity:.95; margin-top:4px;
              font-family: ui-monospace, Menlo, monospace; }
.badge { font-size:10px; padding:1px 5px; border-radius:6px; background:#0003;
         margin-left:6px; }
.note { color:#b00; font-size:11px; }
footer.foot { text-align:center; color:#999; font-size:11px; margin-top:24px; }
"""


def _stamp(m: Message) -> str:
    if not m.date:
        return "(no timestamp)"
    utc = m.date.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%d %H:%M:%S UTC")


def _day(m: Message) -> str:
    return m.date.astimezone(timezone.utc).strftime("%A, %d %B %Y") if m.date else ""


def _bubble(m: Message) -> str:
    side = "me" if m.is_from_me else "them"
    service_class = "imessage" if m.is_imessage else "sms"
    service_label = m.service or ("iMessage" if m.is_imessage else "SMS")

    parts = [f'<div class="row {side}">']
    body = []
    if m.text:
        body.append(escape(m.text))
    for att in m.attachments:
        name = escape(att.transfer_name or att.filename or f"attachment {att.rowid}")
        meta = []
        if att.mime_type:
            meta.append(escape(att.mime_type))
        if att.total_bytes:
            meta.append(f"{att.total_bytes} bytes")
        if att.sha256:
            meta.append("sha256:" + att.sha256[:16] + "…")
        body.append(
            f'<div class="attachment">📎 {name}'
            + (f' <span class="badge">{", ".join(meta)}</span>' if meta else "")
            + "</div>"
        )
    if m.text_source == "attributedBody(undecoded)":
        body.append('<div class="note">[text could not be decoded; raw blob '
                     'preserved in JSON]</div>')
    if not body:
        body.append('<span class="note">[no text content]</span>')

    bubble_class = service_class if m.is_from_me else "them"
    parts.append(
        f'<div class="bubble {bubble_class}" '
        f'data-rowid="{m.rowid}" data-guid="{escape(m.guid or "")}" '
        f'data-utc="{escape(_stamp(m))}" data-source="{m.text_source}">'
        + "".join(body)
        + "</div>"
    )
    parts.append("</div>")

    sender = "Me" if m.is_from_me else escape(m.sender)
    parts.insert(1, f'<div class="sender">{sender} · {escape(service_label)}</div>')
    parts.append(f'<div class="stamp">{escape(_stamp(m))} · msg #{m.rowid}</div>')
    return "".join(parts)


def write_html(path: str, conversation: Conversation, *,
               source_sha256: str, operator: str | None,
               case_reference: str | None) -> None:
    start, end = conversation.date_range
    rng = ""
    if start and end:
        rng = (start.astimezone(timezone.utc).strftime("%Y-%m-%d")
               + " to " + end.astimezone(timezone.utc).strftime("%Y-%m-%d"))

    meta_lines = [
        f"Participants: {escape(', '.join(p.handle for p in conversation.participants) or conversation.title)}",
        f"Messages: {len(conversation.messages)} &nbsp; Date range (UTC): {escape(rng)}",
        f"Source SHA-256: {escape(source_sha256)}",
    ]
    if case_reference:
        meta_lines.append(f"Case: {escape(case_reference)}")
    if operator:
        meta_lines.append(f"Operator: {escape(operator)}")

    rows = []
    last_day = None
    for m in conversation.messages:
        day = _day(m)
        if day and day != last_day:
            rows.append(f'<div class="daysep">{escape(day)}</div>')
            last_day = day
        rows.append(_bubble(m))

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(conversation.title)} — conversation export</title>
<style>{_CSS}</style>
</head><body>
<header class="case">
  <h1>{escape(conversation.title)}</h1>
  <div class="meta">{"<br>".join(meta_lines)}</div>
</header>
<div class="thread">
{chr(10).join(rows)}
</div>
<footer class="foot">Generated by micap-imessage-export. Authoritative data: manifest.json &amp; conversation.json.</footer>
</body></html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
