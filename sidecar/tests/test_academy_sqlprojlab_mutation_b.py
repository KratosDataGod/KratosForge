"""More sqlproj_lab tests, from a mutation run: the size limits at their edges, the exact words each check
says, and Run's row limit and problem rows. Learner T-SQL only runs through the lab's own build."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop.academy import sqlprojlab
from fabric_desktop.warehouse_project_service import MAX_DEPTH
from tests.test_academy_sqlprojlab import BREAKING, EX, SOLUTION, TABLE, folder  # noqa: F401

MIB = 1024 * 1024
QUERY = "SELECT Segment, Sales FROM dbo.vw_SalesBySegment"


def built(folder, text):
    project, seed = sqlprojlab.load_project(folder[0], EX)
    files = sqlprojlab.apply(project, sqlprojlab.parse(text))
    found, _ = sqlprojlab.objects(files)
    con, report, problems = sqlprojlab.build(files, seed)
    return con, found, problems, project, files, report


def check(folder, c, text=SOLUTION, reference="auto"):
    con, found, problems, project, files, _ = built(folder, text)
    try:
        if reference == "auto":
            reference = sqlprojlab._rows(con, c["query"]) if c.get("query") and not problems else None
        return sqlprojlab._check(c, con, found, problems, project, files, reference)
    finally:
        con.close()


# ---- limits at their edges

def test_a_change_set_of_exactly_one_mib_is_read_and_one_byte_more_is_refused():
    assert sqlprojlab.parse(" " * MIB) == {}
    with pytest.raises(sqlprojlab.Refused, match="over 1 MB"):
        sqlprojlab.parse(" " * (MIB + 1))


def test_the_project_may_hold_500_files_but_not_501():
    many = {f"dbo/Tables/T{i}.sql": f"CREATE TABLE dbo.T{i} (a INT);\n" for i in range(501)}
    five_hundred = dict(list(many.items())[:500])
    assert len(sqlprojlab.apply({}, five_hundred)) == 500
    with pytest.raises(sqlprojlab.Refused, match="more than 500 files"):
        sqlprojlab.apply({}, many)


def test_a_path_may_be_as_deep_as_the_project_layout_allows_and_no_deeper():
    ok = "/".join(["a"] * MAX_DEPTH + ["x.sql"])
    assert sqlprojlab.project_path(ok) == ok
    with pytest.raises(sqlprojlab.Refused):
        sqlprojlab.project_path("/".join(["a"] * (MAX_DEPTH + 1) + ["x.sql"]))


def test_result_rows_stop_at_ten_thousand():
    con = sqlprojlab._connect()
    try:
        con.execute("CREATE TABLE t AS SELECT range AS a FROM range(10001)")
        cols, rows = sqlprojlab._rows(con, "SELECT a FROM t")
    finally:
        con.close()
    assert cols == ["a"] and len(rows) == 10000


# ---- reading the change set

def test_a_line_before_any_file_marker_is_named_from_line_one():
    with pytest.raises(sqlprojlab.Refused) as err:
        sqlprojlab.parse("CREATE TABLE dbo.X (a INT);")
    assert str(err.value).startswith("Line 1: ")


def test_a_file_repeated_after_another_file_is_still_refused():
    with pytest.raises(sqlprojlab.Refused, match="Line 5: a.sql is in the change set twice"):
        sqlprojlab.parse("-- file: a.sql\nx\n-- file: b.sql\ny\n-- file: a.sql\nz")


# ---- building

def test_a_table_the_translator_cant_read_fails_its_build_in_words(folder):
    con, _, problems, _, _, report = built(folder, "-- file: dbo/Tables/T.sql\nCREATE TABLE dbo.T (a INT) WITH (FOO = BAR);\n")
    con.close()
    row = next(r for r in report if r["path"] == "dbo/Tables/T.sql")
    assert row["build"].startswith("failed: ")
    assert any(p.startswith("dbo/Tables/T.sql doesn't build: ") for p in problems)


def test_rows_reports_the_column_names(folder):
    con, *_ = built(folder, SOLUTION)
    try:
        assert sqlprojlab._rows(con, QUERY)[0] == ["Segment", "Sales"]
    finally:
        con.close()


# ---- the checks' words

def test_check_problems_wants_a_list_of_objects():
    assert sqlprojlab.check_problems({"kind": "objects_present", "objects": "abc"}) == ["check objects_present needs objects"]


def test_project_builds_counts_the_other_problems(folder):
    two = check(folder, {"kind": "project_builds"}, SOLUTION + BREAKING)
    assert not two["passed"]
    con, _, problems, *_ = built(folder, SOLUTION + BREAKING)
    con.close()
    assert len(problems) == 2 and two["detail"] == problems[0] + " (1 more)"
    assert check(folder, {"kind": "project_builds"}, TABLE)["passed"]
    con, found, problems, project, files, _ = built(folder, TABLE.replace("FactOrders", "Fact_Orders"))
    try:
        got = sqlprojlab._check({"kind": "project_builds"}, con, found, problems, project, files, None)
    finally:
        con.close()
    assert len(problems) == 1
    assert got == {"kind": "project_builds", "passed": False, "detail": problems[0]}


def test_objects_present_names_one_or_more_missing_columns(folder):
    def cols(*names):
        return {"kind": "objects_present", "objects": [{"kind": "table", "schema": "dbo", "name": "FactOrders",
                                                         "columns": ["OrderKey", *names]}]}
    assert check(folder, cols())["passed"]
    assert check(folder, cols("Nope"))["detail"] == "table dbo.FactOrders has no column Nope."
    assert check(folder, cols("Nope", "Zip"))["detail"] == "table dbo.FactOrders has no columns Nope, Zip."


def test_objects_present_says_when_a_view_doesnt_build(folder):
    c = {"kind": "objects_present", "objects": [{"kind": "view", "schema": "dbo", "name": "vw_CustomersBySegment",
                                                 "columns": ["Segment"]}]}
    got = check(folder, c, SOLUTION + BREAKING, reference=None)
    assert got == {"kind": "objects_present", "passed": False,
                   "detail": "view dbo.vw_CustomersBySegment doesn't build, so its columns can't be read."}


def test_deploy_safe_names_what_the_rebuild_changes(folder):
    got = check(folder, {"kind": "deploy_safe"}, SOLUTION + BREAKING, reference=None)
    assert ("Deploying would rebuild table dbo.DimCustomer: column CustomerSegment is only in the project, "
            "column Segment is only in the warehouse. ") in got["detail"]


def test_result_matches_without_a_reference_says_the_exercise_needs_fixing(folder):
    got = check(folder, {"kind": "result_matches", "query": QUERY}, reference=None)
    assert got["detail"] == "The reference project doesn't build; this exercise needs fixing."


def test_result_matches_gives_the_querys_own_error(folder):
    q = "SELECT Nope FROM dbo.vw_SalesBySegment"
    con, *_ = built(folder, SOLUTION)
    try:
        with pytest.raises(duckdb.Error) as err:
            con.execute('SELECT Nope FROM "dbo"."vw_SalesBySegment"')
    finally:
        con.close()
    got = check(folder, {"kind": "result_matches", "query": q}, reference=(["Segment", "Sales"], []))
    assert got["detail"] == "The check's query failed on your project: " + str(err.value).split("\n")[0]


def test_result_matches_names_the_columns_it_expected(folder):
    got = check(folder, {"kind": "result_matches", "query": QUERY}, reference=(["Other"], []))
    assert got["detail"] == "The query returns columns Segment, Sales; expected Other."


def test_result_matches_in_order_compares_the_order(folder):
    con, *_ = built(folder, SOLUTION)
    try:
        ref = sqlprojlab._rows(con, QUERY + " ORDER BY Segment")
    finally:
        con.close()
    c = {"kind": "result_matches", "query": QUERY + " ORDER BY Segment", "ordered": True}
    assert check(folder, c, reference=ref)["passed"]
    flipped = (ref[0], list(reversed(ref[1])))
    assert len(ref[1]) == 2 and not check(folder, c, reference=flipped)["passed"]
    assert check(folder, {**c, "ordered": False}, reference=flipped)["passed"]


def test_an_unknown_check_kind_fails_in_words(folder):
    assert check(folder, {"kind": "nope"}, reference=None) == {
        "kind": "nope", "passed": False, "detail": "A database project exercise can't check nope."}


def test_the_reference_is_only_built_for_result_matches(tmp_path):
    ex = {**EX, "validation": [{"kind": "project_builds"}]}
    assert sqlprojlab._reference(tmp_path, tmp_path / "no-grading", ex) == {}


# ---- Run

def test_run_shows_at_most_limit_rows_and_says_there_are_more(folder):
    many = "".join(f"-- file: dbo/Tables/T{i}.sql\nCREATE TABLE dbo.T{i} (a INT);\n" for i in range(200))
    got = sqlprojlab.run(folder[0], EX, many)
    assert len(got["rows"]) == 200 and got["more"] is True
    total = len(sqlprojlab.run(folder[0], EX, many, limit=10_000)["rows"])
    assert total > 200
    exact = sqlprojlab.run(folder[0], EX, many, limit=total)
    assert len(exact["rows"]) == total and exact["more"] is False
    assert sqlprojlab.run(folder[0], EX, many, limit=total - 1)["more"] is True


def test_run_lists_only_problems_that_dont_belong_to_a_file(folder):
    twice = ("-- file: dbo/Tables/X.sql\nCREATE TABLE dbo.X (a INT);\n"
             "-- file: dbo/Tables/Y.sql\nCREATE TABLE dbo.X (a INT);\n")
    got = sqlprojlab.run(folder[0], EX, BREAKING + twice)
    listed = [r[2] for r in got["rows"] if r[0] == "(problem)"]
    assert listed == ["table dbo.X is defined twice: dbo/Tables/X.sql and dbo/Tables/Y.sql."]

