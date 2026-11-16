"""The Snowflake function list's status (snowflake_coverage.py), pinned where mutation testing found
gaps: the arities a probe tries, how a sample's verdict is reached, the case names, and the page's
tables. Nothing here reads docs/: the committed page is compared in test_snowflake_coverage.py."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop import jsonfile, snowflake_sql, snowflake_sql_parity
from fabric_desktop import snowflake_coverage as sc


def probe_with(monkeypatch, answers):
    """Stands in for _try: each call takes the next answer and is recorded as (sql, setup)."""
    tried, calls = iter(answers), []

    def fake(sql, setup=False):
        calls.append((sql, setup))
        return next(tried)

    monkeypatch.setattr(sc, "_try", fake)
    return calls


def case(function, note="", sql="SELECT 1"):
    return SimpleNamespace(function=function, note=note, sql=sql)


def note(id_, title):
    return SimpleNamespace(id=id_, title=title)


def translated(notes=(), runnable=True, refusal="", sql="SELECT 1"):
    return SimpleNamespace(runnable=runnable, duckdb_sql=sql, statements=[SimpleNamespace(notes=list(notes))],
                           first_refusal=lambda: refusal)


# ---------- the list and the case names ----------

def test_the_list_is_read_from_the_json_beside_the_module():
    rows = sc.load()
    assert isinstance(rows, list) and len(rows) > 100
    assert rows[0] == {"name": "ASCII", "group": "String and binary"}


def test_the_page_is_the_repositorys_docs_snowflake_functions_md():
    assert sc.DOC.name == "snowflake-functions.md" and sc.DOC.parent.name == "docs"
    assert sc.DOC.parent.parent / "sidecar" / "fabric_desktop" / "snowflake_coverage.py" == Path(sc.__file__).resolve()


def test_importing_the_module_writes_nothing(monkeypatch):
    writes = []
    monkeypatch.setattr(jsonfile, "write_text", lambda *a, **k: writes.append(a))
    importlib.reload(sc)
    assert writes == []


@pytest.mark.parametrize("function, name", [
    ("TO_DATE (string)", "TO_DATE"), ("to_date (string) (more)", "TO_DATE"), ("abs", "ABS"),
    ("parse_json path", "Path (v:a)"), ("PARSE_JSON PATH (nested)", "Path (v:a)"), ("+", "+"), ("|| (strings)", "||"),
])
def test_a_case_name_drops_the_what_and_upper_cases_a_function(function, name):
    assert sc.case_name(function) == name


@pytest.mark.parametrize("message, first", [
    ("  \nfirst line\nsecond line", "first line"), ("one line", "one line"), ("", "ValueError"), ("   ", "ValueError"),
    ("x" * 200, "x" * 160),
])
def test_an_error_is_noted_by_its_first_line_cut_to_160(message, first):
    assert sc._first_line(ValueError(message)) == first


# ---------- a function with value checks ----------

def test_a_check_that_differs_gives_its_own_note_or_the_value(monkeypatch):
    results = {"A": ("same", 1), "B": ("differs", "x")}
    monkeypatch.setattr(snowflake_sql_parity, "run", lambda c: results[c.function])
    assert sc._from_cases([case("A", note="a's note"), case("B", note="b's note")]) == ("differs", "B: b's note")
    assert sc._from_cases([case("A", note="a's note"), case("B")]) == ("differs", "B: 'x' here")


def test_checks_that_all_run_are_counted_and_their_labels_named(monkeypatch):
    monkeypatch.setattr(snowflake_sql_parity, "run", lambda c: ("same", 1))
    labels = {"one": [note("SF-1", "Rounds"), note("SF-2", "Pads")], "two": [note("SF-1", "Rounds")], "three": []}
    monkeypatch.setattr(snowflake_sql, "translate", lambda sql: translated(labels[sql]))
    assert sc._from_cases([case("A", sql="three")]) == ("runs", "1 value check")
    assert sc._from_cases([case("A", sql="three"), case("B", sql="three")]) == ("runs", "2 value checks")
    assert sc._from_cases([case("A", sql="one"), case("B", sql="two")]) == (
        "runs", "2 value checks; labelled SF-1 Rounds, SF-2 Pads")


def test_a_function_with_cases_takes_its_status_from_them_and_one_without_is_probed(monkeypatch):
    monkeypatch.setattr(snowflake_sql_parity, "run", lambda c: ("error", "Binder Error: no"))
    probe_with(monkeypatch, [("not checked", "")])
    assert sc.check({"name": "F"}, {"F": [case("F")]}) == ("fails", "F: Binder Error: no")
    assert sc.check({"name": "G"}, {"F": [case("F")]}) == ("not checked", "")


# ---------- one sample ----------

def test_a_sample_runs_without_the_two_tables_unless_asked():
    assert sc._try("SELECT 1") == ("not checked", "")
    assert sc._try("SELECT a FROM t") == ("fails", "Catalog Error: Table with name t does not exist!")
    assert sc._try("SELECT a FROM t JOIN s USING (a)", setup=True) == ("not checked", "")


def test_a_sample_duckdb_cant_run_fails_with_its_first_line():
    assert sc._try("SELECT nosuchfn(1)") == ("fails", "Catalog Error: Scalar Function with name nosuchfn does not exist!")


def test_a_sample_sqlglot_cant_read_is_unread_with_the_first_line(monkeypatch):
    def unreadable(sql):
        raise ValueError("Expecting )\nat column 8")
    monkeypatch.setattr(snowflake_sql, "translate", unreadable)
    assert sc._try("SELECT (") == ("unread", "Expecting )")


def test_a_refused_sample_gives_the_reason_without_the_statement_prefix(monkeypatch):
    monkeypatch.setattr(snowflake_sql, "translate", lambda sql: translated(
        runnable=False, refusal="Statement 1 runs only in Snowflake: SYSTEM$WAIT needs Snowflake"))
    assert sc._try("SELECT SYSTEM$WAIT(1)") == ("refused", "SYSTEM$WAIT needs Snowflake")


def test_a_labelled_sample_differs_with_every_label(monkeypatch):
    monkeypatch.setattr(snowflake_sql, "translate", lambda sql: translated([note("SF-1", "Rounds"), note("SF-2", "Pads")]))
    assert sc._try("SELECT 1") == ("differs", "SF-1 Rounds; SF-2 Pads")


# ---------- a function without checks ----------

def test_a_listed_sample_runs_once_on_the_two_tables(monkeypatch):
    calls = probe_with(monkeypatch, [("not checked", "")])
    assert sc._probe({"name": "F", "probe": "SELECT F(a) FROM t"}) == ("not checked", "")
    assert calls == [("SELECT F(a) FROM t", True)]


def test_a_sample_sqlglot_cant_read_fails_and_says_so_and_other_verdicts_pass_through(monkeypatch):
    probe_with(monkeypatch, [("unread", "Expecting )"), ("refused", "needs Snowflake")])
    assert sc._probe({"name": "F", "probe": "SELECT F("}) == ("fails", "sqlglot can't read it: Expecting )")
    assert sc._probe({"name": "F", "probe": "SELECT F(1)"}) == ("refused", "needs Snowflake")


def test_a_probe_tries_zero_to_four_nulls_and_a_window_function_inside_over(monkeypatch):
    calls = probe_with(monkeypatch, [("unread", "first"), ("unread", "second")] + [("unread", "later")] * 3)
    assert sc._probe({"name": "F", "group": "String"}) == ("fails", "sqlglot can't read it: first")
    assert [sql for sql, _ in calls] == ["SELECT F()", "SELECT F(NULL)", "SELECT F(NULL, NULL)",
                                        "SELECT F(NULL, NULL, NULL)", "SELECT F(NULL, NULL, NULL, NULL)"]
    assert all(setup is False for _, setup in calls)
    calls = probe_with(monkeypatch, [("not checked", "")])
    sc._probe({"name": "W", "group": sc.WINDOW_GROUP})
    assert calls == [("SELECT W() OVER (ORDER BY 1)", False)]


@pytest.mark.parametrize("found", [("not checked", ""), ("differs", "SF-1 Rounds")])
def test_a_probe_stops_at_the_first_arity_that_runs(monkeypatch, found):
    calls = probe_with(monkeypatch, [("fails", "Binder Error: no"), found, ("not checked", "")])
    assert sc._probe({"name": "F"}) == found
    assert len(calls) == 2


def test_a_refusal_at_any_arity_wins_over_duckdbs_errors(monkeypatch):
    probe_with(monkeypatch, [("fails", "Binder Error: no"), ("refused", "needs Snowflake")]
               + [("fails", "Binder Error: no")] * 3)
    assert sc._probe({"name": "F"}) == ("refused", "needs Snowflake")


# ---------- the list and its page ----------

def test_the_sample_is_shown_only_for_a_function_without_checks(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: [{"name": "ABS", "group": "Numeric", "probe": "SELECT ABS(a) FROM t"},
                                             {"name": "F", "group": "Numeric", "probe": "SELECT F(a) FROM t"},
                                             {"name": "G", "group": "Numeric"}])
    monkeypatch.setattr(snowflake_sql_parity, "CASES", [case("abs (numbers)")])
    seen = []

    def fake_check(function, cases):
        seen.append(sorted(cases))
        return "runs", function["name"]
    monkeypatch.setattr(sc, "check", fake_check)
    assert sc.coverage() == [
        {"name": "ABS", "group": "Numeric", "sample": "", "status": "runs", "note": "ABS"},
        {"name": "F", "group": "Numeric", "sample": "SELECT F(a) FROM t", "status": "runs", "note": "F"},
        {"name": "G", "group": "Numeric", "sample": "", "status": "runs", "note": "G"},
    ]
    assert seen == [["ABS"]] * 3


def test_a_cell_escapes_pipes_and_joins_lines():
    assert sc._cell("a|b\nc") == "a\\|b c"


def row(name, group, status, sample="", note_=""):
    return {"name": name, "group": group, "status": status, "sample": sample, "note": note_}


def test_the_page_counts_each_status_and_tables_each_group():
    rows = [row("A", "One", "runs"), row("B", "One", "runs", note_="x|y"), row("C", "Two", "fails", sample="SELECT C(1)"),
            row("D", "Two", "not checked"), row("E", "One", "refused")]
    text = sc.markdown(rows)
    lines = text.split("\n")
    counts = lines[lines.index("| Status | Functions |") + 2:lines.index("| **All** | **5** |")]
    assert counts == ["| Runs | 2 |", "| Differs | 0 |", "| Refused | 1 |", "| Fails | 1 |", "| Not checked | 1 |"]
    one = lines.index("## One")
    assert lines[one:one + 7] == ["## One", "", "| Function | Status | Note |", "| --- | --- | --- |",
                                  "| `A` | Runs |  |", "| `B` | Runs | x\\|y |", "| `E` | Refused |  |"]
    two = lines.index("## Two")
    assert lines[two:] == ["## Two", "", "| Name | Status | Sample | Note |", "| --- | --- | --- | --- |",
                           "| `C` | Fails | `SELECT C(1)` |  |", "| `D` | Not checked |  |  |", ""]
    assert one < two and text.startswith("# Snowflake functions on this PC\n")
