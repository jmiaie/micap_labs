"""A minimal, dependency-free PDF writer tuned for evidence documents.

This deliberately avoids third-party libraries (reportlab, fpdf, headless
browsers) so the exporter runs anywhere Python does, with nothing to install.

It uses only the PDF standard-14 Courier fonts (no font embedding). Courier is
monospaced, which makes line-wrapping math exact and produces a clean,
unambiguous exhibit. Characters outside WinAnsi/cp1252 (most emoji, many
non-Latin scripts) cannot be drawn by a base font and are rendered as a visible
``[U+XXXX]`` placeholder so nothing is *silently* dropped — the full-fidelity
text is always preserved in the JSON and HTML exports.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

PAGE_W, PAGE_H = 612.0, 792.0  # US Letter, points
MARGIN = 54.0
CHAR_W_RATIO = 0.6  # Courier advance width = 0.6 em
LINE_FACTOR = 1.32

Color = tuple[float, float, float]
BLACK: Color = (0.0, 0.0, 0.0)
GREY: Color = (0.4, 0.4, 0.4)
IMESSAGE_BLUE: Color = (0.0, 0.32, 0.86)
SMS_GREEN: Color = (0.18, 0.55, 0.20)


def _sanitize(s: str) -> str:
    """Map a text run to drawable cp1252 characters; escape the rest."""
    out: list[str] = []
    for ch in s:
        if ch in ("\n", "\r"):
            continue
        if ch == "\t":
            out.append("    ")
            continue
        if ord(ch) < 0x20:
            out.append(f"[U+{ord(ch):04X}]")
            continue
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            out.append(f"[U+{ord(ch):04X}]")
    return "".join(out)


def _escape(s: str) -> bytes:
    s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return s.encode("cp1252", "replace")


def _wrap(text: str, max_chars: int) -> list[str]:
    if max_chars < 1:
        max_chars = 1
    lines: list[str] = []
    for raw in text.split("\n"):
        if raw == "":
            lines.append("")
            continue
        words = raw.split(" ")
        cur = ""
        for word in words:
            while len(word) > max_chars:  # hard-break very long tokens
                if cur:
                    lines.append(cur)
                    cur = ""
                lines.append(word[:max_chars])
                word = word[max_chars:]
            candidate = word if not cur else cur + " " + word
            if len(candidate) <= max_chars:
                cur = candidate
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


@dataclass
class _Row:
    kind: str  # "text" | "rule" | "gap"
    height: float
    text: str = ""
    bold: bool = False
    size: float = 9.0
    color: Color = BLACK
    indent: float = 0.0


@dataclass
class PdfBuilder:
    body_size: float = 9.0
    header_lines: list[str] = field(default_factory=list)
    footer_note: str = ""
    _rows: list[_Row] = field(default_factory=list)

    # -- content API --------------------------------------------------------
    def set_header(self, lines: list[str]) -> None:
        self.header_lines = lines

    def set_footer_note(self, note: str) -> None:
        self.footer_note = note

    def _text_row(self, text, *, bold, size, color, indent) -> None:
        usable = PAGE_W - 2 * MARGIN - indent
        max_chars = int(usable / (CHAR_W_RATIO * size))
        for ln in _wrap(_sanitize(text), max_chars):
            self._rows.append(
                _Row("text", size * LINE_FACTOR, ln, bold, size, color, indent)
            )

    def title(self, text: str) -> None:
        self._text_row(text, bold=True, size=16, color=BLACK, indent=0)
        self.gap(6)

    def heading(self, text: str) -> None:
        self.gap(4)
        self._text_row(text, bold=True, size=11, color=BLACK, indent=0)

    def line(self, text="", *, bold=False, color=BLACK, indent=0.0, size=None):
        self._text_row(text, bold=bold, size=size or self.body_size,
                       color=color, indent=indent)

    def kv(self, key: str, value: str) -> None:
        self._text_row(f"{key}: {value}", bold=False, size=self.body_size,
                       color=BLACK, indent=0)

    def gap(self, height: float = 6.0) -> None:
        self._rows.append(_Row("gap", height))

    def rule(self, color: Color = GREY) -> None:
        self._rows.append(_Row("rule", 8.0, color=color))

    # -- rendering ----------------------------------------------------------
    def _header_height(self) -> float:
        return len(self.header_lines) * 10 + 10 if self.header_lines else 0

    def _paginate(self) -> list[list[_Row]]:
        body_top = PAGE_H - MARGIN - self._header_height()
        body_bottom = MARGIN + 16  # leave room for footer
        pages: list[list[_Row]] = []
        cur: list[_Row] = []
        y = body_top
        for row in self._rows:
            if y - row.height < body_bottom and cur:
                pages.append(cur)
                cur = []
                y = body_top
            cur.append(row)
            y -= row.height
        if cur:
            pages.append(cur)
        return pages or [[]]

    def _render_page(self, rows: list[_Row], page_no: int, total: int) -> bytes:
        ops: list[bytes] = []

        def show(text, x, y, size, bold, color):
            font = "F2" if bold else "F1"
            r, g, b = color
            ops.append(
                f"{r:.3f} {g:.3f} {b:.3f} rg\nBT /{font} {size:.1f} Tf "
                f"1 0 0 1 {x:.2f} {y:.2f} Tm ".encode()
                + b"(" + _escape(text) + b") Tj ET\n"
            )

        def hline(y, color=GREY):
            r, g, b = color
            ops.append(
                f"{r:.3f} {g:.3f} {b:.3f} RG 0.5 w {MARGIN:.2f} {y:.2f} m "
                f"{PAGE_W - MARGIN:.2f} {y:.2f} l S\n".encode()
            )

        # Header, repeated on every page.
        y = PAGE_H - MARGIN
        if self.header_lines:
            for i, hl in enumerate(self.header_lines):
                show(_sanitize(hl), MARGIN, y - 8, 8, i == 0, GREY)
                y -= 10
            hline(y - 2)
        # Body.
        y = PAGE_H - MARGIN - self._header_height()
        for row in rows:
            if row.kind == "gap":
                y -= row.height
            elif row.kind == "rule":
                hline(y - 4, row.color)
                y -= row.height
            else:
                show(row.text, MARGIN + row.indent, y - row.size, row.size,
                     row.bold, row.color)
                y -= row.height
        # Footer.
        hline(MARGIN + 12)
        footer = f"Page {page_no} of {total}"
        if self.footer_note:
            footer += f"   |   {self.footer_note}"
        show(_sanitize(footer), MARGIN, MARGIN, 7.5, False, GREY)
        return b"".join(ops)

    def build(self) -> bytes:
        pages = self._paginate()
        total = len(pages)
        contents = [self._render_page(p, i + 1, total) for i, p in enumerate(pages)]

        objects: dict[int, bytes] = {}
        objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>"
        objects[4] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold /Encoding /WinAnsiEncoding >>"

        kids: list[int] = []
        for i, content in enumerate(contents):
            content_obj = 5 + 2 * i
            page_obj = 6 + 2 * i
            stream = (
                f"<< /Length {len(content)} >>\nstream\n".encode()
                + content
                + b"\nendstream"
            )
            objects[content_obj] = stream
            objects[page_obj] = (
                f"<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 {PAGE_W:.0f} {PAGE_H:.0f}] "
                f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                f"/Contents {content_obj} 0 R >>".encode()
            )
            kids.append(page_obj)

        kids_str = " ".join(f"{k} 0 R" for k in kids).encode()
        objects[2] = b"<< /Type /Pages /Kids [" + kids_str + b"] /Count " \
            + str(len(kids)).encode() + b" >>"

        return _serialize(objects)


def _serialize(objects: dict[int, bytes]) -> bytes:
    max_obj = max(objects)
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for num in range(1, max_obj + 1):
        body = objects.get(num)
        if body is None:
            continue
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_pos = len(out)
    size = max_obj + 1
    out += f"xref\n0 {size}\n".encode()
    out += b"0000000000 65535 f \n"
    for num in range(1, size):
        if num in offsets:
            out += f"{offsets[num]:010d} 00000 n \n".encode()
        else:
            out += b"0000000000 00000 f \n"
    doc_id = hashlib.md5(bytes(out)).hexdigest().upper()
    out += b"trailer\n"
    out += (
        f"<< /Size {size} /Root 1 0 R /ID [<{doc_id}> <{doc_id}>] >>\n"
    ).encode()
    out += f"startxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)
