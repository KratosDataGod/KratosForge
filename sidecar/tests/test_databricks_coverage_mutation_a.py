"""databricks_coverage.py paths that its first mutation run (batch 383, docs/quality/mutation-testing.md)
showed no test checked: values as DuckDB types give them, the listed checks and probes, the list and
the page markdown() writes. Run on DuckDB on this PC, or with the example's run replaced."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import databricks_coverage as dc
from fabric_desktop import spark_coverage


@pytest.fixture
def con():
    c = dc.connect()
    yield c
    c.close()


def test_a_struct_keeps_its_fields_in_order_and_a_map_is_sorted(con):
    assert dc.run("SELECT named_struct('b', 1, 'a', array(2.5D, NULL)) AS s", con) == [[{"struct": [["b", 1], ["a", ["float:2.5", None]]]}]]
    assert dc.run("SELECT map('z', named_struct('q', 1), 'y', named_struct('q', 2)) AS m", con) == [
        [{"map": [["y", {"struct": [["q", 2]]}], ["z", {"struct": [["q", 1]]}]]}]]
    assert dc.run("SELECT CAST(NULL AS STRUCT<a: INT>) AS s, array(array(1), NULL) AS l", con) == [[None, [[1], None]]]


def test_only_an_order_free_function_s_lists_compare_sorted(con):
    # an example that names collect_set has its lists sorted; DuckDB's own order would vary from run to run
    assert dc.run("SELECT array(3, 1, 2) AS collect_set", con) == [[[1, 2, 3]]]
    assert dc.run("SELECT array(3, 1, 2) AS xs", con) == [[[3, 1, 2]]]


def test_a_connection_runs_in_utc_with_the_databricks_functions():
    c = dc.connect()
    try:
        assert c.sql("SELECT current_setting('TimeZone')").fetchall() == [("UTC",)]
        assert dc.run("SELECT ip_cidr_contains('192.168.1.0/24', '192.168.1.7')", c) == [[True]]
    finally:
        c.close()


def test_databricks_own_value_is_the_one_checked(monkeypatch, con):
    monkeypatch.setattr(dc, "run", lambda example, c: dc.DATABRICKS_VALUES["schema_of_json"])
    assert dc.check({"name": "schema_of_json", "example": "SELECT 1", "value": [["spark's"]]}, con) == ("runs", "")


def test_a_varying_function_is_not_checked_without_a_listed_check(con):
    assert dc.check({"name": "try_reflect", "example": "SELECT 1", "value": [[1]]}, con) == ("not checked", "")


def test_an_error_is_refused_or_fails_with_its_first_line(monkeypatch, con):
    def raising(exc):
        def run(example, c):
            raise exc
        return run

    f = {"name": "zz_x", "example": "SELECT 1", "value": [[1]]}
    monkeypatch.setattr(dc, "run", raising(NotImplementedError("zz_x can't run on this PC\nmore")))
    assert dc.check(f, con) == ("refused", "zz_x can't run on this PC")
    monkeypatch.setattr(dc, "run", raising(duckdb.Error("Binder Error: " + "y" * 300)))
    assert dc.check(f, con) == ("fails", ("Binder Error: " + "y" * 300)[:160])
    monkeypatch.setattr(dc, "run", raising(RuntimeError("")))
    assert dc.check(f, con) == ("fails", "RuntimeError")


def test_a_differs_note_cuts_both_values_to_80_characters(monkeypatch, con):
    monkeypatch.setattr(dc, "run", lambda example, c: [["a" * 200]])
    status, note = dc.check({"name": "zz_x", "example": "SELECT 1", "value": [["b" * 200]]}, con)
    here, there = note.split(" here, ")
    assert status == "differs" and len(here) == 80 and there == '[["' + "b" * 77 + " in Databricks"


@pytest.fixture
def listed(monkeypatch):
    """A DATABRICKS_CHECKS entry named zz_listed, whose example answers ``answer``."""
    box = {"answer": [[1]]}

    def run(example, c):
        if isinstance(box["answer"], BaseException):
            raise box["answer"]
        return box["answer"]

    monkeypatch.setattr(dc, "run", run)

    def entry(answer, **spec):
        box["answer"] = answer
        monkeypatch.setitem(dc.DATABRICKS_CHECKS, "zz_listed", {"example": "SELECT zz()"} | spec)
        return dc.check({"name": "zz_listed"}, None)
    return entry


def test_a_listed_value_is_compared_sorted_with_its_note(listed):
    assert listed([[1], [2]], value=[[2], [1]], note="n") == ("runs", "n")
    assert listed([[1]], value=[[2]]) == ("differs", "[[1]] here, [[2]] in Databricks")
    assert listed([[1]], value=[[2]], note="known") == ("differs", "known")


def test_a_listed_pattern_must_match_every_value(listed):
    assert listed([["12:00:01", "12:00:02"]], pattern=r"\d\d:\d\d:\d\d") == ("runs", "")
    assert listed([["12:00:01", "noon"]], pattern=r"\d\d:\d\d:\d\d") == ("differs", '[["12:00:01", "noon"]] here, "\\\\d\\\\d:\\\\d\\\\d:\\\\d\\\\d" in Databricks')
    assert listed([], pattern=r".*")[0] == "differs"
    assert listed([["x1"]], pattern=r"x")[0] == "differs"  # the whole value matches, not a part


def test_a_listed_error_must_stop_with_its_text(listed):
    assert listed(duckdb.Error("Invalid Input Error: custom message"), error="custom message", note="n") == ("runs", "n")
    assert listed(duckdb.Error("Invalid Input Error: other"), error="custom message") == ("fails", "Invalid Input Error: other")
    assert listed(NotImplementedError("custom message: not on this PC"), error="custom message") == (
        "refused", "custom message: not on this PC")
    assert listed([[1]], error="custom message") == ("differs", "[[1]] here; Databricks stops with an error")
    assert listed([[1]], error="custom message", note="n") == ("differs", "n")
    assert listed(duckdb.Error("Binder Error: no"), value=[[1]]) == ("fails", "Binder Error: no")


def test_a_probe_tries_up_to_four_null_arguments(monkeypatch):
    tried = []

    def run(code, c):
        tried.append(code)
        if code != "SELECT zz(NULL, NULL, NULL, NULL)":
            raise duckdb.Error(f"Binder Error: no {code}")
        return [[None]]

    monkeypatch.setattr(dc, "run", run)
    assert dc.probe({"name": "zz"}, None) == ("not checked", "")
    assert tried == ["SELECT zz()", "SELECT zz(NULL)", "SELECT zz(NULL, NULL)", "SELECT zz(NULL, NULL, NULL)",
                     "SELECT zz(NULL, NULL, NULL, NULL)"]


def test_a_probe_refused_by_name_says_so(monkeypatch):
    def run(code, c):
        if code == "SELECT zz(NULL)":
            raise NotImplementedError("zz can't run on this PC\nmore")
        raise duckdb.Error("Binder Error: no")

    monkeypatch.setattr(dc, "run", run)
    assert dc.probe({"name": "zz"}, None) == ("refused", "zz can't run on this PC")


def test_a_probe_that_fails_names_the_first_error_but_not_a_missing_argument(monkeypatch):
    def run(code, c):
        raise duckdb.Error("Catalog Error: zz does not exist" if code == "SELECT zz()"
                           else "Parser Error: Required keyword: 'this' missing\nmore")

    monkeypatch.setattr(dc, "run", run)
    assert dc.probe({"name": "zz"}, None) == ("fails", "Parser Error: an argument is missing")
    monkeypatch.setattr(dc, "run", lambda code, c: (_ for _ in ()).throw(duckdb.Error("x does not exist " + "y" * 300)))
    assert dc.probe({"name": "zz", "probe": "SELECT zz"}, None) == ("fails", ("x does not exist " + "y" * 300)[:160])


def test_coverage_lists_spark_s_then_databricks_own_with_their_examples_and_notes(monkeypatch):
    monkeypatch.setattr(dc, "connect", lambda: "con")
    monkeypatch.setattr(spark_coverage, "load", lambda: [{"name": "abs", "group": "math_funcs", "example": "SELECT abs(-1)",
                                                          "value": [[1]]}, {"name": "current_user"}])
    monkeypatch.setattr(dc, "load", lambda: [
        {"name": "zz_valued", "group": "databricks:misc", "example": "SELECT 1", "value": [[1]], "note": "known"},
        {"name": "zz_probed", "group": "databricks:misc", "note": "probe note"},
        {"name": "version", "group": "databricks:misc"}])
    monkeypatch.setattr(dc, "check", lambda f, c: ("runs", "checked " + f["name"] if f["name"] == "abs" else ""))
    monkeypatch.setattr(dc, "probe", lambda f, c: ("not checked", "probed"))
    assert dc.coverage() == [
        {"name": "abs", "group": "math_funcs", "example": "SELECT abs(-1)", "status": "runs", "note": "checked abs"},
        {"name": "current_user", "group": "other", "example": "SELECT current_user()", "status": "runs", "note": ""},
        {"name": "zz_valued", "group": "databricks:misc", "example": "SELECT 1", "status": "runs", "note": "known"},
        {"name": "zz_probed", "group": "databricks:misc", "example": "", "status": "not checked", "note": "probed"},
        {"name": "version", "group": "databricks:misc", "example": "SELECT version()", "status": "runs", "note": ""}]


def test_the_page_counts_each_status_and_puts_databricks_own_groups_last():
    rows = [{"name": "zz", "group": "databricks:ip", "example": "", "status": "fails", "note": "a|b\nc"},
            {"name": "abs", "group": "math_funcs", "example": "SELECT abs(-1)", "status": "runs", "note": ""},
            {"name": "y", "group": "aaa_new_funcs", "example": "SELECT y", "status": "differs", "note": "n"},
            {"name": "w", "group": "databricks:zz", "example": "", "status": "not checked", "note": ""},
            {"name": "avg", "group": "agg_funcs", "example": "SELECT avg(1)", "status": "refused", "note": "r"}]
    lines = dc.markdown(rows).split("\n")
    assert lines[0] == "# Databricks SQL functions on this PC" and lines[-1] == ""
    for line in ("| Runs | 1 |", "| Differs | 1 |", "| Refused | 1 |", "| Fails | 1 |", "| Not checked | 1 |",
                 "| **All** | **5** |"):
        assert line in lines
    assert [line for line in lines if line.startswith("## ")] == [
        "## Aaa new", "## Aggregate", "## Math", "## Databricks only: IP addresses", "## Databricks:zz"]
    at = lines.index("## Databricks only: IP addresses")
    assert lines[at + 1:at + 6] == ["", "| Function | Status | Example | Note |", "| --- | --- | --- | --- |",
                                    "| `zz` | Fails |  | a\\|b c |", ""]
    assert lines[lines.index("## Math") + 4] == "| `abs` | Runs | `SELECT abs(-1)` |  |"


def test_importing_the_module_runs_nothing_and_writes_no_page(monkeypatch):
    import importlib.util

    from fabric_desktop import jsonfile

    def never(*a, **k):
        raise AssertionError("importing databricks_coverage ran the page writer")

    monkeypatch.setattr(jsonfile, "write_text", never)
    monkeypatch.setattr(dc, "connect", never)
    spec = importlib.util.spec_from_file_location("fabric_desktop._databricks_coverage_import_check", dc.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DOC.name == "databricks-functions.md" and module.DOC.parent.name == "docs"
    assert (module.DOC.parent.parent / "sidecar" / "fabric_desktop" / "databricks_coverage.py").is_file()


def test_a_probe_never_tries_five_arguments(monkeypatch):
    tried = []

    def run(code, c):
        tried.append(code)
        if code.count("NULL") != 5:
            raise duckdb.Error("Binder Error: wrong number of arguments")
        return [[None]]

    monkeypatch.setattr(dc, "run", run)
    assert dc.probe({"name": "zz"}, None) == ("fails", "Binder Error: wrong number of arguments")
    assert len(tried) == 5


def test_getdate_and_monotonically_increasing_id_give_databricks_documented_values(con):
    assert dc.check({"name": "getdate"}, con) == ("runs", "")
    assert dc.check({"name": "monotonically_increasing_id"}, con) == (
        "runs", dc.DATABRICKS_CHECKS["monotonically_increasing_id"]["note"])


def test_listed_notes_cut_each_value_to_80_characters(listed):
    status, note = listed([["a" * 200]], value=[["b" * 200]])
    here, there = note.split(" here, ")
    assert status == "differs" and len(here) == 80 and there == '[["' + "b" * 77 + " in Databricks"
    status, note = listed([["a" * 200]], error="custom message")
    assert note == '[["' + "a" * 77 + " here; Databricks stops with an error"


def test_a_probe_refusal_is_cut_to_160_characters(monkeypatch):
    def run(code, c):
        raise NotImplementedError("zz can't run on this PC " + "y" * 300)

    monkeypatch.setattr(dc, "run", run)
    assert dc.probe({"name": "zz"}, None) == ("refused", ("zz can't run on this PC " + "y" * 300)[:160])
