"""academy/pylab.py's checks held to their exact words and cuts: a check it doesn't know, a table file that
isn't there or can't be read (200 characters of why), the name a table is given (40 characters, t_ before a
digit), a config check's own answer, the last line of an error (300 characters), the last 20000 characters
of output, and which exercises may run in an AppContainer. The learner process is stubbed: nothing here
starts a script."""

from __future__ import annotations

import pytest

from fabric_desktop.academy import pylab


def test_a_check_it_doesnt_know_fails_in_words(tmp_path):
    assert pylab.run_check(tmp_path, {"kind": "row_count"}, {}) == {
        "kind": "row_count", "passed": False, "detail": "A Python exercise can't check row_count."}


@pytest.mark.parametrize("path, said", [("missing.parquet", "missing.parquet isn't there."),
                                        ("../x.parquet", "../x.parquet isn't inside the exercise folder.")])
def test_a_table_file_that_isnt_there_or_is_outside_fails_in_words(tmp_path, path, said):
    got = pylab.run_check(tmp_path, {"kind": "schema_matches", "path": path, "columns": [{"name": "a"}]}, {})
    assert got == {"kind": "schema_matches", "passed": False, "detail": said}


def test_a_table_file_duckdb_cant_read_says_why_in_200_characters(tmp_path):
    name = "n" * 150 + ".parquet"
    (tmp_path / name).write_bytes(b"not parquet at all")
    got = pylab.run_check(tmp_path, {"kind": "data_quality", "path": name, "rules": []}, {})
    prefix = f"{name} couldn't be read as parquet: "
    assert got["passed"] is False and got["detail"].startswith(prefix)
    assert len(got["detail"]) - len(prefix) == 200


@pytest.mark.parametrize("path, table", [
    ("out/orders.parquet", "orders"),
    ("out/2024.parquet", "t_2024"),
    ("a1.parquet", "a1"),
    ("o-r d.parquet", "o_r_d"),
    ("x" * 50 + ".parquet", "x" * 40),
])
def test_the_table_is_named_from_the_file(tmp_path, monkeypatch, path, table):
    seen = []
    monkeypatch.setattr(pylab.ws, "load_table", lambda con, work, rel, name, fmt: seen.append(name))
    monkeypatch.setattr(pylab.validators, "schema_matches", lambda con, name, *a: {"table": name})
    assert pylab.run_check(tmp_path, {"kind": "schema_matches", "path": path, "columns": []}, {}) == {"table": table}
    assert seen == [table]


def test_a_config_check_gives_the_validators_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(pylab.validators, "config_file_matches", lambda work, path, rules: {"checked": (path, rules)})
    assert pylab.run_check(tmp_path, {"kind": "config_matches", "path": "c.json", "rules": [1]}, {}) == {
        "checked": ("c.json", [1])}


def test_an_error_is_named_by_its_last_line_cut_at_300_characters():
    run = {"status": "error", "stderr": "Traceback\n  ...\nValueError: " + "v" * 400 + "\n\n", "stdout": ""}
    got = pylab._ran(run, None)
    assert got == {"kind": "notebook_ran", "passed": False,
                   "detail": "Your script stopped with an error: " + ("ValueError: " + "v" * 400)[:300]}


def test_grading_shows_the_last_20000_characters_of_output(tmp_path, monkeypatch):
    out, err = "o" * 5 + "O" * 20_000, "e" * 5 + "E" * 20_000
    monkeypatch.setattr(pylab.sandbox, "run", lambda *a, **k: {
        "status": "ok", "exit_code": 0, "seconds": 0.5, "limits": {}, "runs_as": "test", "stdout": out, "stderr": err})
    got = pylab.grade(tmp_path, "answer.py", [{"kind": "notebook_ran"}], tmp_path)
    assert got["passed"] is True
    assert got["run"]["stdout"] == "O" * 20_000 and got["run"]["stderr"] == "E" * 20_000


@pytest.mark.parametrize("ex, ok", [
    ({"adapter": "delta_lab"}, False),
    ({"adapter": "python_lab", "validation": [{"kind": "delta_table"}]}, False),
    ({"adapter": "python_lab", "validation": [{"kind": "notebook_ran"}]}, True),
])
def test_only_exercises_without_delta_may_run_in_an_appcontainer(ex, ok):
    assert pylab.appcontainer_ok(ex) is ok
