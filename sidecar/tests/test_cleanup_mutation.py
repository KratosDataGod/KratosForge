"""Clean up: exact titles and texts, the f-string line tracking, leading blank lines, frozen
cells and an empty cell that wasn't picked. Found by mutation testing (batch 23). Clean up only
edits cell text in memory and writes nothing; the fixture keeps any stray path in tmp_path."""

from __future__ import annotations

import pytest

from test_cleanup import applied, code, tidy


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def test_titles_say_one_or_many():
    assert tidy([code("import os\nx = 1")])["unused_imports"]["title"] == "Remove 1 unused import"
    assert tidy([code("import os\nimport json\nx = 1")])["unused_imports"]["title"] == "Remove 2 unused imports"


def test_three_cells_are_listed_with_and():
    cells = [code("x = 1   ", "a"), code("y = 2   ", "b"), code("z = 3   ", "c")]
    assert tidy(cells)["spacing"]["text"] == "Cells 1, 2 and 3: spaces at line ends, extra blank lines."


def test_two_print_lines_are_counted_from_the_source():
    t = tidy([code("x = 1\nprint(x)\nprint(x + 1)")])
    assert t["print_lines"]["title"] == "Remove 2 print lines"


def test_one_empty_cell_has_no_code_and_two_have_none():
    one = tidy([code("x = 1", "a"), code("   ", "b")])["empty_cells"]
    assert (one["title"], one["text"]) == ("Remove 1 empty cell", "Cell 2 has no code.")
    two = tidy([code("x = 1", "a"), code("", "b"), code("\n", "c")])["empty_cells"]
    assert (two["title"], two["text"]) == ("Remove 2 empty cells", "Cells 2 and 3 have no code.")


def test_an_empty_cell_stays_unless_its_change_is_picked():
    cells = [code("import os\nx = 1", "a"), code("", "b")]
    assert "empty_cells" in tidy(cells)
    from fabric_desktop import cleanup
    out = cleanup.apply(cells, ["unused_imports"])
    assert out["removed"] == []
    assert [c["cell_id"] for c in out["cells"]] == ["a", "b"]
    assert [c["source"] for c in out["cells"]] == ["x = 1", ""]


def test_leading_blank_lines_go():
    cells = [code("\n\nx = 1")]
    assert "spacing" in tidy(cells)
    assert applied(cells, ["spacing"]) == ["x = 1"]


def test_a_frozen_cell_gets_no_spacing_change():
    cells = [code("x = 1   \n\n\n\ny = 2", "a", frozen=True)]
    assert "spacing" not in tidy(cells)
    assert applied(cells, ["spacing"]) == ["x = 1   \n\n\n\ny = 2"]


def test_f_string_lines_are_kept_up_to_but_not_including_the_closing_line():
    src = 'x = f"""a  \n{y}  \nc"""   \nz = 1   '
    cells = [code(src)]
    assert "spacing" in tidy(cells)
    assert applied(cells, ["spacing"]) == ['x = f"""a  \n{y}  \nc"""\nz = 1']
