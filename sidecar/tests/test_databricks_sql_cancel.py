"""Cancel and the output sink on the Databricks SQL warehouse engine (KUDP hardening): a Cancel
pressed while one statement of a cell runs stops the cell before the next is sent, a Cancel while a
whole result's chunks are being read keeps none of it, and every outcome reaches ``on_output`` as it
reaches the result. Against the fake workspaces in test_databricks_sql.py; nothing reaches a workspace."""

from __future__ import annotations

import test_databricks_sql as t
from fabric_desktop.engines.contract import CellRequest

STATEMENTS = "/api/2.0/sql/statements"


def sent(ws):
    return [c[3]["statement"] for c in ws.calls if c[1] == STATEMENTS and c[0] == "POST"]


def test_a_cancel_during_one_statement_stops_the_cell_before_the_next_is_sent():
    ws = t.FakeWorkspace(polls=2)
    s = t.open_session(ws)
    ws.on_poll = lambda: setattr(s, "_cancelled", True)   # Cancel pressed while the first one runs
    seen = []
    r = s.execute(CellRequest("CREATE TABLE a (i INT); SELECT id, name FROM t", "sql"), on_output=seen.append)
    assert r.status == "cancelled" and r.outputs[-1]["evalue"] == "Cancelled." and seen == r.outputs
    assert sent(ws) == ["CREATE TABLE a (i INT)"]


def test_a_cancel_while_chunks_are_read_keeps_none_of_the_result():
    ws = t.ChunkedWorkspace([t._typed_rows(0, 2), t._typed_rows(2, 2), t._typed_rows(4, 2)], t.TYPED)
    s = t.open_session(ws)
    real = s._client.chunk

    def chunk(statement, n):
        s._cancelled = True                                # Cancel pressed after the first chunk
        return real(statement, n)
    s._client.chunk = chunk
    r = s.execute(CellRequest("SELECT * FROM t", "sql", result_rows=100))
    assert r.status == "cancelled" and r.table is None and r.outputs[-1]["evalue"] == "Cancelled."
    assert sum("/result/chunks/" in c[1] for c in ws.calls) == 1


def test_a_whole_result_reaches_the_sink_as_it_reaches_the_result():
    ws = t.ChunkedWorkspace([t._typed_rows(0, 3), t._typed_rows(3, 2)], t.TYPED)
    seen = []
    r = t.open_session(ws).execute(CellRequest("SELECT * FROM t", "sql", preview_rows=2, result_rows=10),
                                   on_output=seen.append)
    assert r.status == "ok" and r.table.num_rows == 5 and seen == r.outputs
    assert seen[-1]["type"] == "table" and len(seen[-1]["rows"]) == 2


def test_a_whole_result_that_cant_be_typed_reaches_the_sink_as_an_error():
    ws = t.FakeWorkspace(rows=[["1"], ["2", "x"]])
    seen = []
    r = t.open_session(ws).execute(CellRequest("SELECT id, name FROM t", "sql", result_rows=10), on_output=seen.append)
    assert r.status == "error" and r.table is None and seen == r.outputs
    assert "one value per column" in seen[-1]["evalue"]


def test_a_whole_result_with_no_columns_reaches_the_sink_as_an_error():
    seen = []
    r = t.open_session(t.FakeWorkspace()).execute(CellRequest("CREATE TABLE x (i INT)", "sql", result_rows=10),
                                                  on_output=seen.append)
    assert r.status == "error" and seen == r.outputs and "End the cell with a query" in seen[-1]["evalue"]


def test_a_result_over_its_row_limit_reaches_the_sink_as_an_error():
    ws = t.ChunkedWorkspace([t._typed_rows(0, 3), t._typed_rows(3, 3)], t.TYPED)
    seen = []
    r = t.open_session(ws).execute(CellRequest("SELECT * FROM t", "sql", result_rows=4), on_output=seen.append)
    assert r.status == "error" and r.table is None and seen == r.outputs
    assert seen[-1]["ename"] == "ResultTooLarge"
