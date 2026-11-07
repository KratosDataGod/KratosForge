"""Clean up: the edges the main tests don't reach. Cells that don't parse or are SQL, string
annotations that aren't code, f-strings and unfinished strings in spacing, and a tidy-up that would
leave a cell that doesn't parse. A tidy-up never changes what the code does."""

from __future__ import annotations

from fabric_desktop import cleanup

from test_cleanup import applied, code, tidy


def test_cells_that_dont_parse_or_arent_python_are_left_alone():
    cells = [code("import os\nx = (", "c1"), code("%%sql\nSELECT 1", "c2"), {"type": "markdown", "source": "# T"},
             code("import json\nprint(json.dumps(1))", "c3")]
    t = tidy(cells)
    assert "unused_imports" not in t
    assert applied(cells, list(t))[:2] == ["import os\nx = (", "%%sql\nSELECT 1"]


def test_import_star_is_never_removed():
    assert "unused_imports" not in tidy([code("from os import *"), code("x = 1")])


def test_a_string_annotation_that_isnt_code_doesnt_keep_or_break_anything():
    cells = [code("from typing import List\nimport os"), code("def f(x: 'not valid ((') -> 'List[int]':\n    y: 'List' = x\n    return y")]
    t = tidy(cells)
    assert t["unused_imports"]["text"] == "Cell 1: no cell uses os."  # List is kept: the annotations use it


def test_a_cell_that_names_values_at_run_time_keeps_every_import_but_a_parse_problem_doesnt_count():
    assert "unused_imports" not in tidy([code("import os\nglobals()['x'] = 1")])
    # a cell that doesn't parse says so as "dynamic", but that alone doesn't keep other cells' imports
    assert "unused_imports" in tidy([code("import os"), code("x = (")])


def test_spacing_leaves_f_strings_and_multi_line_strings_alone():
    src = 'x = f"""a   \n\n\n\nb"""   \ny = 1   \n\n\n\n\nz = 2'
    cells = [code(src)]
    assert "spacing" in tidy(cells)
    [out] = applied(cells, ["spacing"])
    assert out == 'x = f"""a   \n\n\n\nb"""\ny = 1\n\n\nz = 2'


def test_spacing_leaves_a_cell_alone_when_it_cant_tell_where_strings_are():
    assert cleanup._string_lines("x = '''unfinished") == {1}
    assert cleanup._string_lines("if x:\n  y\n z\n") == {1, 2, 3, 4}


def test_a_cell_starting_inside_a_string_keeps_its_leading_blank_line():
    assert cleanup._tidy_spacing('"""\n\nkeep"""') == '"""\n\nkeep"""'


def test_print_lines_and_their_diff():
    cells = [code("print('a')\nx = 1\nprint('b')")]
    t = tidy(cells)
    assert t["print_lines"]["on"] is False and t["print_lines"]["diff"] == [{"cell": 1, "removed": ["print('a')", "print('b')"]}]
    assert applied(cells, ["print_lines"]) == ["x = 1"]
    assert cleanup._print_lines("x = (") == []


def test_a_tidy_up_that_would_leave_a_cell_unparseable_is_not_applied(monkeypatch):
    cells = [code("import os\nx = 1")]
    monkeypatch.setattr(cleanup, "_drop_imports", lambda source, drops: "x = (")
    assert applied(cells, ["unused_imports"]) == ["import os\nx = 1"]


def test_parameter_values_repeated_elsewhere_are_suggested_once():
    cells = [code("region = 'emea-west'\nyear = 2024", tags=["parameters"]),
             code("df = read(region='emea-west')\nlimit = 2024\nother = 'emea-west'"),
             code("for r in df.iterrows():\n    pass")]
    s = {(x["code"], x["cell"]) for x in cleanup.plan(cells)["suggestions"]}
    assert ("parameter_value", 2) in s and ("row_by_row", 3) in s
    assert sum(1 for x in cleanup.plan(cells)["suggestions"] if x["code"] == "parameter_value") == 1


def test_values_too_plain_to_match_a_parameter():
    assert not any(cleanup._worth(v) for v in (True, None, "abc", 0, 1, -1, 10, 100))
    assert all(cleanup._worth(v) for v in ("abcd", 2024, 3.5))
