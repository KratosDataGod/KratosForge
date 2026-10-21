"""Format cell and Format notebook (code_format.py): ruff for Python, the SQL formatter for %%sql."""

from __future__ import annotations

import subprocess

from fabric_desktop import code_format
from fabric_desktop.code_format import format_cells
from fabric_desktop.service import Service


def test_python_is_formatted_in_black_style_and_magics_stay_as_written():
    [r] = format_cells(["import os,sys\nx={ 'a':1 }\n%time y = x+1\n!pip list\ndisplay( x )\n"])
    assert r == {"source": 'import os, sys\n\nx = {"a": 1}\n%time y = x+1\n!pip list\ndisplay(x)', "changed": True, "error": ""}


def test_formatted_code_comes_back_unchanged():
    [r] = format_cells(["x = 1\nprint(x)"])
    assert r == {"source": "x = 1\nprint(x)", "changed": False, "error": ""}


def test_a_cell_python_cant_read_is_left_alone_and_the_others_still_format():
    bad, good = format_cells(["def f( :\n  pass", "y=2"])
    assert bad["source"] == "def f( :\n  pass" and not bad["changed"]
    assert bad["error"].startswith("Not formatted, because Python can't read it (line 1:")
    assert good["source"] == "y = 2"


def test_sql_cells_go_through_the_sql_formatter_and_keep_their_magic():
    a, b = format_cells(["%%sql\nselect  a,b from t where x=1", "%sql\nselect 1"])
    assert a["source"].startswith("%%sql\nSELECT\n") and "WHERE\n  x = 1" in a["source"]
    assert b["source"] == "%sql\nSELECT\n  1"


def test_sql_the_formatter_cant_read_is_left_as_written_with_its_reason():
    [r] = format_cells(["%%sql\nselect from where ((("])
    assert r["source"] == "%%sql\nselect from where (((" and not r["changed"] and r["error"]


def test_other_languages_and_empty_cells_are_left_alone():
    cells = ["%md\n# Title", "%%html\n<b>x</b>", "%sh\nls  -l", "%%bash\necho  1", "", "   \n"]
    assert [r["source"] for r in format_cells(cells)] == cells
    assert not any(r["changed"] or r["error"] for r in format_cells(cells))


def test_settings_files_near_the_engine_dont_change_the_style(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 20\n[tool.ruff.format]\nquote-style = 'single'\n")
    monkeypatch.chdir(tmp_path)
    [r] = format_cells(["x = {'a': 'bbbbbbbbbbbbbbbbbb', 'c': 'dddddddddddd'}"])
    assert r["source"] == 'x = {"a": "bbbbbbbbbbbbbbbbbb", "c": "dddddddddddd"}'


def test_without_ruff_python_cells_say_so_and_sql_still_formats(monkeypatch):
    def missing() -> str:
        raise FileNotFoundError("no ruff")
    monkeypatch.setattr(code_format, "_ruff", missing)
    py, sql = format_cells(["x=1", "%%sql\nselect 1"])
    assert py == {"source": "x=1", "changed": False, "error": "The code formatter (ruff) isn't installed in this engine."}
    assert sql["changed"]


def test_a_formatter_that_hangs_leaves_the_cell_as_it_was(monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired("ruff", 20)
    monkeypatch.setattr(code_format.subprocess, "run", slow)
    [r] = format_cells(["x=1"])
    assert r["source"] == "x=1" and "too long" in r["error"]


def test_the_rpc_takes_a_list_of_sources():
    svc = Service.__new__(Service)
    assert svc.notebook_format(["x=1"])[0]["source"] == "x = 1"
    try:
        svc.notebook_format("x=1")
    except ValueError:
        pass
    else:
        raise AssertionError("a string isn't a list of cells")
