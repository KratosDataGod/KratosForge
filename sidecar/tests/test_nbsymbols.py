"""Go to definition, find references and rename across a notebook's Python cells (nbsymbols.py)."""

from __future__ import annotations

import pytest

from fabric_desktop.nbsymbols import find_symbol, rename_check


def code(source: str) -> dict:
    return {"type": "code", "source": source}


def spots(result: dict) -> list[tuple[int, str, str]]:
    """(cell, the text at from:to, kind) for each place."""
    return [(p["cell"], p["kind"], p["line"]) for p in result["places"]]


CELLS = [
    code("total = 0\nrows = [1, 2]"),
    {"type": "markdown", "source": "total is the sum"},
    code("for r in rows:\n    total += r\nprint(total)"),
    code("def f(total):\n    return total * 2\n\ndef g():\n    global total\n    total = 5\n"),
    code("%%sql\nSELECT total FROM t"),
    code("df.total = 1\nprint('total')  # total\nf(total=total)"),
]


def test_finds_every_module_level_use_across_cells_and_skips_shadowed_names():
    found = find_symbol(CELLS, 0, 2, None)
    assert found["name"] == "total"
    assert spots(found) == [(0, "definition", 1), (2, "definition", 2), (2, "reference", 3),
                            (3, "reference", 5), (3, "definition", 6), (5, "reference", 3)]
    for p in found["places"]:
        assert symbols_text(CELLS, p) == "total"


def symbols_text(cells, p):
    return cells[p["cell"]]["source"][p["from"]:p["to"]]


def test_the_name_under_the_cursor_or_none_off_a_name():
    assert find_symbol(CELLS, 2, CELLS[2]["source"].index("rows") + 1)["name"] == "rows"
    assert find_symbol(CELLS, 5, CELLS[5]["source"].index("'total'") + 2)["name"] is None  # a string
    assert find_symbol(CELLS, 1, 0)["name"] is None  # Markdown


def test_imports_defs_and_classes_are_definitions():
    cells = [code("import pandas as pd\nfrom os import path\nclass Row:\n    pass\n"),
             code("pd.DataFrame()\npath.join('a')\nr = Row()")]
    for name, line in (("pd", 1), ("path", 2), ("Row", 3)):
        found = find_symbol(cells, 0, 0, name)
        assert [(p["cell"], p["kind"]) for p in found["places"]] == [(0, "definition"), (1, "reference")], name
        assert found["places"][0]["line"] == line
        assert all(symbols_text(cells, p) == name for p in found["places"])


def test_cells_python_cant_read_are_listed_and_utf8_columns_are_characters():
    cells = [code("café = 'é'; x = 1"), code("def broken(:\n  x"), code("print('ü', x)")]
    found = find_symbol(cells, 0, 0, "x")
    assert found["skipped"] == [{"cell": 1, "why": "Python can't read it"}]
    assert [symbols_text(cells, p) for p in found["places"]] == ["x", "x"]


def test_comprehension_and_lambda_targets_are_their_own():
    cells = [code("x = 1\nys = [x for x in range(3)]\nf = lambda x: x\nz = x")]
    found = find_symbol(cells, 0, 0, "x")
    assert [p["line"] for p in found["places"]] == [1, 4]


@pytest.mark.parametrize("cells", ["nope", [1, 2]])
def test_cells_must_be_a_list_of_cells(cells):
    with pytest.raises(ValueError, match="list of cells"):
        find_symbol(cells, 0, 0)


@pytest.mark.parametrize("name, ok", [("total2", True), ("_x", True), ("été", True), ("2x", False), ("a-b", False),
                                      ("class", False), ("", False)])
def test_rename_check(name, ok):
    assert (rename_check(name) is None) == ok
