"""The Databricks SQL statement client when the warehouse answers awkwardly: results sent as download
links, chunks that don't add up, Retry-After on 429 and 503, a statement that never leaves PENDING, a
statement cancelled elsewhere, and replies that aren't what the API documents. Against the fakes in
test_databricks_sql.py; nothing reaches a workspace, and every failure is one plain sentence or two."""

from __future__ import annotations

import pytest

import test_databricks_sql as t
from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines.contract import CellRequest, SessionConfig

STATEMENTS = "/api/2.0/sql/statements"


class HeaderResp(t.Resp):
    def __init__(self, status=200, body=None, headers=None):
        super().__init__(status, body)
        self.headers = headers or {}


def _answer_first(ws, answers, when):
    """The next calls matching ``when(method, path)`` get ``answers`` before the fake's own answer."""
    real, left = ws.request, list(answers)

    def request(method, url, **kw):
        path = url[len(t.HOST):]
        if left and when(method, path):
            ws.calls.append((method, path, None, None, None))
            return left.pop(0)
        return real(method, url, **kw)
    ws.request = request


def _edit_done(ws, change):
    """``change`` edits each finished statement's reply in place."""
    real = ws._done

    def done(sid, statement):
        body = real(sid, statement)
        change(body)
        return body
    ws._done = done


def _session(ws, sleeps=None, clock=None, max_seconds=dbsql.MAX_SECONDS):
    sleep = (lambda s: sleeps.append(s)) if sleeps is not None else (lambda s: None)
    client = dbsql.Client(t.tokens(), ws, sleep=sleep)
    if clock is None:
        return dbsql.DatabricksSqlSession(SessionConfig("nb"), client, "wh1", sleep=lambda s: None)
    return dbsql.DatabricksSqlSession(SessionConfig("nb"), client, "wh1", sleep=lambda s: clock.advance(),
                                      clock=clock, max_seconds=max_seconds)


class Clock:
    def __init__(self, step=60.0):
        self.now, self.step = 0.0, step

    def __call__(self):
        return self.now

    def advance(self):
        self.now += self.step


# ---------------------------------------------------------------- results as download links


def test_a_preview_sent_as_download_links_is_a_plain_error_never_an_empty_table():
    ws = t.FakeWorkspace()
    _edit_done(ws, lambda d: d.get("result") and d.update(result={"chunk_index": 0, "external_links": [
        {"chunk_index": 0, "external_link": "https://storage.example/x?sig=abc"}]}))
    seen = []
    r = t.open_session(ws).execute(CellRequest("SELECT id, name FROM t", "sql"), on_output=seen.append)
    assert r.status == "error" and seen == r.outputs
    assert "download links" in r.outputs[-1]["evalue"] and "LIMIT" in r.outputs[-1]["evalue"]
    assert "sig=abc" not in r.outputs[-1]["evalue"]


def test_a_later_chunk_sent_as_download_links_keeps_none_of_the_result():
    ws = t.ChunkedWorkspace([t._typed_rows(0, 2), t._typed_rows(2, 2)], t.TYPED)
    real = ws._page
    ws._page = lambda n, limit: {"chunk_index": n, "external_links": [{"external_link": "https://x"}]} if n \
        else real(n, limit)
    r = t.open_session(ws).execute(CellRequest("SELECT * FROM t", "sql", result_rows=10))
    assert r.status == "error" and r.table is None and "download links" in r.outputs[-1]["evalue"]


# ---------------------------------------------------------------- chunks that don't add up


def test_a_whole_result_shorter_than_the_manifest_says_keeps_none_of_it():
    ws = t.ChunkedWorkspace([t._typed_rows(0, 2), t._typed_rows(2, 2)], t.TYPED)
    real = ws._page
    ws._page = lambda n, limit: {**real(n, limit), "data_array": []} if n == 1 else real(n, limit)
    r = t.open_session(ws).execute(CellRequest("SELECT * FROM t", "sql", result_rows=10))
    assert r.status == "error" and r.table is None
    assert r.outputs[-1]["evalue"].startswith("The warehouse said the result has 4 rows but sent 2")


def test_a_chunk_whose_row_count_doesnt_match_its_rows_is_an_error():
    ws = t.ChunkedWorkspace([t._typed_rows(0, 2), t._typed_rows(2, 2)], t.TYPED)
    real = ws._page
    ws._page = lambda n, limit: {**real(n, limit), "row_count": 5}
    r = t.open_session(ws).execute(CellRequest("SELECT * FROM t", "sql", result_rows=10))
    assert r.status == "error" and "holds 5 rows but sent 2" in r.outputs[-1]["evalue"]


def test_a_chunk_that_repeats_rows_is_an_error():
    ws = t.ChunkedWorkspace([t._typed_rows(0, 2), t._typed_rows(2, 2)], t.TYPED)
    real = ws._page
    ws._page = lambda n, limit: {**real(n, limit), "row_offset": 0}   # chunk 1 says it starts at row 0
    r = t.open_session(ws).execute(CellRequest("SELECT * FROM t", "sql", result_rows=10))
    assert r.status == "error" and "skip or repeat rows" in r.outputs[-1]["evalue"]


def test_chunks_with_matching_offsets_and_counts_read_whole():
    ws = t.ChunkedWorkspace([t._typed_rows(0, 3), t._typed_rows(3, 2), t._typed_rows(5, 1)], t.TYPED)
    real = ws._page

    def page(n, limit):
        p = real(n, limit)
        return {**p, "row_offset": sum(len(c) for c in ws.chunks[:n]), "row_count": len(p["data_array"])}
    ws._page = page
    r = t.open_session(ws).execute(CellRequest("SELECT * FROM t", "sql", result_rows=10))
    assert r.status == "ok" and r.table.column("id").to_pylist() == list(range(6))
    assert [p for _, p, *_ in ws.calls if "/result/chunks/" in p] == [
        f"{STATEMENTS}/s1/result/chunks/1", f"{STATEMENTS}/s1/result/chunks/2"]


# ---------------------------------------------------------------- Retry-After


@pytest.mark.parametrize("status", [429, 503])
def test_a_poll_waits_as_retry_after_says_then_finishes(status):
    ws = t.FakeWorkspace(polls=2)
    sleeps = []
    _answer_first(ws, [HeaderResp(status, {"message": "busy"}, {"Retry-After": "7"})],
                  lambda m, p: m == "GET" and p.startswith(STATEMENTS + "/"))
    r = _session(ws, sleeps).execute(CellRequest("SELECT 1", "sql"))
    assert r.status == "ok" and ws.cancelled == []
    assert sum(sleeps) == pytest.approx(7)


def test_a_submit_waits_as_retry_after_says_after_a_429_and_runs_once():
    ws = t.FakeWorkspace()
    sleeps = []
    s = _session(ws, sleeps)
    _answer_first(ws, [HeaderResp(429, {"message": "busy"}, {"Retry-After": "3"})],
                  lambda m, p: m == "POST" and p == STATEMENTS)
    assert s.execute(CellRequest("SELECT 1", "sql")).status == "ok" and ws.n == 1
    assert sum(sleeps) == pytest.approx(3)


def test_a_retry_after_longer_than_the_wait_cap_gives_up_with_a_plain_message():
    ws = t.FakeWorkspace(polls=2)
    sleeps = []
    _answer_first(ws, [HeaderResp(503, {"message": "busy"}, {"Retry-After": "3600"})] * 4,
                  lambda m, p: m == "GET" and p.startswith(STATEMENTS + "/"))
    r = _session(ws, sleeps).execute(CellRequest("SELECT 1", "sql"))
    evalue = r.outputs[-1]["evalue"]
    assert r.status == "error" and evalue.startswith("Databricks refused the request (HTTP 503).")
    assert "was stopped on Shared" in evalue and ws.cancelled == ["s1"]
    assert sum(sleeps) <= 30


# ---------------------------------------------------------------- deadlines


class PendingWorkspace(t.FakeWorkspace):
    """The statement stays PENDING (a warehouse starting, or queued behind others) until cancelled."""

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        resp = super().request(method, url, headers, timeout, json, params)
        body = resp.json()
        if (body.get("status") or {}).get("state") == "RUNNING":
            body["status"]["state"] = "PENDING"
        return resp


def test_a_statement_still_pending_at_the_deadline_is_cancelled_and_says_it_never_started():
    ws = PendingWorkspace(polls=1000)
    r = _session(ws, clock=Clock(), max_seconds=300).execute(CellRequest("SELECT 1", "sql"))
    evalue = r.outputs[-1]["evalue"]
    assert r.status == "error" and ws.cancelled == ["s1"]
    assert evalue.startswith("The statement was still waiting to start after 5 minutes")
    assert evalue.endswith("The statement was stopped on Shared.")


def test_a_statement_running_at_the_deadline_still_says_running():
    ws = t.FakeWorkspace(polls=1000)
    r = _session(ws, clock=Clock(), max_seconds=300).execute(CellRequest("SELECT 1", "sql"))
    assert r.outputs[-1]["evalue"].startswith("The statement was still running after 5 minutes.")


def test_a_cancel_on_timeout_is_sent_once_and_the_cell_reports_an_error_not_a_cancel():
    ws = PendingWorkspace(polls=1000)
    r = _session(ws, clock=Clock(), max_seconds=120).execute(CellRequest("SELECT 1", "sql"))
    cancels = [p for m, p, *_ in ws.calls if p.endswith("/cancel")]
    assert r.status == "error" and cancels == [f"{STATEMENTS}/s1/cancel"]


# ---------------------------------------------------------------- states this PC didn't cause


def test_a_statement_cancelled_in_databricks_says_it_wasnt_cancelled_here():
    ws = t.FakeWorkspace()
    _edit_done(ws, lambda d: d.update(status={"state": "CANCELED"}))
    r = t.open_session(ws).execute(CellRequest("SELECT 1", "sql"))
    assert r.status == "error" and "cancelled on the warehouse, not from here" in r.outputs[-1]["evalue"]


def test_a_closed_statement_says_to_run_the_cell_again():
    ws = t.FakeWorkspace()
    _edit_done(ws, lambda d: d.update(status={"state": "CLOSED"}))
    r = t.open_session(ws).execute(CellRequest("SELECT 1", "sql"))
    assert r.status == "error" and r.outputs[-1]["evalue"].startswith("The warehouse closed the statement")


# ---------------------------------------------------------------- replies the API doesn't document


def test_a_running_statement_without_an_id_says_it_cant_be_followed():
    ws = t.FakeWorkspace()
    _answer_first(ws, [t.Resp(200, {"status": {"state": "PENDING"}})], lambda m, p: m == "POST" and p == STATEMENTS)
    r = t.open_session(ws).execute(CellRequest("SELECT 1", "sql"))
    assert r.status == "error" and "sent no statement ID" in r.outputs[-1]["evalue"]
    assert not any(p.endswith("/cancel") for _, p, *_ in ws.calls)


@pytest.mark.parametrize("body", [[1, 2], "ok", 3])
def test_a_reply_that_isnt_a_json_object_is_a_plain_error(body):
    ws = t.FakeWorkspace(polls=2)
    s = t.open_session(ws)
    _answer_first(ws, [t.Resp(200, body)], lambda m, p: m == "GET" and p.startswith(STATEMENTS + "/"))
    r = s.execute(CellRequest("SELECT 1", "sql"))
    assert r.status == "error" and "isn't an object" in r.outputs[-1]["evalue"] and ws.cancelled == ["s1"]


def test_a_poll_whose_status_isnt_an_object_is_polled_again():
    ws = t.FakeWorkspace(polls=2)
    s = t.open_session(ws)
    _answer_first(ws, [t.Resp(200, {"statement_id": "s1", "status": "RUNNING"})],
                  lambda m, p: m == "GET" and p.startswith(STATEMENTS + "/"))
    assert s.execute(CellRequest("SELECT 1", "sql")).status == "ok"


@pytest.mark.parametrize("answer", [t.Resp(503, {"message": "down"}), ConnectionError("reset")], ids=["5xx", "dropped"])
def test_a_submit_that_may_have_started_says_to_check_before_running_again(answer):
    ws = t.FakeWorkspace()
    s = t.open_session(ws)
    real = ws.request

    def request(method, url, **kw):
        if method == "POST" and url.endswith(STATEMENTS):
            ws.calls.append((method, STATEMENTS, None, None, None))
            if isinstance(answer, Exception):
                raise answer
            return answer
        return real(method, url, **kw)
    ws.request = request
    r = s.execute(CellRequest("INSERT INTO t VALUES (1)", "sql"))
    evalue = r.outputs[-1]["evalue"]
    assert r.status == "error" and "may have started" in evalue and "query history" in evalue
    assert sum(1 for m, p, *_ in ws.calls if m == "POST" and p == STATEMENTS) == 1


def test_a_submit_refused_unread_doesnt_say_it_may_have_started():
    ws = t.FakeWorkspace()
    s = t.open_session(ws)
    _answer_first(ws, [t.Resp(403, {"message": "no"})], lambda m, p: m == "POST" and p == STATEMENTS)
    evalue = s.execute(CellRequest("SELECT 1", "sql")).outputs[-1]["evalue"]
    assert "HTTP 403" in evalue and "may have started" not in evalue


def test_refusals_carry_their_status_and_never_a_token():
    ws = t.FakeWorkspace(refuse=403)
    with pytest.raises(dbsql.DatabricksError) as err:
        dbsql.Client(t.tokens(), ws, sleep=lambda s: None).warehouse("wh1")
    assert err.value.status == 403 and t.PAT not in str(err.value)


def _connect_failures():
    import requests
    import urllib3.exceptions as u3
    refused = requests.ConnectionError(u3.MaxRetryError(None, "/", u3.NewConnectionError(None, "refused")))
    lookup = requests.ConnectionError(u3.MaxRetryError(None, "/", u3.NameResolutionError("adb", None, OSError())))
    return [refused, lookup, requests.ConnectTimeout("connect timed out"), ConnectionRefusedError()]


@pytest.mark.parametrize("failure", _connect_failures(), ids=["refused", "lookup", "connect-timeout", "os-refused"])
def test_a_submit_whose_connection_never_opened_says_nothing_was_sent(failure):
    ws = t.FakeWorkspace()
    s = t.open_session(ws)
    real = ws.request

    def request(method, url, **kw):
        if method == "POST" and url.endswith(STATEMENTS):
            raise failure
        return real(method, url, **kw)
    ws.request = request
    evalue = s.execute(CellRequest("INSERT INTO t VALUES (1)", "sql")).outputs[-1]["evalue"]
    assert "Nothing was sent" in evalue and "may have started" not in evalue


def test_a_connection_reset_after_sending_still_says_it_may_have_started():
    import requests
    import urllib3.exceptions as u3
    reset = requests.ConnectionError(u3.ProtocolError("Connection aborted.", ConnectionResetError()))
    assert dbsql._never_sent(reset) is False and dbsql._never_sent(requests.ReadTimeout()) is False
