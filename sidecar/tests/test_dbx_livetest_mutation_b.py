"""The Databricks live test (dbx_livetest): what each check says when it passes, the summary's lines, the run's
ids and files, and this PC's checks (mutation batch 480). Every workspace, warehouse, cluster and CLI here is a
fake; nothing reaches Databricks or the network."""

from __future__ import annotations

import re
import sys
import threading
from types import SimpleNamespace

from fabric_desktop import dbx_livetest as dl
from fabric_desktop.engines.contract import CellRequest, CellResult
from tests.test_dbx_livetest import HOST, ConnectAdapter, Resp, Workspace, make


def details(result):
    return {c["id"]: c["detail"] for c in result["checks"]}


def test_each_passed_check_says_what_it_found(tmp_path):
    result = make(tmp_path, cluster="0123-456789-abcdefgh", connect=ConnectAdapter())[0].run()
    got = details(result)
    assert got["dbx.cluster"] == ("kf tests: 16.4.x-scala2.12, Standard_D4ds_v5, 0 workers, TERMINATED, "
                                  "auto-termination 10 min.")
    assert got["sql.open"] == "Session on kf tests (the warehouse was STOPPED)."
    assert got["uc.catalogs"] == "2 catalogs, including main, samples."
    assert re.fullmatch(r"5 rows in \d+\.\d s \(the first query includes starting a stopped warehouse\)\.",
                        got["sql.query"])
    assert got["connect.open"] == "Connected with databricks-connect 16.4.23."
    assert got["errors.bad_token"] == "Refused with 401."


def test_a_cluster_without_a_worker_count_shows_0_workers(tmp_path):
    ws = Workspace()
    ws.cluster.pop("num_workers")
    result = make(tmp_path, ws, cluster="0123-456789-abcdefgh", connect=ConnectAdapter())[0].run()
    assert " 0 workers, " in details(result)["dbx.cluster"]


class ManyCatalogs(Workspace):
    def request(self, method, url, **kw):
        if url.endswith("/api/2.1/unity-catalog/catalogs"):
            self.calls.append((method, url[len(HOST):]))
            return Resp(200, {"catalogs": [{"name": f"c{i}"} for i in range(7)]})
        return super().request(method, url, **kw)


def test_the_catalog_check_names_the_first_five(tmp_path):
    result = make(tmp_path, ManyCatalogs())[0].run()
    assert details(result)["uc.catalogs"] == "7 catalogs, including c0, c1, c2, c3, c4."


def test_the_missing_warehouse_asked_for_has_a_16_character_id(tmp_path):
    test, ws = make(tmp_path)
    test.run()
    asked = [p.rsplit("/", 1)[-1] for _m, p in ws.calls if p.startswith("/api/2.0/sql/warehouses/")
             and not p.endswith("/wh1") and not p.endswith("/stop")]
    assert asked and all(re.fullmatch(r"[0-9a-f]{16}", a) for a in asked)


def test_stopping_says_what_it_asked_for_under_its_own_title(tmp_path):
    test, ws = make(tmp_path, cluster="0123-456789-abcdefgh", connect=ConnectAdapter(), stop_compute=True)
    result = test.run()
    stops = {c["id"]: (c["title"], c["detail"]) for c in result["checks"] if c["id"].startswith("stop.")}
    assert stops == {
        "stop.warehouse": ("Stop the warehouse", "Stop requested; it was stopped when the run began."),
        "stop.cluster": ("Terminate the cluster", ("Termination requested; it was terminated when the run "
                                                   "began. The cluster is kept, not deleted."))}
    assert result["left_running"] == []
    assert "Started by this run" not in (tmp_path / "r1" / "summary.md").read_text(encoding="utf-8")


def test_a_session_that_opens_but_cant_say_its_warehouse_skips_the_rest(tmp_path):
    class Nameless:
        def open_session(self, config, authorization=None):
            return SimpleNamespace(close=lambda: None, closed=True)

    test, _ws = make(tmp_path)
    test.sql_adapter = Nameless()
    result = test.run()
    status = {c["id"]: c["status"] for c in result["checks"]}
    assert status["sql.open"] == "failed"
    assert [status[i] for i in ("uc.catalogs", "sql.query", "sql.error", "sql.cancel")] == ["skipped"] * 4
    assert "sql.close" not in status


def test_the_slow_statement_runs_on_a_daemon_thread(tmp_path, monkeypatch):
    made = []
    real = threading.Thread

    def thread(*args, **kwargs):
        made.append(kwargs.get("daemon"))
        return real(*args, **kwargs)

    monkeypatch.setattr(dl.threading, "Thread", thread)
    test, _ws = make(tmp_path)
    test.run()
    assert made and all(d is True for d in made)


def test_the_cancel_wait_ends_at_its_deadline(tmp_path):
    test, _ws = make(tmp_path)
    times = iter([0.0, dl.CANCEL_WAIT])
    test.clock = lambda: next(times, dl.CANCEL_WAIT)
    go = threading.Event()

    class Session:
        def execute(self, request, on_output=None):
            go.wait(5)
            return CellResult("ok", [], 0.1, None)

        def cancel(self):
            go.set()
            return True

    def run():
        try:
            return test._cancel(Session(), CellRequest("x", "sql"), "cancelled")
        except RuntimeError as exc:
            return str(exc)
        finally:
            go.set()

    assert run() == "The cell ended ok before it could be cancelled"


def test_a_run_has_a_12_character_id_and_writes_result_json_with_two_space_indents(tmp_path):
    ws = Workspace()
    test = make(tmp_path, ws)[0]
    fresh = dl.DatabricksLiveTest({"host": HOST}, "wh1", test.tokens, test.client, test.sql_adapter, tmp_path)
    assert re.fullmatch(r"[0-9a-f]{12}", fresh.run_id)
    test.run()
    lines = (tmp_path / "r1" / "result.json").read_text(encoding="utf-8").splitlines()
    assert lines[1] == '  "schema": "databricks-live-test-result@1",'


# ---- the summary

def _result(**changes):
    result = {"run_id": "r1", "outcome": "passed", "workspace": {"host": HOST},
              "warehouse": {"name": "wh", "id": "wh1"}, "cluster": None, "started_at": "now", "refused": [],
              "checks": [], "left_running": [], "stop_compute": False}
    return result | changes


def test_the_summary_names_the_workspace_and_says_how_to_stop_what_is_left():
    text = dl.summary(_result(left_running=[{"kind": "warehouse", "id": "wh1"}]))
    assert f"Workspace {HOST}, warehouse wh, started now." in text
    assert "**Started by this run and left to its auto-stop** (add --stop-compute to stop it):" in text
    text = dl.summary(_result(left_running=[{"kind": "warehouse", "id": "wh1"}], stop_compute=True))
    assert "**Started by this run and left to its auto-stop** (stopping it failed):" in text
    assert "Started by this run" not in dl.summary(_result())


def test_the_summary_cuts_titles_at_200_and_ids_at_100_characters():
    title, wid, cid = "t" * 300, "w" * 150, "c" * 150
    text = dl.summary(_result(warehouse={"name": "", "id": wid}, cluster={"name": "", "id": cid},
                              checks=[{"title": title, "status": "passed", "seconds": 1, "detail": ""}]))
    assert f"warehouse {'w' * 99}…, cluster {'c' * 99}…, started" in text
    assert f"| {'t' * 199}… | Passed |" in text


# ---- this PC

def test_this_pcs_python_is_shown_as_major_minor_micro(tmp_path):
    found = dl.pc_checks({"host": HOST}, environ={"PATH": ""})
    have = ".".join(map(str, sys.version_info[:3]))
    assert found[0] == (True, f"Python {have} (KratosForge needs 3.12 or later).")


def test_the_cli_is_asked_its_version_as_text_with_a_30_second_limit():
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="Databricks CLI v0.205\n", stderr="")

    assert dl._cli_version("/opt/databricks", run) == (True, "Databricks CLI 0.205.0 at /opt/databricks.")
    [(args, kwargs)] = calls
    assert args == ["/opt/databricks", "--version"]
    assert kwargs["capture_output"] is True and kwargs["text"] is True and kwargs["timeout"] == 30

