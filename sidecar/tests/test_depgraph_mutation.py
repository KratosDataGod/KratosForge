"""Mutation-testing follow-up for notebook cell dependencies (depgraph.py): dotted imports, names
local to nested functions, keyword-only defaults, a cell magic part way down, the line a parse
error or a network call is reported on, and declared edges that carry no names."""

import pytest

from fabric_desktop import depgraph
from tests.test_depgraph import by_n, cells


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def test_imports_define_the_top_package_or_the_alias():
    a = depgraph.analyze("import a.b.c\nimport os.path as p\nfrom m import q, r as rr")
    assert a["defines"] == ["a", "p", "q", "rr"]


def test_an_import_inside_a_nested_function_stays_local_to_that_function():
    a = depgraph.analyze("def f():\n    def g():\n        import os\n    return os")
    assert a["defines"] == ["f"] and a["uses"] == ["os"] and a["deferred"] == ["os"]


def test_keyword_only_defaults_are_read_when_the_function_is_defined():
    a = depgraph.analyze("def f(*, k=kk, j=None):\n    return k")
    assert a["uses"] == ["kk"] and a["deferred"] == []


def test_names_after_a_function_are_read_now_not_deferred():
    a = depgraph.analyze("def f():\n    return x\ny = z")
    assert a["uses"] == ["x", "z"] and a["deferred"] == ["x"]


def test_a_bare_write_table_call_writes_and_one_without_a_name_does_not():
    a = depgraph.analyze("write_table(df, 'Sales')\nlakehouse.write_table(df)")
    assert a["writes"] == [{"kind": "table", "name": "dbo.sales", "line": 1}]


def test_only_sql_calls_are_read_as_sql():
    assert depgraph.analyze("print('DELETE FROM x')")["writes"] == []
    a = depgraph.analyze("spark.sql(q)\nspark.sql('SELECT 1 FROM t')")
    assert a["reads"] == [{"kind": "table", "name": "dbo.t", "line": 2}]


def test_fs_copies_write_their_destination_and_other_fs_calls_their_first_argument():
    a = depgraph.analyze("notebookutils.fs.cp('Files/a','Files/b')\nmssparkutils.fs.rm('Files/c')\n"
                         "notebookutils.fs.cp('Files/z')\nnotebookutils.fs.put(name)")
    assert a["writes"] == [{"kind": "file", "name": "Files/b", "line": 1}, {"kind": "file", "name": "Files/c", "line": 2},
                           {"kind": "file", "name": "Files/z", "line": 3}]
    assert [e["line"] for e in a["effects"]] == [1, 2, 3, 4]


def test_a_file_write_needs_a_literal_path():
    a = depgraph.analyze("df.to_csv(p)\ndf.write_parquet('Files/o.parquet')")
    assert a["writes"] == [{"kind": "file", "name": "Files/o.parquet", "line": 2}]
    assert a["effects"] == [{"line": 2, "text": "writes the file Files/o.parquet"}]


def test_a_cell_magic_below_a_line_magic_reports_the_line_magic_once():
    a = depgraph.analyze("%pip install x\n%%sh\nls")
    assert a["effects"] == [{"line": 1, "text": "installs packages"}]


def test_a_parse_error_is_reported_on_its_line():
    a = depgraph.analyze("x = 1\ny = (\n")
    assert a["parsed"] is False
    assert a["dynamic"] == [{"line": 2, "text": "doesn't parse: '(' was never closed"}]


def test_a_network_call_is_reported_on_its_line():
    a = depgraph.analyze("import requests\nx = 1\ny = requests.get(u)")
    assert a["effects"] == [{"line": 3, "text": "calls requests.get over the network"}]


def test_a_declared_dependency_carries_no_names():
    g = by_n(depgraph.graph(cells("a = 1", "b = 2", meta={2: {"depends_on": ["c1"]}})))
    assert g[2]["upstream"] == [{"cell_id": "c1", "n": 1, "kind": "declared", "via": [], "source": "declared"}]


def test_only_file_system_calls_on_fs_change_files():
    assert depgraph.analyze("rows.append('Files/a')\nnotebookutils.fs.ls('Files')")["effects"] == []


def test_a_cell_magic_two_lines_below_a_line_magic_doesnt_repeat_it():
    a = depgraph.analyze("%pip install a\nx = 1\n%%sh\nls")
    assert a["effects"] == [{"line": 1, "text": "installs packages"}]
