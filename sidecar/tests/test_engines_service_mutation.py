"""Engine picker paths that mutation testing (batch 15, docs/quality/mutation-testing.md) showed no test
checked: how Settings shows a connection, keys with a colon in the connection's part, whether a remote
engine asks for a confirm under each profile policy, the switch a prepare describes, the risk check's
wording, pairs that share only one folder, and the background threads. Databricks is the fake of the
documented APIs from test_databricks_sql; nothing reaches a workspace."""

from __future__ import annotations

import re
import threading
import time
from types import SimpleNamespace

import pytest

from fabric_desktop import connprofiles
from fabric_desktop import engines_service as es

from test_engines_service import CLI, Svc, wait_ready


@pytest.fixture(autouse=True)
def app_data(tmp_path, monkeypatch):
    """App data (Databricks sync pairs, settings) goes to this test's folder, never the real one."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))


@pytest.fixture(autouse=True)
def cli(monkeypatch):
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: CLI)


def _profile(svc, name="Team", label="", **execution):
    out = svc.engine_connection_save(profile_name=name, databricks_cli="dev", warehouse_id="wh1", label=label)
    if execution:
        data = svc._profiles().load(out["profile"])
        data["execution"] |= execution
        svc._profiles().save(data)
    return out


# ---- how a connection shows

def test_a_connection_view_keeps_its_sign_in_and_falls_back_to_the_database():
    view = es._connection_view("p1", {"id": "c1", "platform": "databricks", "catalog": "main",
                                      "credential": {"method": "cli-profile", "profile": "dev"}})
    assert (view["key"], view["method"], view["cli"], view["catalog"]) == ("profile:p1:c1", "cli-profile", "dev", "main")
    snow = es._connection_view("p1", {"id": "c2", "platform": "snowflake", "database": "SALES",
                                      "credential": {"method": "oauth-local", "connection": "sf"}})
    assert (snow["key"], snow["catalog"], snow["cli"]) == ("", "SALES", "sf")


def test_a_saved_connection_is_listed_as_profile_name_and_label(tmp_path):
    svc = Svc(tmp_path)
    saved = _profile(svc, name="Team", label="Prod WH")
    rows = {c["key"]: c for c in svc.engine_connections()["connections"]}
    assert rows[saved["key"]]["label"] == "Team · Prod WH"


def test_a_key_whose_connection_part_has_a_colon_names_that_connection(tmp_path):
    svc = Svc(tmp_path)
    saved = _profile(svc, name="P")
    with pytest.raises(ValueError, match="'P' has no connection 'a:b'"):
        svc._connection(f"profile:{saved['profile']}:a:b")


# ---- the confirm a remote engine asks for

@pytest.mark.parametrize("execution, confirm", [
    ({"remote_compute": "policy", "allowed_remote_engines": ["databricks-sql"]}, False),
    ({"remote_compute": "policy", "allowed_remote_engines": ["databricks-connect"]}, True),
    ({"remote_compute": "ask", "allowed_remote_engines": ["databricks-sql"]}, True),
    ({"remote_compute": "ask", "allowed_remote_engines": []}, True),
])
def test_prepare_asks_for_a_confirm_unless_the_policy_allows_this_engine(tmp_path, execution, confirm):
    svc = Svc(tmp_path)
    key = _profile(svc, **execution)["key"]
    plan = svc.engine_prepare("nb", "databricks-sql", connection=key)
    assert plan["confirm"] is confirm and plan["policy"]["mode"] == execution["remote_compute"]


# ---- the switch a prepare describes

def test_prepare_from_one_local_engine_to_another_says_the_session_closes(tmp_path):
    svc = Svc(tmp_path)
    svc.engine_select("nb", "local-duckdb")
    wait_ready(svc, "nb")
    other = svc.engine_prepare("nb", "local-sqlite")["transition"]
    assert other["needed"] is True and "close" in other["decisions"]
    same = svc.engine_prepare("nb", "local-duckdb")["transition"]
    assert same == {"needed": False, "loses": [], "decisions": [], "message": ""}
    svc.engine_close("nb")


def test_prepare_for_the_kernel_while_on_the_kernel_needs_no_switch(tmp_path):
    assert Svc(tmp_path).engine_prepare("nb", "local-python")["transition"]["needed"] is False


# ---- the risk check's wording

class _Read(Svc):
    state = {"name": "Shared", "state": "STOPPED", "auto_stop": None}

    def _engine_read_target(self, engine, t):
        return dict(self.state)


def test_the_risk_lines_name_the_warehouse(tmp_path):
    texts = [x["text"] for x in _Read(tmp_path).engine_prepare("nb", "databricks-sql", connection="cli:dev")["risk"]["lines"]]
    assert "The warehouse is stopped, so it will start and bill DBUs." in texts
    assert "The warehouse has auto-stop turned off, so it keeps billing DBUs until someone stops it." in texts


def test_one_writing_cell_is_one_cell_and_six_name_the_first_five(tmp_path):
    svc = _Read(tmp_path)
    one = svc.engine_prepare("nb", "databricks-sql", connection="cli:dev", cells=["%%sql\nINSERT INTO t VALUES (1)"])
    assert "1 cell may change data on Shared (cell 1)." in [x["text"] for x in one["risk"]["lines"]]
    six = svc.engine_prepare("nb", "databricks-sql", connection="cli:dev",
                             cells=["%%sql\nDELETE FROM t"] * 6)
    assert "6 cells may change data on Shared (cell 1, 2, 3, 4, 5)." in [x["text"] for x in six["risk"]["lines"]]


# ---- Databricks sync pairs

def test_a_pair_sharing_only_one_folder_with_another_is_kept(tmp_path):
    svc = Svc(tmp_path)
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    first = svc.databricks_sync_pair_save("one", "cli:dev", str(tmp_path / "a"), "/Workspace/Users/dev/x")["id"]
    assert re.fullmatch(r"s-[0-9a-f]{10}", first)
    svc.databricks_sync_pair_save("same local", "cli:dev", str(tmp_path / "a"), "/Workspace/Users/dev/y")
    svc.databricks_sync_pair_save("same remote", "cli:dev", str(tmp_path / "b"), "/Workspace/Users/dev/x")
    assert len(svc.databricks_sync_pairs()["pairs"]) == 3


# ---- state and polling

def test_a_choice_whose_start_was_forgotten_reads_as_ready(tmp_path):
    svc = Svc(tmp_path)
    svc._engine_manager()
    svc._engine_choice["nb"] = {"engine": "local-duckdb", "label": "DuckDB"}
    assert svc.engine_state("nb") == {"engine": "local-duckdb", "label": "DuckDB", "status": "ready"}


def test_a_running_cell_reports_its_time_to_a_tenth_of_a_second(tmp_path):
    svc = Svc(tmp_path)
    svc._engine_manager()
    job = es._Job()
    job.started = time.monotonic() - 2.34
    svc._engine_cells["nb"] = job
    out = svc.engine_poll_cell("nb", wait=0)
    assert out["status"] == "running" and out["seconds"] in (2.3, 2.4)


def test_sessions_and_cells_run_on_daemon_threads_so_the_app_can_quit(tmp_path, monkeypatch):
    made = []

    class Thread:
        def __init__(self, target, daemon, name):
            made.append((daemon, name))
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(es, "threading", SimpleNamespace(Thread=Thread, Event=threading.Event, Lock=threading.Lock))
    svc = Svc(tmp_path)
    svc.engine_select("nb", "local-duckdb")
    assert svc.engine_start_cell("nb", "%%sql\nSELECT 1")["status"] == "running"
    assert made == [(True, "engine-start-nb"), (True, "engine-cell-nb")]
    svc.engine_close("nb")
