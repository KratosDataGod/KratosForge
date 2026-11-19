"""Kill tests for code_format.py (docs/quality/mutation-testing.md, batch 112).

What ruff is given (a one-cell 4.5 notebook, a 20 s limit), ruff's errors as the cell's reason, a
timeout that changes nothing, an empty %%sql cell, and a %%pyspark cell without ruff.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from fabric_desktop import code_format
from fabric_desktop.code_format import format_cells


def _fake_ruff(monkeypatch, *, returncode=0, stderr="", formatted=None):
    calls = []

    def run(args, **kw):
        calls.append((args, kw))
        nb = json.loads(kw["input"])
        if formatted is not None:
            nb["cells"][0]["source"] = formatted
        return SimpleNamespace(returncode=returncode, stderr=stderr, stdout=json.dumps(nb))

    monkeypatch.setattr(code_format, "_ruff", lambda: "ruff")
    monkeypatch.setattr(code_format, "subprocess", SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired))
    return calls


def test_ruff_gets_the_cell_as_a_one_cell_notebook_with_20_seconds(monkeypatch):
    calls = _fake_ruff(monkeypatch, formatted="x = 1")
    assert format_cells(["x=1"]) == [{"source": "x = 1", "changed": True, "error": ""}]
    [(args, kw)] = calls
    assert args == ["ruff", "format", "--isolated", "--stdin-filename", "cell.ipynb", "-"]
    assert kw["timeout"] == 20
    nb = json.loads(kw["input"])
    assert (nb["nbformat"], nb["nbformat_minor"]) == (4, 5)
    assert nb["cells"] == [{"cell_type": "code", "source": "x=1", "metadata": {}, "outputs": [], "execution_count": None}]


def test_a_parse_error_names_its_line_and_reason(monkeypatch):
    _fake_ruff(monkeypatch, returncode=2, stderr="error: Failed to parse cell.ipynb:cell 1:2:8: Expected ')'\nmore")
    [r] = format_cells(["f(\nx"])
    assert r == {"source": "f(\nx", "changed": False,
                 "error": "Not formatted, because Python can't read it (line 2: Expected ')')."}


def test_another_ruff_error_is_given_as_ruff_words_it(monkeypatch):
    _fake_ruff(monkeypatch, returncode=2, stderr="error: Stopped at 1:2: odd")
    [r] = format_cells(["x=1"])
    assert r["error"] == "Not formatted, because Python can't read it (Stopped at 1:2: odd)."
    _fake_ruff(monkeypatch, returncode=2, stderr="error: cannot parse this")
    [r] = format_cells(["x=1"])
    assert r["error"] == "Not formatted, because Python can't read it (cannot parse this)."


def test_a_formatter_that_hangs_changes_nothing(monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired("ruff", 20)

    monkeypatch.setattr(code_format, "_ruff", lambda: "ruff")
    monkeypatch.setattr(code_format, "subprocess", SimpleNamespace(run=slow, TimeoutExpired=subprocess.TimeoutExpired))
    [r] = format_cells(["x=1"])
    assert r == {"source": "x=1", "changed": False, "error": "Formatting took too long, so the cell was left as it was."}


def test_an_sql_cell_with_nothing_under_its_magic_is_left_alone():
    assert format_cells(["%%sql\n   \n"]) == [{"source": "%%sql\n   \n", "changed": False, "error": ""}]


def test_a_pyspark_cell_without_ruff_says_so(monkeypatch):
    def missing() -> str:
        raise FileNotFoundError("no ruff")

    monkeypatch.setattr(code_format, "_ruff", missing)
    assert format_cells(["%%pyspark\nx=1"]) == [
        {"source": "%%pyspark\nx=1", "changed": False, "error": "The code formatter (ruff) isn't installed in this engine."}]
