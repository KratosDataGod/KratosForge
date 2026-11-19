"""The Databricks live test's checks (dbx_livetest.py) when the workspace or a session answers wrongly. Every
client, token provider and session here is a stand-in: nothing reaches Databricks or the network."""

from __future__ import annotations

import subprocess
import threading
from types import SimpleNamespace

import pytest

from fabric_desktop import dbx_livetest as dl
from fabric_desktop.engines.contract import CellResult
from fabric_desktop.engines.databricks_sql import DatabricksError

TAGS = {"custom_tags": [{"key": dl.MARKER, "value": ""}]}
NOT_FOUND = DatabricksError("HTTP 404: no such warehouse", 404)


class Rest:
    """The plain REST calls, answered from what the test sets. A missing warehouse fails as ``missing``."""

    def __init__(self, warehouse=None, cluster=None, missing=NOT_FOUND,
                 fail_stop=None):
        self.warehouse_info = {"name": "kf tests", "state": "RUNNING", "auto_stop_mins": 5, "tags": TAGS} \
            if warehouse is None else warehouse
        self.cluster_info = cluster
        self.missing, self.fail_stop = missing, fail_stop
        self.calls = []

    def warehouse(self, wid):
        self.calls.append(("warehouse", wid))
        if wid == "wh1":
            if isinstance(self.warehouse_info, Exception):
                raise self.warehouse_info
            return self.warehouse_info
        if self.missing is None:
            return {"id": wid}
        raise self.missing

    def cluster(self, cid):
        if isinstance(self.cluster_info, Exception):
            raise self.cluster_info
        return self.cluster_info

    def stop_warehouse(self, wid):
        if self.fail_stop:
            raise self.fail_stop
        self.calls.append(("stop", wid))

    def terminate_cluster(self, cid):
        self.calls.append(("terminate", cid))


class BadToken:
    def __init__(self, error=None):
        self.error = error

    def warehouse(self, wid):
        if self.error is None:
            return {"id": wid}
        raise self.error


def make(tmp_path, rest=None, token="tok", sql=None, **kw):
    tokens = SimpleNamespace(token=lambda: token, method="databricks-cli")
    kw.setdefault("bad_token_client", BadToken(DatabricksError("HTTP 401: Invalid access token.", 401)))
    return dl.DatabricksLiveTest({"host": "https://adb-1.2.azuredatabricks.net"}, "wh1", tokens, rest or Rest(),
                                 sql, tmp_path, run_id="r1", clock=lambda: 0.0, sleep=lambda s: None, **kw)


def result(status, rows=None, error=None, text=None, seconds=1.0):
    outputs = ([{"type": "error", "evalue": error}] if error else []) + ([{"type": "text", "text": text}] if text else [])
    return CellResult(status, outputs, seconds, rows)


class Session:
    def __init__(self, answers=None, catalogs=(), cancels=True, closes=True, proc=None):
        self.answers = answers or {}
        self.catalogs = list(catalogs)
        self.cancels, self.closes = cancels, closes
        self.closed = False
        self.proc = proc
        self.warehouse_name, self.warehouse_state = "kf tests", ""
        self.cancelled = threading.Event()

    def catalog(self):
        return self.catalogs

    def execute(self, request, on_output=None):
        if request.code == dl.SLOW_SQL and self.cancels:
            assert self.cancelled.wait(10), "never cancelled"  # the slow statement runs until the cancel
        answer = self.answers.get(request.code) or self.answers.get("*")
        return answer() if callable(answer) else answer

    def cancel(self):
        if self.cancels:
            self.cancelled.set()
        return self.cancels

    def close(self):
        self.closed = self.closes


def checks(test):
    return {c.id: (c.status, c.detail) for c in test.checks}


# ---- the run stops early


def test_an_empty_token_stops_the_run_before_any_call(tmp_path):
    rest = Rest()
    out = make(tmp_path, rest, token="").run()
    assert out["outcome"] == "failed" and out["checks"][0]["detail"] == "RuntimeError: Sign-in returned an empty token"
    assert rest.calls == [] and len(out["checks"]) == 1


def test_a_warehouse_that_cannot_be_read_stops_the_run(tmp_path):
    out = make(tmp_path, Rest(warehouse=DatabricksError("HTTP 403: not allowed", 403))).run()
    assert [(c["id"], c["status"]) for c in out["checks"]] == [("auth.token", "passed"), ("dbx.warehouse", "failed")]
    assert "HTTP 403: not allowed" in out["checks"][1]["detail"] and out["refused"] == []


def test_a_cluster_that_cannot_be_read_stops_the_run(tmp_path):
    out = make(tmp_path, Rest(cluster=DatabricksError("HTTP 404: no cluster", 404)), cluster_id="c1").run()
    assert [c["id"] for c in out["checks"]] == ["auth.token", "dbx.warehouse", "dbx.cluster"]
    assert out["outcome"] == "failed" and out["cluster"]["id"] == "c1"


def test_a_running_warehouse_is_not_one_the_run_started(tmp_path):
    test = make(tmp_path)
    detail = test._read_warehouse()
    assert test.started_by_run == []
    assert detail == "kf tests: ?, classic, RUNNING, auto-stop 5 min."


def test_the_plan_with_stop_compute_says_it_stops_only_what_was_stopped(tmp_path):
    steps = make(tmp_path, stop_compute=True).plan()
    assert any(s.startswith("Stop the warehouse and terminate the cluster, but only those") for s in steps)
    assert not any("stop on their own" in s for s in steps)


# ---- the SQL checks


class Adapter:
    def __init__(self, session=None, fail=None):
        self.session, self.fail = session, fail

    def open_session(self, config, authorization=None):
        if self.fail:
            raise self.fail
        return self.session


def test_a_sql_session_that_will_not_open_skips_the_checks_that_need_it(tmp_path):
    test = make(tmp_path, sql=Adapter(fail=RuntimeError("warehouse refused the session")))
    test._sql_checks()
    got = checks(test)
    assert got["sql.open"] == ("failed", "RuntimeError: warehouse refused the session")
    assert {k: v[0] for k, v in got.items() if k != "sql.open"} == {
        "uc.catalogs": "skipped", "sql.query": "skipped", "sql.error": "skipped", "sql.cancel": "skipped"}
    assert "sql.close" not in got


def test_each_wrong_answer_from_the_warehouse_fails_its_own_check(tmp_path):
    session = Session(answers={"*": result("ok", rows=3), dl.SLOW_SQL: result("ok", rows=1)}, catalogs=[{"catalog": ""}],
                      closes=False)
    test = make(tmp_path, sql=Adapter(session))
    test._sql_checks()
    got = checks(test)
    assert got["sql.open"][0] == "passed"
    assert got["uc.catalogs"] == ("failed", "RuntimeError: Unity Catalog listed no catalogs")
    assert got["sql.query"] == ("failed", "RuntimeError: Expected 5 rows, the warehouse reported 3")
    assert got["sql.error"] == ("failed", "RuntimeError: A statement on a table that doesn't exist ended ok")
    assert got["sql.cancel"] == ("failed", "RuntimeError: After the cancel the cell ended ok: no error")
    assert got["sql.close"] == ("failed", "RuntimeError: The session still says it is open")


def test_a_query_that_errors_and_an_error_that_names_another_table_fail(tmp_path):
    test = make(tmp_path)
    session = Session(answers={"*": result("error", error="[PARSE_SYNTAX_ERROR] near FROM")})
    with pytest.raises(RuntimeError, match=r"^The query ended error: \[PARSE_SYNTAX_ERROR\] near FROM$"):
        test._query(session)
    with pytest.raises(RuntimeError, match=r"^The error doesn't name the missing table: \[PARSE_SYNTAX_ERROR\]"):
        test._sql_error(session)
    named = Session(answers={"*": result("error", error="[TABLE_OR_VIEW_NOT_FOUND] somewhere")})
    assert test._sql_error(named) == "Reported as an error naming the table."


def test_a_cancel_reported_only_in_the_error_text_counts(tmp_path):
    test = make(tmp_path)
    session = Session(answers={"*": result("error", error="Query was CANCELED by the user", seconds=2.0)})
    assert test._cancel(session, dl.CellRequest(dl.SLOW_SQL, "sql"), "cancelled") == \
        "Cancelled after 2.0 s; the cell reported error."


def test_a_cell_that_ends_with_no_result_fails_the_cancel(tmp_path):
    test = make(tmp_path)
    with pytest.raises(RuntimeError, match="^The cell ended without a result$"):
        test._cancel(Session(answers={}), dl.CellRequest(dl.SLOW_SQL, "sql"), "cancelled")


def test_a_cell_still_running_after_the_wait_fails_the_cancel(tmp_path, monkeypatch):
    class Stuck:
        def __init__(self, target, daemon):
            pass

        def start(self):
            pass

        def is_alive(self):
            return True

        def join(self, timeout=None):
            assert timeout == 300

    monkeypatch.setattr(dl.threading, "Thread", Stuck)
    times = iter([0.0, 0.0, dl.CANCEL_WAIT + 1])
    test = make(tmp_path)
    test.clock = lambda: next(times)
    with pytest.raises(RuntimeError, match="^The cell was still running 5 minutes after the cancel$"):
        test._cancel(Session(cancels=False), dl.CellRequest(dl.SLOW_SQL, "sql"), "cancelled")


def test_a_session_whose_process_is_still_running_fails_close(tmp_path):
    test = make(tmp_path)
    with pytest.raises(RuntimeError, match="^The session's process on this PC is still running$"):
        test._close(Session(proc=SimpleNamespace(poll=lambda: None)))
    assert test._close(Session(proc=SimpleNamespace(poll=lambda: 0))) == \
        "Closed, and its process on this PC has ended."
    assert test._close(Session()) == "Closed."


def test_a_cell_that_errors_fails_its_check(tmp_path):
    test = make(tmp_path)
    with pytest.raises(RuntimeError, match="^The cell ended error: Py4JError$"):
        test._cell(Session(answers={"*": result("error", error="Py4JError")}))
    with pytest.raises(RuntimeError, match="^Expected 5, the cell showed nothing$"):
        test._cell(Session(answers={"*": result("ok")}))


# ---- the error checks


@pytest.mark.parametrize("missing, want", [
    (DatabricksError("HTTP 500: server error", 500), "Expected HTTP 400 or 404: HTTP 500: server error"),
    (DatabricksError("no answer came back"), "Expected HTTP 400 or 404: no answer came back"),
    (None, "Asking for a warehouse that doesn't exist didn't fail"),
])
def test_a_missing_warehouse_that_is_not_reported_as_400_or_404_fails(tmp_path, missing, want):
    with pytest.raises(RuntimeError) as info:
        make(tmp_path, Rest(missing=missing))._not_found()
    assert str(info.value) == want


def test_a_missing_warehouse_reported_as_400_passes(tmp_path):
    assert make(tmp_path, Rest(missing=DatabricksError("HTTP 400: bad id", 400)))._not_found() == "Reported as HTTP 400."


@pytest.mark.parametrize("error, want", [
    (DatabricksError("HTTP 429: slow down", 429), "Expected HTTP 401 or 403: HTTP 429: slow down"),
    (None, "The workspace accepted a bad token"),
])
def test_a_bad_token_that_is_not_refused_with_401_or_403_fails(tmp_path, error, want):
    with pytest.raises(RuntimeError) as info:
        make(tmp_path, bad_token_client=BadToken(error))._bad_token()
    assert str(info.value) == want


def test_a_stop_that_fails_is_a_failed_check_and_leaves_that_compute_running(tmp_path):
    rest = Rest(fail_stop=DatabricksError("HTTP 403: CAN USE only", 403))
    test = make(tmp_path, rest)
    test.started_by_run = [{"kind": "warehouse", "id": "wh1"}, {"kind": "cluster", "id": "c1"}]
    test._stop()
    assert [(c.id, c.status) for c in test.checks] == [("stop.warehouse", "failed"), ("stop.cluster", "passed")]
    assert test.stopped == [{"kind": "cluster", "id": "c1"}] and ("terminate", "c1") in rest.calls


# ---- this PC


def test_a_cli_that_does_not_answer_version_is_named(tmp_path):
    def hangs(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 30)

    def missing(*args, **kwargs):
        raise FileNotFoundError("databricks")

    assert dl._cli_version("/opt/databricks", hangs) == \
        (False, "The Databricks CLI at /opt/databricks didn't answer --version (TimeoutExpired).")
    assert dl._cli_version("/opt/databricks", missing) == \
        (False, "The Databricks CLI at /opt/databricks didn't answer --version (FileNotFoundError).")
