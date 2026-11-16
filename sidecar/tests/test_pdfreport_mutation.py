"""Edge cases of pdfreport.py: font metrics, wrapping at exact widths, page breaks and layout positions."""

from __future__ import annotations

import re
from pathlib import Path

import matplotlib

from fabric_desktop import pdfreport

AFM = Path(matplotlib.get_data_path()) / "fonts" / "afm"


def _afm_widths(name: str) -> tuple[int, ...]:
    """Widths of ASCII 32..126 in WinAnsiEncoding, read from Adobe's AFM file shipped with matplotlib."""
    by_code, by_name = {}, {}
    for row in (AFM / name).read_text(encoding="latin-1").splitlines():
        m = re.match(r"C (-?\d+) ; WX (\d+) ; N (\S+) ;", row)
        if m:
            by_code[int(m.group(1))] = int(m.group(2))
            by_name[m.group(3)] = int(m.group(2))
    # The AFM files use StandardEncoding, where 39 and 96 are curly quotes; WinAnsi has ' and ` there.
    return tuple(by_name["quotesingle"] if c == 39 else by_name["grave"] if c == 96 else by_code[c] for c in range(32, 127))


def test_the_width_tables_match_adobes_helvetica_metrics():
    assert pdfreport._REGULAR == _afm_widths("phvr8a.afm")
    assert pdfreport._BOLD == _afm_widths("phvb8a.afm")
    assert pdfreport._REGULAR == _afm_widths("phvro8a.afm")  # oblique has the regular widths


def test_width_uses_the_table_for_printable_ascii_and_556_for_anything_else():
    assert pdfreport.width(" ", "F1", 1000) == 278
    assert pdfreport.width("~", "F1", 1000) == 584
    assert pdfreport.width("~", "F2", 1000) == 584
    for c in ("\x1f", "\x7f", "é", "狼"):
        assert pdfreport.width(c, "F1", 1000) == 556, repr(c)
    assert pdfreport.width("ab", "F1", 10) == (556 + 556) * 10 / 1000


def test_runs_start_with_emphasis_without_an_empty_run():
    assert pdfreport._runs("*a* b", "F1") == [("a", "F3"), (" b", "F1")]
    assert pdfreport._runs(r"\*x", "F1") == [("*", "F1"), ("x", "F1")]


def test_merge_joins_only_neighbours_in_the_same_font():
    line = [("a", "F1"), ("b", "F1"), ("c", "F2"), ("d", "F1")]
    assert pdfreport._merge(line) == [("ab", "F1"), ("c", "F2"), ("d", "F1")]


def _texts(rows):
    return ["".join(t for t, _ in row) for row in rows]


def test_a_word_exactly_a_line_wide_starts_its_own_line_unbroken():
    # At size 1000 widths are whole numbers: "i" is 222, a space 278.
    rows = pdfreport._wrap(pdfreport._runs("a " + "i" * 10, "F1"), 1000, 2220)
    assert _texts(rows) == ["a", "i" * 10]


def test_a_word_longer_than_a_line_breaks_into_full_lines():
    rows = pdfreport._wrap(pdfreport._runs("i" * 25, "F1"), 1000, 2220)
    assert _texts(rows) == ["i" * 10, "i" * 10, "i" * 5]


def test_two_words_that_fit_exactly_share_a_line_and_one_point_less_splits_them():
    runs = pdfreport._runs("iiii iiii", "F1")
    assert _texts(pdfreport._wrap(runs, 1000, 222 * 8 + 278)) == ["iiii iiii"]
    assert _texts(pdfreport._wrap(runs, 1000, 222 * 8 + 277)) == ["iiii", "iiii"]


def test_the_space_after_a_word_takes_the_font_its_last_letter_is_in():
    assert pdfreport._wrap(pdfreport._runs("*a* b", "F1"), 10, 500) == [[("a ", "F3"), ("b", "F1")]]
    # A word that changes font mid-way: the space follows its last run, not its first.
    assert pdfreport._wrap(pdfreport._runs("*a*b c", "F1"), 10, 500) == [[("a", "F3"), ("b c", "F1")]]


def test_need_starts_a_page_only_below_the_bottom_margin_and_footer():
    p = pdfreport._Pages()
    assert p.y == 738
    p.y = 54 + 18 + 10
    p.need(10)
    assert len(p.pages) == 1 and p.y == 82
    p.need(11)
    assert len(p.pages) == 2 and p.y == 738


def _streams(pdf: bytes) -> list[str]:
    return [s.decode("latin-1") for s in re.findall(rb"stream\n(.*?)\nendstream", pdf, re.S)]


def _shown(stream: str) -> list[tuple[float, float, str]]:
    """(x, y, text) of each text line drawn, footer included."""
    out = []
    for m in re.finditer(r"BT (\S+) (\S+) Td (.*?) ET", stream):
        out.append((float(m.group(1)), float(m.group(2)), "".join(re.findall(r"\((.*?)\) Tj", m.group(3)))))
    return out


def test_pages_are_us_letter_with_text_at_the_left_margin():
    pdf = pdfreport.render("Hello")
    assert b"/MediaBox [0 0 612 792]" in pdf
    [(x, y, text), _footer] = _shown(_streams(pdf)[0])
    assert (x, y, text) == (54, 723.3, "Hello")  # 738 less one 14.7-point body line


def test_headings_blank_lines_and_paragraphs_sit_at_their_line_heights():
    stream = _streams(pdfreport.render("# Title\n## Section\nOne\n\nTwo"))[0]
    rows = _shown(stream)[:-1]
    assert rows == [(54, 714.6, "Title"), (54, 686.4, "Section"), (54, 671.7, "One"), (54, 650.7, "Two")]
    assert "/F2 18 Tf (Title)" in stream and "/F2 13 Tf (Section)" in stream and "/F1 10.5 Tf (One)" in stream


def test_a_paragraph_wraps_at_the_width_between_the_margins():
    text = " ".join(f"word{i}" for i in range(60))
    expected = _texts(pdfreport._wrap(pdfreport._runs(text, "F1"), pdfreport.BODY, 504))
    assert len(expected) > 2
    assert [t for _x, _y, t in _shown(_streams(pdfreport.render(text))[0])[:-1]] == expected


def test_a_wrapped_bullet_has_one_bullet_mark_and_an_indented_text():
    text = " ".join(f"item{i}" for i in range(40))
    rows = _shown(_streams(pdfreport.render("- " + text))[0])[:-1]
    marks = [r for r in rows if r[2] == "\x95"]
    lines = [r for r in rows if r[2] != "\x95"]
    assert marks == [(58, 723.3, "\x95")]
    assert len(lines) > 1 and {x for x, _y, _t in lines} == {68}
    assert " ".join(t for _x, _y, t in lines) == text


def _heading_page(body_lines: int, blanks: int) -> int:
    md = "\n".join(["x"] * body_lines + [""] * blanks + ["## Head"])
    for n, stream in enumerate(_streams(pdfreport.render(md)), 1):
        if any(t == "Head" for _x, _y, t in _shown(stream)):
            return n
    raise AssertionError("heading not drawn")


def test_a_section_heading_moves_to_the_next_page_unless_three_body_lines_fit_under_it():
    # The heading needs 13*1.4 + 10.5*3 = 49.7 points above the 72-point bottom limit.
    assert _heading_page(41, 1) == 1  # y 129.0: room for it
    assert _heading_page(42, 0) == 2  # y 120.6: not quite
    assert _heading_page(43, 0) == 2  # y 105.9


def test_the_footer_is_right_aligned_at_the_bottom_margin():
    [*_, (x, y, text)] = _shown(_streams(pdfreport.render("x"))[0])
    assert text == "Page 1 of 1" and y == 36
    assert round(x + pdfreport.width(text, "F1", 8), 2) == 612 - 54


def test_the_trailer_size_counts_every_xref_entry():
    pdf = pdfreport.render("x")
    count = int(re.search(rb"xref\n0 (\d+)\n", pdf).group(1))
    assert count == 9  # the free entry, 6 fixed objects and one page with its content
    assert re.search(rb"/Size (\d+) ", pdf).group(1) == b"9"
