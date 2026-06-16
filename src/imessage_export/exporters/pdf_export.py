"""PDF export — the paginated exhibit intended for filing/printing.

Each page repeats a header identifying the conversation and the source database
hash, and a footer with page numbers, so a printed copy is self-identifying and
tamper-evident page-by-page. Messages are listed chronologically with full
metadata: database row number, UTC timestamp, direction, sender handle, and
service (iMessage vs SMS).
"""

from __future__ import annotations

from datetime import timezone

from ..models import Conversation, Message
from ..pdfwriter import IMESSAGE_BLUE, SMS_GREEN, GREY, PdfBuilder
from ..version import TOOL_NAME, __version__


def _stamp(m: Message) -> str:
    if not m.date:
        return "(no timestamp)"
    return m.date.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _message_block(pdf: PdfBuilder, m: Message) -> None:
    color = IMESSAGE_BLUE if m.is_imessage else SMS_GREEN
    direction = "SENT  ->" if m.is_from_me else "RECV  <-"
    service = m.service or ("iMessage" if m.is_imessage else "SMS")
    header = f"#{m.rowid}  {_stamp(m)}  {direction}  {m.sender}  [{service}]"
    pdf.line(header, bold=True, color=color)

    if m.text:
        pdf.line(m.text, indent=18)
    for att in m.attachments:
        name = att.transfer_name or att.filename or f"attachment {att.rowid}"
        meta = []
        if att.mime_type:
            meta.append(att.mime_type)
        if att.total_bytes:
            meta.append(f"{att.total_bytes} bytes")
        if att.sha256:
            meta.append("sha256:" + att.sha256)
        suffix = f" ({'; '.join(meta)})" if meta else ""
        pdf.line(f"[attachment] {name}{suffix}", indent=18, color=GREY)
    if m.text_source == "attributedBody(undecoded)":
        pdf.line("[text could not be decoded; raw blob preserved in JSON]",
                 indent=18, color=GREY)
    if not m.text and not m.attachments:
        pdf.line("[no text content]", indent=18, color=GREY)

    receipts = []
    if m.date_delivered:
        receipts.append("delivered " + m.date_delivered.astimezone(timezone.utc)
                        .strftime("%Y-%m-%d %H:%M:%S UTC"))
    if m.date_read:
        receipts.append("read " + m.date_read.astimezone(timezone.utc)
                        .strftime("%Y-%m-%d %H:%M:%S UTC"))
    if receipts:
        pdf.line("   " + " | ".join(receipts), indent=18, color=GREY, size=7.5)
    pdf.gap(5)


def write_pdf(path: str, conversation: Conversation, *,
              source_sha256: str, operator: str | None,
              case_reference: str | None) -> None:
    pdf = PdfBuilder(body_size=9.0)
    start, end = conversation.date_range
    rng = "n/a"
    if start and end:
        rng = (start.astimezone(timezone.utc).strftime("%Y-%m-%d")
               + " to " + end.astimezone(timezone.utc).strftime("%Y-%m-%d"))

    pdf.set_header([
        f"CONVERSATION EXPORT — {conversation.title}",
        f"source sha256: {source_sha256}",
        f"case: {case_reference or '(n/a)'}    operator: {operator or '(n/a)'}",
    ])
    pdf.set_footer_note(f"{TOOL_NAME} v{__version__}")

    # Cover / summary section.
    pdf.title("Message Conversation Export")
    pdf.kv("Conversation", conversation.title)
    pdf.kv("Group chat", "yes" if conversation.is_group else "no")
    pdf.kv("Participants",
           ", ".join(p.handle for p in conversation.participants) or "(none)")
    pdf.kv("Messages", str(len(conversation.messages)))
    pdf.kv("Date range (UTC)", rng)
    pdf.kv("Source SHA-256", source_sha256)
    pdf.kv("Case reference", case_reference or "(not provided)")
    pdf.kv("Operator", operator or "(not provided)")
    pdf.gap(4)
    pdf.line("Colours: blue = iMessage, green = SMS/MMS. All times shown in UTC. "
             "'#n' is the database row id. This exhibit is a rendering of "
             "conversation.json; verify integrity via manifest.json.",
             color=GREY, size=8)
    pdf.rule()
    pdf.heading("Messages (chronological)")
    pdf.gap(2)

    last_day = None
    for m in conversation.messages:
        day = m.date.astimezone(timezone.utc).strftime("%A, %d %B %Y") if m.date else None
        if day and day != last_day:
            pdf.gap(2)
            pdf.line(f"——— {day} ———", bold=True, color=GREY, size=8)
            last_day = day
        _message_block(pdf, m)

    with open(path, "wb") as f:
        f.write(pdf.build())
