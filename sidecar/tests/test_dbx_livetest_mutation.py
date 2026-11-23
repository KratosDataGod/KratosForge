"""The Databricks live test (dbx_livetest.py), held to exact values: each check's detail on a full run, the
catalogs it names, the ids it makes up, this PC's Python and CLI lines and how the CLI is asked, a session that
opened but can't say what it is, the cancel's deadline, stopping, result.json's layout, and the summary's every
line. Every workspace, client and session is a stand-in from the main tests: nothing reaches Databricks."""

from __future__ import annotations

import json
import re
import subprocess
from types import SimpleNamespace

import pytest

from fabric_desktop import dbx_livetest as dl
from tests.test_dbx_livetest import ConnectAdapter
from tests.test_dbx_livetest import make as make_full
from tests.test_dbx_livetest_cov2 import Adapter, Rest, Session, checks, make, result

DETAILS = {
    "auth.token": "Token received (not recorded), by databricks-cli.",
    "dbx.warehouse": "kf tests: 2X-Small, serverless, STOPPED, auto-stop 5 min.",
    "dbx.cluster": "kf tests: 16.4.x-scala2.12, Standard_D4ds_v5, 0 workers, TERMINATED, auto-termination 10 min.",
    "sql.open": "Session on kf tests (the warehouse was STOPPED).",
    "uc.catalogs": "2 catalogs, including main, samples.",
    "sql.query": "5 rows in 0.0 s (the first query includes starting a stopped warehouse).",
    "sql.error": "Reported as an error naming the table.",
    "sql.cancel": "Cancelled after 0.0 s; the cell reported cancelled.",
    "sql.close": "Closed.",
    "connect.open": "Connected with databricks-connect 16.4.23.",
    "connect.cell": "spark.range(5).count() is 5, in 0.5 s.",
    "connect.cancel": "Cancelled after 1.0 s; the cell reported error.",
    "connect.close": "Closed.",
    "errors.not_found": "Reported as HTTP 404.",
    "errors.bad_token": "Refused with 401.",
}


def test_a_full_run_says_exactly_what_each_check_found(tmp_path):
    test, _ = make_full(tmp_path, cluster="0123-456789-abcdefgh", connect=ConnectAdapter())
    got = test.run()
    assert {c["id"]: c["detail"] for c in got["checks"]} == DETAILS
    text = (tmp_path / "r1" / "result.json").read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=2)


def test_a_cluster_without_a_worker_count_has_none(tmp_path):
    test = make(tmp_path, Rest(cluster={"cluster_name": "c", "state": "RUNNING"}))
    test.cluster_id = "c1"
    assert test._read_cluster() == "c: ?, ?, 0 workers, RUNNING, auto-termination ? min."


def test_the_catalogs_named_are_the_first_five(tmp_path):
    session = Session(catalogs=[{"catalog": c} for c in "gfedcba"] + [{"catalog": ""}])
    assert make(tmp_path)._catalogs(session) == "7 catalogs, including a, b, c, d, e."


def test_the_query_detail_gives_its_seconds(tmp_path):
    session = Session(answers={"*": result("ok", rows=5, seconds=2.25)})
    assert make(tmp_path)._query(session) == "5 rows in 2.2 s (the first query includes starting a stopped warehouse)."


def test_made_up_ids_have_their_length(tmp_path):
    rest = Rest()
    test = make(tmp_path, rest)
    test._not_found()
    [(_, missing)] = rest.calls
    assert re.fullmatch(r"[0-9a-f]{16}", missing)
    unnamed = dl.DatabricksLiveTest({"host": "https://adb-1.2.azuredatabricks.net"}, "wh1", None, rest, None, tmp_path)
    assert re.fullmatch(r"[0-9a-f]{12}", unnamed.run_id)


def test_this_pcs_python_is_named_by_its_three_numbers():
    ok, line = dl.pc_checks({"host": "https://adb-1.2.azuredatabricks.net"}, environ={"PATH": ""})[0]
    assert re.fullmatch(r"Python \d+\.\d+\.\d+ \(KratosForge needs 3\.12 or later\)\.", line)


def test_the_cli_is_asked_only_for_its_version_and_a_missing_patch_is_zero():
    calls = []

    def run(args, **kw):
        calls.append((args, kw))
        return SimpleNamespace(stdout="Databricks CLI v0.205", stderr="", returncode=0)
    assert dl._cli_version("/cli", run) == (True, "Databricks CLI 0.205.0 at /cli.")
    assert calls == [(["/cli", "--version"], {"capture_output": True, "text": True, "encoding": "utf-8",
                                              "errors": "replace", "timeout": 30, "stdin": subprocess.DEVNULL})]


def test_a_session_that_opened_but_cant_say_what_it_is_skips_the_rest(tmp_path):
    class Nameless(Session):
        @property
        def warehouse_name(self):
            raise RuntimeError("no name")

        @warehouse_name.setter
        def warehouse_name(self, value):
            pass
    test = make(tmp_path, sql=Adapter(session=Nameless()))
    test._sql_checks()
    got = checks(test)
    assert got["sql.open"] == ("failed", "RuntimeError: no name")
    assert {k: v[0] for k, v in got.items()} == {"sql.open": "failed", "uc.catalogs": "skipped", "sql.query": "skipped",
                                                "sql.error": "skipped", "sql.cancel": "skipped"}


def test_no_cancel_is_sent_once_the_wait_is_over(tmp_path, monkeypatch):
    class Stuck:
        def __init__(self, target, daemon):
            pass

        def start(self):
            pass

        def is_alive(self):
            return True

        def join(self, timeout=None):
            pass
    monkeypatch.setattr(dl, "threading", SimpleNamespace(Thread=Stuck))
    times = iter([0.0, dl.CANCEL_WAIT])
    test = make(tmp_path)
    test.clock = lambda: next(times)
    session = Session()
    with pytest.raises(RuntimeError, match="still running"):
        test._cancel(session, dl.CellRequest(dl.SLOW_SQL, "sql"), "cancelled")
    assert not session.cancelled.is_set()


def test_stopping_names_what_it_stops(tmp_path):
    test = make(tmp_path)
    test.started_by_run = [{"kind": "warehouse", "id": "wh1"}, {"kind": "cluster", "id": "c1"}]
    test._stop()
    assert [(c.id, c.title, c.detail) for c in test.checks] == [
        ("stop.warehouse", "Stop the warehouse", "Stop requested; it was stopped when the run began."),
        ("stop.cluster", "Terminate the cluster",
         "Termination requested; it was terminated when the run began. The cluster is kept, not deleted.")]


def _result(**kw):
    return {"run_id": "r1", "outcome": "refused", "started_at": "S", "workspace": {"host": ""},
            "warehouse": {"id": "wh1", "name": "W" * 250}, "cluster": {"id": "c1", "name": "C" * 250},
            "refused": ["x|y"], "checks": [{"title": "T" * 250, "status": "skipped", "seconds": 0.5, "detail": "d"}],
            "left_running": [{"kind": "warehouse", "id": "I" * 150}], "stop_compute": True} | kw


def test_the_summary_writes_every_part_cut_to_its_limits():
    assert dl.summary(_result()) == (
        f"# KratosForge Databricks live test r1\n\nOutcome: **refused**. Workspace from the CLI profile, "
        f"warehouse {'W' * 199}…, cluster {'C' * 199}…, started S.\n\nNothing was run, because:\n\n- x\\|y\n\n"
        f"| Check | Result | Seconds | Detail |\n| --- | --- | --- | --- |\n| {'T' * 199}… | Skipped | 0.5 | d |\n\n"
        f"**Started by this run and left to its auto-stop** (stopping it failed):\n\n- warehouse {'I' * 99}…\n")


def test_a_summary_names_compute_by_id_without_a_name():
    text = dl.summary(_result(workspace={"host": "https://h"}, warehouse={"id": "I" * 150, "name": ""},
                              cluster={"id": "K" * 150, "name": ""}, refused=[], checks=[], left_running=[],
                              stop_compute=False))
    assert text == (f"# KratosForge Databricks live test r1\n\nOutcome: **refused**. Workspace https://h, "
                    f"warehouse {'I' * 99}…, cluster {'K' * 99}…, started S.\n\n"
                    "| Check | Result | Seconds | Detail |\n| --- | --- | --- | --- |\n")


def test_a_summary_says_how_to_stop_what_was_left_running():
    text = dl.summary(_result(cluster=None, refused=[], checks=[], stop_compute=False,
                              left_running=[{"kind": "cluster", "id": "c1"}]))
    assert text.endswith(f"warehouse {'W' * 199}…, started S.\n\n| Check | Result | Seconds | Detail |\n"
                         "| --- | --- | --- | --- |\n\n**Started by this run and left to its auto-stop** "
                         "(add --stop-compute to stop it):\n\n- cluster c1\n")
