"""Which built-ins answer in a Databricks SQL cell as in Databricks (databricks_coverage.py), pinned so a
changed limit, check or word fails a test: the listed checks that run, how a status and its note are worked
out (with the example run stood in for), the probe's NULL arguments, the example shown, the page's counts,
titles and table, and where the page is written."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import databricks_coverage as dc


@pytest.fixture(scope="module")
def con():
    c = dc.connect()
    yield c
    c.close()


@pytest.mark.parametrize("name", ["cube", "current_database", "current_schema", "current_timezone", "getdate",
                                  "monotonically_increasing_id", "raise_error"])
def test_the_listed_checks_that_run_here(con, name):
    assert dc._listed(dc.DATABRICKS_CHECKS[name], con)[0] == "runs"


def test_an_order_free_aggregate_is_compared_in_any_order(con):
    assert dc.run("SELECT collect_set(x) FROM VALUES (3), (1), (2) AS t(x)", con) == [[[1, 2, 3]]]


def ran(monkeypatch, result):
    def run(example, con):
        if isinstance(result, BaseException):
            raise result
        return result
    monkeypatch.setattr(dc, "run", run)


def test_a_value_that_differs_is_shown_cut_to_80_characters(monkeypatch):
    ran(monkeypatch, [["x" * 100]])
    status, note = dc.check({"name": "f", "example": "SELECT f()", "value": [["y" * 100]]}, None)
    got, want = json.dumps([["x" * 100]])[:80], json.dumps([["y" * 100]])[:80]
    assert (status, note) == ("differs", f"{got} here, {want} in Databricks")


def test_an_error_is_refused_or_fails_with_its_first_line_cut_to_160(monkeypatch):
    ran(monkeypatch, RuntimeError("a" * 200 + "\nsecond"))
    assert dc.check({"name": "f", "example": "SELECT f()", "value": []}, None) == ("fails", "a" * 160)
    ran(monkeypatch, NotImplementedError("  "))
    assert dc.check({"name": "f", "example": "SELECT f()", "value": []}, None) == ("refused", "NotImplementedError")


def test_a_listed_check_without_a_note_says_what_differs(monkeypatch):
    ran(monkeypatch, [["z" * 100]])
    got = json.dumps([["z" * 100]])[:80]
    assert dc._listed({"example": "e", "error": "boom"}, None) == ("differs", f"{got} here; Databricks stops with an error")
    assert dc._listed({"example": "e", "value": [["v" * 100]]}, None) == (
        "differs", f"{got} here, {json.dumps([['v' * 100]])[:80]} in Databricks")
    assert dc._listed({"example": "e", "pattern": "q+"}, None) == ("differs", f'{got} here, "q+" in Databricks')
    assert dc._listed({"example": "e", "pattern": "z+"}, None) == ("runs", "")
    ran(monkeypatch, [])
    assert dc._listed({"example": "e", "pattern": ".*"}, None)[0] == "differs"  # no value at all isn't a match


def test_a_function_is_probed_with_0_to_4_null_arguments(monkeypatch):
    tried = []

    def run(code, con):
        tried.append(code)
        raise RuntimeError(f"Scalar Function with name f does not exist! {'x' * 200}")
    monkeypatch.setattr(dc, "run", run)
    assert dc.probe({"name": "f"}, None) == ("fails", "DuckDB has no f, and the translation doesn't write it")
    assert tried == ["SELECT f()", "SELECT f(NULL)", "SELECT f(NULL, NULL)", "SELECT f(NULL, NULL, NULL)",
                     "SELECT f(NULL, NULL, NULL, NULL)"]


def test_a_probe_note_is_its_first_line_cut_to_160(monkeypatch):
    monkeypatch.setattr(dc, "run", lambda code, con: (_ for _ in ()).throw(RuntimeError("b" * 200)))
    assert dc.probe({"name": "f", "probe": "SELECT f(x)"}, None) == ("fails", "b" * 160)
    monkeypatch.setattr(dc, "run", lambda code, con: (_ for _ in ()).throw(NotImplementedError("c" * 200 + "\nmore")))
    assert dc.probe({"name": "f"}, None) == ("refused", "c" * 160)


def test_the_example_shown_is_the_listed_one_then_the_functions_own():
    assert dc._example({"name": "getdate", "example": "SELECT other"}) == dc.DATABRICKS_CHECKS["getdate"]["example"]
    assert dc._example({"name": "f", "example": "SELECT f(1)"}) == "SELECT f(1)"
    assert dc._example({"name": "f"}) == ""


def test_the_page_counts_titles_and_rows():
    rows = [{"name": "a|b", "group": "databricks:ip", "example": "SELECT 1", "status": "runs", "note": "x\ny"},
            {"name": "c", "group": "math_funcs", "example": "", "status": "fails", "note": ""},
            {"name": "d", "group": "odd_funcs", "example": "", "status": "fails", "note": ""}]
    page = dc.markdown(rows)
    assert "| Runs | 1 |" in page and "| Fails | 2 |" in page and "| Refused | 0 |" in page
    assert "| **All** | **3** |" in page
    assert page.index("## Math") < page.index("## Odd") < page.index("## Databricks only: IP addresses")
    assert "| `a\\|b` | Runs | `SELECT 1` | x y |" in page and "| `c` | Fails |  |  |" in page


def test_the_page_is_written_to_the_repositorys_docs_folder():
    assert (dc.DOC.parents[1] / "sidecar" / "fabric_desktop" / "databricks_coverage.py").is_file()
    assert dc.DOC.name == "databricks-functions.md" and dc.DOC.parent.name == "docs"


def test_each_group_lists_only_its_own_functions():
    rows = [{"name": "c", "group": "math_funcs", "example": "", "status": "fails", "note": ""},
            {"name": "d", "group": "odd_funcs", "example": "", "status": "runs", "note": ""}]
    math = dc.markdown(rows).split("## Math")[1].split("## ")[0]
    assert "`c`" in math and "`d`" not in math


def test_coverage_lists_spark_functions_then_databricks_own_with_their_groups(monkeypatch):
    from fabric_desktop import spark_coverage
    monkeypatch.setattr(dc, "connect", lambda: None)
    monkeypatch.setattr(spark_coverage, "load", lambda: [{"name": "abs", "group": "math_funcs"}, {"name": "odd"}])
    monkeypatch.setattr(dc, "load", lambda: [{"name": "getdate", "group": "databricks:datetime"},
                                             {"name": "mine", "group": "databricks:misc", "note": "known"}])
    monkeypatch.setattr(dc, "check", lambda f, con: ("runs", ""))
    monkeypatch.setattr(dc, "probe", lambda f, con: ("not checked", ""))
    assert [(r["name"], r["group"], r["status"], r["note"]) for r in dc.coverage()] == [
        ("abs", "math_funcs", "runs", ""), ("odd", "other", "runs", ""),
        ("getdate", "databricks:datetime", "runs", ""), ("mine", "databricks:misc", "not checked", "known")]
