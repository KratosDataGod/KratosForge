"""A small PDF writer for the professional report (KF-DX-010): the report's Markdown as a clean,
text-only PDF, with no new dependency.

It understands exactly the Markdown progress.report() writes: ``#`` and ``##`` headings, ``- ``
bullets, paragraphs, ``*emphasis*`` (the source tags) and backslash escapes (names are escaped, so a
notebook called ``*x*`` stays text). Everything is drawn with the PDF standard fonts Helvetica,
Helvetica-Bold and Helvetica-Oblique in WinAnsi encoding, so the file embeds no font, no image and
no script, and opens in any PDF reader. A character the encoding doesn't have prints as ``?``.

US Letter, 0.75 inch margins, word-wrapped with the fonts' published character widths, and a
"Page n of N" footer. The output depends only on the text, so the same report gives the same bytes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

PAGE_W, PAGE_H = 612, 792
MARGIN = 54
BODY, H1, H2, FOOT = 10.5, 18, 13, 8

# Widths (1/1000 em) of printable ASCII 32..126, from the Adobe Helvetica and Helvetica-Bold AFM files.
# Oblique has Helvetica's widths. Anything else counts as 556, a typical lower-case width.
_REGULAR = (278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, *(556,) * 10,
            278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833,
            722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556,
            556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556,
            500, 722, 500, 500, 500, 334, 260, 334, 584)
_BOLD = (278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, *(556,) * 10,
         333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833,
         722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556,
         611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611,
         556, 778, 556, 556, 500, 389, 280, 389, 584)
_FONTS = {"F1": ("Helvetica", _REGULAR), "F2": ("Helvetica-Bold", _BOLD), "F3": ("Helvetica-Oblique", _REGULAR)}
_INLINE = re.compile(r"\\(.)|\*([^*\\]+)\*")


def width(text: str, font: str, size: float) -> float:
    table = _FONTS[font][1]
    return sum(table[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in text) * size / 1000


def _runs(text: str, font: str) -> list[tuple[str, str]]:
    """``text`` split into (text, font) runs: *emphasis* in oblique, escapes as their character."""
    out: list[tuple[str, str]] = []
    last = 0
    for m in _INLINE.finditer(text):
        if m.start() > last:
            out.append((text[last:m.start()], font))
        out.append((m.group(1), font) if m.group(1) is not None else (m.group(2), "F3"))
        last = m.end()
    if last < len(text):
        out.append((text[last:], font))
    return out


def _words(runs: list[tuple[str, str]]) -> Iterator[list[tuple[str, str]]]:
    """Words (each a list of runs, since a word can change font mid-way), split at spaces."""
    word: list[tuple[str, str]] = []
    for text, font in runs:
        parts = text.split(" ")
        for i, part in enumerate(parts):
            if i:
                if word:
                    yield word
                word = []
            if part:
                word.append((part, font))
    if word:
        yield word


def _wrap(runs: list[tuple[str, str]], size: float, room: float) -> list[list[tuple[str, str]]]:
    """Lines of runs that fit ``room`` points. A word longer than a line is broken by character."""
    space = width(" ", "F1", size)
    lines: list[list[tuple[str, str]]] = []
    line: list[tuple[str, str]] = []
    used = 0.0
    for word in _words(runs):
        w = sum(width(t, f, size) for t, f in word)
        if w > room:  # a long id or path: break it to fit
            for t, f in word:
                for ch in t:
                    cw = width(ch, f, size)
                    if used + cw > room and line:
                        lines.append(line)
                        line, used = [], 0.0
                    line.append((ch, f))
                    used += cw
            continue
        if line and used + space + w > room:
            lines.append(line)
            line, used = [], 0.0
        if line:
            line.append((" ", line[-1][1]))
            used += space
        line.extend(word)
        used += w
    if line:
        lines.append(line)
    return [_merge(x) for x in lines] or [[]]


def _merge(line: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Neighbouring runs in the same font as one, so a line is a few strings, not one per word."""
    out: list[tuple[str, str]] = []
    for text, font in line:
        if out and out[-1][1] == font:
            out[-1] = (out[-1][0] + text, font)
        else:
            out.append((text, font))
    return out


def _pdf_text(text: str) -> str:
    raw = text.encode("cp1252", errors="replace").decode("latin-1")
    return raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class _Pages:
    def __init__(self) -> None:
        self.pages: list[list[str]] = [[]]
        self.y: float = PAGE_H - MARGIN

    def need(self, height: float) -> None:
        if self.y - height < MARGIN + 18:
            self.pages.append([])
            self.y = PAGE_H - MARGIN

    def line(self, segments: list[tuple[float, list[tuple[str, str]]]], size: float, lead: float) -> None:
        """One line: each segment is an x position and the runs drawn from there."""
        self.need(lead)
        self.y -= lead
        for x, runs in segments:
            ops, last = [], None
            for text, font in runs:
                if font != last:
                    ops.append(f"/{font} {size:g} Tf")
                    last = font
                ops.append(f"({_pdf_text(text)}) Tj")
            if ops:
                self.pages[-1].append(f"BT {x:g} {self.y:.2f} Td {' '.join(ops)} ET")


def render(markdown: str, title: str = "Engineering accomplishments") -> bytes:
    """The report's Markdown as PDF bytes."""
    out = _Pages()
    room = PAGE_W - 2 * MARGIN
    for raw in markdown.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            out.y -= BODY * 0.6
            continue
        if line.startswith("# "):
            for row in _wrap(_runs(line[2:], "F2"), H1, room):
                out.line([(MARGIN, row)], H1, H1 * 1.3)
            out.y -= 4
        elif line.startswith("## "):
            out.need(H2 * 1.4 + BODY * 3)  # keep a heading with the start of its section
            out.y -= 6
            for row in _wrap(_runs(line[3:], "F2"), H2, room):
                out.line([(MARGIN, row)], H2, H2 * 1.4)
        elif line.startswith("- "):
            for i, row in enumerate(_wrap(_runs(line[2:], "F1"), BODY, room - 14)):
                bullet = [(MARGIN + 4, [("\u2022", "F1")])] if i == 0 else []
                out.line([*bullet, (MARGIN + 14, row)], BODY, BODY * 1.4)
        else:
            for row in _wrap(_runs(line, "F1"), BODY, room):
                out.line([(MARGIN, row)], BODY, BODY * 1.4)
    return _assemble(out.pages, title)


def _assemble(pages: list[list[str]], title: str) -> bytes:
    n = len(pages)
    # objects: 1 catalog, 2 pages, 3-5 fonts, 6 info, then a page and its content per page
    objects: dict[int, bytes] = {}
    kids = []
    for i, ops in enumerate(pages):
        page_id, content_id = 7 + 2 * i, 8 + 2 * i
        kids.append(f"{page_id} 0 R")
        footer = f"Page {i + 1} of {n}"
        fx = PAGE_W - MARGIN - width(footer, "F1", FOOT)
        stream = "\n".join([*ops, f"0.4 g BT {fx:.2f} {MARGIN - 18:g} Td /F1 {FOOT:g} Tf ({footer}) Tj ET"]).encode("latin-1")
        objects[content_id] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        objects[page_id] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> /Contents {content_id} 0 R >>").encode("latin-1")
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {n} >>".encode("latin-1")
    for num, (name, _) in zip((3, 4, 5), _FONTS.values(), strict=True):
        objects[num] = f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} /Encoding /WinAnsiEncoding >>".encode("latin-1")
    objects[6] = f"<< /Title ({_pdf_text(title)}) /Producer (KratosForge) >>".encode("latin-1")
    body = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for num in sorted(objects):
        offsets.append(len(body))
        body += f"{num} 0 obj\n".encode("ascii") + objects[num] + b"\nendobj\n"
    xref = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    body += b"".join(f"{o:010d} 00000 n \n".encode("ascii") for o in offsets)
    body += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    return bytes(body)
