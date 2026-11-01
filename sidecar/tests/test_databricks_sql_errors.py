"""The Databricks SQL warehouse engine's error paths (KUDP hardening), against the fake workspace in
test_databricks_sql.py: nothing reaches a workspace. Comment-only SQL is never sent, so it never wakes a
stopped warehouse."""

from __future__ import annotations

import pytest

import test_databricks_sql as t
from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines.contract import CellRequest, Unsupported

STATEMENTS = "/api/2.0/sql/statements"


def sent(ws):
    return [c[3]["statement"] for c in ws.calls if c[1] == STATEMENTS and c[0] == "POST"]


# ---------------------------------------------------------------- splitting


@pytest.mark.parametrize("code, statements", [
    ("SELECT 1; -- done", ["SELECT 1"]),
    ("SELECT 1;\n/* the end */", ["SELECT 1"]),
    ("-- a note\n;\nSELECT 2", ["SELECT 2"]),
    ("-- lead\nSELECT 1", ["-- lead\nSELECT 1"]),
    ("SELECT ';' AS s; SELECT 2", ["SELECT ';' AS s", "SELECT 2"]),
    ("SELECT 1 -- why\n; SELECT 2", ["SELECT 1 -- why", "SELECT 2"]),
    ("SELECT 'not closed", ["SELECT 'not closed"]),     # sqlglot can't read it: sent whole, the warehouse says why
    (" ; ; ", []),
])
def test_statements_are_split_and_comment_only_parts_left_out(code, statements):
    assert dbsql.split_statements(code) == statements


@pytest.mark.parametrize("code", ["-- just a note", "/* nothing */ ;", "-- a\n-- b\n;"])
def test_a_cell_that_is_only_comments_is_refused_and_sends_nothing(code):
    ws = t.FakeWorkspace()
    s = t.open_session(ws)
    with pytest.raises(Unsupported, match="no SQL to run"):
        s.execute(CellRequest(code, "sql"))
    assert sent(ws) == []


def test_a_trailing_comment_neither_runs_nor_takes_the_place_of_the_last_query():
    ws = t.FakeWorkspace()
    r = t.open_session(ws).execute(CellRequest("SELECT id, name FROM t; -- that's all", "sql", result_rows=10))
    assert r.status == "ok" and r.table is not None and r.table.num_rows == 2
    assert sent(ws) == ["SELECT id, name FROM t"]


# ---------------------------------------------------------------- replies


class _NotJson(t.Resp):
    def __init__(self, status):
        super().__init__(status)
        self.content = b"<html>Gateway</html>"

    def json(self):
        raise ValueError("not JSON")


def test_a_reply_that_isnt_json_says_which_host_and_status():
    ws = t.FakeWorkspace()
    ws.request = lambda *a, **k: _NotJson(200)
    with pytest.raises(dbsql.DatabricksError, match="something other than JSON \\(HTTP 200\\)"):
        dbsql.Client(t.tokens(), ws, sleep=lambda s: None).cluster("0123-456789-abcdef")


def test_a_refusal_that_isnt_json_still_gives_the_hint_and_no_page_text():
    ws = t.FakeWorkspace()
    ws.request = lambda *a, **k: _NotJson(403)
    with pytest.raises(dbsql.DatabricksError, match="HTTP 403") as exc:
        dbsql.Client(t.tokens(), ws, sleep=lambda s: None).cluster("0123-456789-abcdef")
    assert "Gateway" not in str(exc.value)


@pytest.mark.parametrize("call, value", [
    ("cluster", "../clusters/list"), ("terminate_cluster", "x?all=1"), ("stop_warehouse", "wh1/../../other"),
])
def test_ids_that_arent_ids_are_refused_before_any_call(call, value):
    ws = t.FakeWorkspace()
    with pytest.raises(dbsql.DatabricksError, match="isn't a Databricks"):
        getattr(dbsql.Client(t.tokens(), ws, sleep=lambda s: None), call)(value)
    assert ws.calls == []


def test_a_whole_result_whose_rows_dont_match_the_columns_is_an_error_never_a_table():
    ws = t.FakeWorkspace(rows=[["1"], ["2", "x"]])
    r = t.open_session(ws).execute(CellRequest("SELECT id, name FROM t", "sql", result_rows=10))
    assert r.status == "error" and r.table is None and "one value per column" in r.outputs[-1]["evalue"]


def test_a_whole_result_needs_the_cell_to_end_with_a_query():
    ws = t.FakeWorkspace()
    r = t.open_session(ws).execute(CellRequest("CREATE TABLE x (i INT)", "sql", result_rows=10))
    assert r.status == "error" and r.table is None and "End the cell with a query" in r.outputs[-1]["evalue"]


def test_a_result_the_warehouse_cut_short_says_so():
    ws = t.FakeWorkspace()
    real = ws._done
    ws._done = lambda sid, statement: (lambda d: d["manifest"].update(truncated=True) or d)(real(sid, statement))
    r = t.open_session(ws).execute(CellRequest("SELECT id, name FROM t", "sql"))
    assert r.status == "ok" and r.outputs[-1]["truncated_by_warehouse"] is True


# ---------------------------------------------------------------- the session


def test_a_second_cell_while_one_runs_is_refused_and_sends_nothing():
    ws = t.FakeWorkspace()
    s = t.open_session(ws)
    assert s._lock.acquire(blocking=False)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            s.execute(CellRequest("SELECT 1", "sql"))
    finally:
        s._lock.release()
    assert sent(ws) == []


def test_a_cancel_the_warehouse_refuses_says_it_didnt_cancel():
    ws = t.FakeWorkspace()
    s = t.open_session(ws)
    s._statement = "s9"
    assert s._lock.acquire(blocking=False)
    try:
        ws.refuse = 500
        assert s.cancel() is False
    finally:
        s._lock.release()
        s._statement = ""
