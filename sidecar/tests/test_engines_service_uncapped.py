"""Engine picker paths that the uncapped mutation run (batch 185, docs/quality/mutation-testing.md)
showed no test checked."""

from __future__ import annotations

import pytest

import test_databricks_sync_service as sync_tests
import test_engine_connections_settings as settings_tests
from fabric_desktop import connprofiles

from test_databricks_workspace_sync import REMOTE


@pytest.fixture(autouse=True)
def clis(monkeypatch):
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: settings_tests.CLI)
    monkeypatch.setattr(connprofiles, "snowflake_cli_connections", lambda folder=None: settings_tests.SF)


def test_removing_a_sync_pair_answers_with_its_id(tmp_path):
    (tmp_path / "nb").mkdir()
    svc = sync_tests.Svc(tmp_path)
    pid = svc.databricks_sync_pair_save("sales-etl", "cli:dev", str(tmp_path / "nb"), REMOTE)["id"]
    assert svc.databricks_sync_pair_remove(pid) == {"removed": pid}


def test_removing_a_connection_answers_with_the_profile_and_the_connection(tmp_path):
    svc = settings_tests.Svc(tmp_path)
    pid = svc.engine_connection_save(profile_name="P", databricks_cli="dev")["profile"]
    assert svc.engine_connection_remove(pid, "dev") == {"profile": pid, "removed": "dev"}


def test_adding_a_connection_clears_the_last_check_of_the_profile(tmp_path):
    svc = settings_tests.Svc(tmp_path)
    store = svc._profiles()
    store.save({"id": "p1", "name": "P", "kind": "local",
                "validation": {"status": "warnings", "checked_at": None, "problems": ["x: add your own sign-in"]}})
    svc.engine_connection_save(profile="p1", databricks_cli="dev")
    assert store.load("p1")["validation"] == {"status": "not_checked", "checked_at": None, "problems": []}


def test_settings_show_a_databricks_warehouse_id_and_a_snowflake_warehouse(tmp_path):
    svc = settings_tests.Svc(tmp_path)
    svc.engine_connection_save(profile_name="D", databricks_cli="dev", warehouse_id="wh-run")
    svc.engine_connection_save(profile_name="S", snowflake_cli="sf")
    views = {c["platform"]: c for p in svc.engine_profiles()["profiles"] for c in p["connections"]}
    assert views["databricks"]["warehouse_id"] == "wh-run"
    assert views["snowflake"]["warehouse_id"] == "WH"


def test_discovery_reads_unity_catalog_unless_asked_not_to(tmp_path, monkeypatch):
    from fabric_desktop.engines import databricks_discovery
    asked = []

    class Discovery:
        def __init__(self, client, catalogs):
            asked.append(catalogs)

        def run(self):
            return {"ok": True}

    monkeypatch.setattr(databricks_discovery, "Discovery", Discovery)
    svc = settings_tests.Svc(tmp_path)
    svc.engine_discover("cli:dev")
    svc.engine_discover("cli:dev", catalogs=False)
    assert asked == [True, False]


def test_runs_are_of_the_last_day_unless_asked(tmp_path):
    asked = []

    class Reader:
        def list(self, days, mine):
            asked.append(days)
            return {"rows": []}

    svc = settings_tests.Svc(tmp_path)
    svc._runs_reader = lambda connection: Reader()
    svc.databricks_runs("cli:dev", mine=False)
    svc.databricks_runs("cli:dev", days=7, mine=False)
    assert asked == [1, 7]


def test_billing_runs_when_the_sign_in_cannot_name_its_user(tmp_path, monkeypatch):
    import test_databricks_billing as billing_tests
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: billing_tests.CLI)
    svc = billing_tests.Svc(tmp_path)
    svc._auth = object()  # no signed_in_user
    assert svc.databricks_billing("cli:dev", days=7, confirmed=True)["ok"]
    assert svc.adapter.log[0][2].by == "this PC's user"


def test_an_engine_that_cannot_run_cells_yet_is_refused_with_its_plan(tmp_path):
    from fabric_desktop.engines import registry
    with pytest.raises(ValueError, match="Apache Spark \\(this PC\\) can't run cells in this version. Phase 2"):
        settings_tests.Svc(tmp_path).engine_prepare("nb", registry.LOCAL_SPARK.id)


def test_the_profiles_folder_can_be_asked_for_twice(tmp_path, monkeypatch):
    from fabric_desktop import engines_service as es
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    svc = es.EnginesMixin()
    assert svc._profiles().root == svc._profiles().root == tmp_path / "appdata" / "FabricDesktop" / "profiles"


def test_a_saved_cli_connection_is_not_serverless(tmp_path):
    svc = settings_tests.Svc(tmp_path)
    pid = svc.engine_connection_save(profile_name="D", databricks_cli="dev")["profile"]
    assert svc._profiles().load(pid)["connections"][0]["compute"]["serverless"] is False


def test_a_third_connection_from_the_same_cli_profile_gets_the_next_number(tmp_path):
    svc = settings_tests.Svc(tmp_path)
    pid = svc.engine_connection_save(profile_name="D", databricks_cli="dev")["profile"]
    svc.engine_connection_save(profile=pid, databricks_cli="dev")
    assert svc.engine_connection_save(profile=pid, databricks_cli="dev")["connection"] == "dev-3"
    assert [c["id"] for c in svc._profiles().load(pid)["connections"]] == ["dev", "dev-2", "dev-3"]


@pytest.mark.parametrize("call", ["validate", "plan", "deploy"])
def test_bundle_calls_pass_the_allowed_commands_and_the_typed_target_on(tmp_path, monkeypatch, call):
    from fabric_desktop.engines import databricks_bundles
    seen = []
    monkeypatch.setattr(databricks_bundles, call, lambda *args: seen.append(args) or {"ok": True})
    svc = settings_tests.Svc(tmp_path)
    svc._bundle_run = "runner"
    if call == "deploy":
        svc.databricks_bundle_deploy("f", "prod", "cli:dev", "d1", typed="prod", allow_commands="c1")
        assert seen == [("f", "prod", "dev", "d1", "prod", "runner", "c1")]
    else:
        getattr(svc, f"databricks_bundle_{call}")("f", "dev", "cli:dev", allow_commands="c1")
        assert seen == [("f", "dev", "dev", "runner", "c1")]


def test_going_back_to_python_from_a_local_engine_plans_to_close_it(tmp_path):
    import test_engines_service as engine_tests
    svc = engine_tests.Svc(tmp_path)
    svc.engine_select("nb", "local-duckdb")
    engine_tests.wait_ready(svc, "nb")
    try:
        plan = svc.engine_prepare("nb", "local-python")
        assert plan["transition"] == {"needed": True, "loses": ["variables and tables in the DuckDB (this PC) session"],
                                      "decisions": ["close"], "message": ""}
    finally:
        svc.engine_close("nb")


def test_a_profile_that_allows_the_engine_by_policy_needs_no_confirm(tmp_path):
    import test_engines_service as engine_tests
    svc = engine_tests.Svc(tmp_path)
    key = svc.engine_connection_save(profile_name="P", databricks_cli="dev", warehouse_id="wh1")["key"]
    data = svc._profiles().load(key.split(":")[1])
    data["execution"] |= {"remote_compute": "policy", "allowed_remote_engines": ["databricks-sql"]}
    svc._profiles().save(data)
    try:
        svc.engine_select("nb", "databricks-sql", connection=key, decision="keep")
        assert engine_tests.wait_ready(svc, "nb")["reason"] == "policy"
    finally:
        svc.engine_close("nb")


@pytest.mark.parametrize("ids", [{"workspace_id": "w1"}, {"lakehouse_id": "lh1"}])
def test_fabric_spark_needs_both_the_workspace_and_the_lakehouse(tmp_path, ids):
    with pytest.raises(ValueError, match="Attach a lakehouse to this notebook first"):
        settings_tests.Svc(tmp_path).engine_prepare("nb", "fabric-spark", **ids)


# ---- what Settings and the picker list

def test_a_new_profile_gets_a_14_character_id(tmp_path):
    import re
    pid = settings_tests.Svc(tmp_path).engine_connection_save(profile_name="D", databricks_cli="dev")["profile"]
    assert re.fullmatch(r"p-[0-9a-f]{12}", pid)


def test_a_connection_row_carries_its_profiles_remote_compute_policy(tmp_path):
    svc = settings_tests.Svc(tmp_path)
    key = svc.engine_connection_save(profile_name="P", databricks_cli="dev")["key"]
    data = svc._profiles().load(key.split(":")[1])
    data["execution"] |= {"remote_compute": "never"}
    svc._profiles().save(data)
    row = next(c for c in svc.engine_connections()["connections"] if c["key"] == key)
    assert row["remote_compute"] == "never"


def test_profiles_show_their_names_and_only_the_active_one_is_active(tmp_path):
    import json
    svc = settings_tests.Svc(tmp_path)
    pid = svc.engine_connection_save(profile_name="Sales", databricks_cli="dev")["profile"]
    (svc._profiles().root / "broken.json").write_text(json.dumps({"id": "broken", "name": "Broken", "connections": "x"}))
    rows = {p["id"]: p for p in svc.engine_profiles()["profiles"]}
    assert (rows[pid]["name"], rows[pid]["active"]) == ("Sales", False)
    assert (rows["broken"]["name"], rows["broken"]["active"]) == ("Broken", False)
    assert rows["default"]["active"] is True
    svc._profiles().set_active(pid)
    rows = {p["id"]: p for p in svc.engine_profiles()["profiles"]}
    assert (rows[pid]["active"], rows["default"]["active"]) == (True, False)


def test_runs_that_cannot_read_the_signed_in_user_are_still_signed_in(tmp_path, monkeypatch):
    import test_databricks_runs_service as runs_tests
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: runs_tests.CLI)
    ws = runs_tests.Workspace()
    ws.me = ""
    out = runs_tests.Svc(tmp_path, ws).databricks_runs("cli:dev")
    assert out["signed_in"] is True and out["rows"] == []
    assert out["error"].startswith("Couldn't read who you're signed in as: ")


def test_replacing_one_connection_keeps_the_others(tmp_path):
    svc = settings_tests.Svc(tmp_path)
    pid = svc.engine_connection_save(profile_name="D", databricks_cli="dev")["profile"]
    svc.engine_connection_save(profile=pid, databricks_cli="dev")
    svc.engine_connection_save(profile=pid, databricks_cli="dev", warehouse_id="wh-run", replace="dev")
    conns = svc._profiles().load(pid)["connections"]
    assert [c["id"] for c in conns] == ["dev-2", "dev"]
    assert conns[1]["compute"]["warehouse_id"] == "wh-run"


def test_a_long_cli_profile_name_is_cut_to_36_characters_before_its_number(tmp_path, monkeypatch):
    name = "p" * 40
    entry = dict(settings_tests.CLI["profiles"][0], name=name)
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: {"path": "x", "found": True,
                                                                                     "profiles": [entry]})
    svc = settings_tests.Svc(tmp_path)
    pid = svc.engine_connection_save(profile_name="D", databricks_cli=name)["profile"]
    assert svc.engine_connection_save(profile=pid, databricks_cli=name)["connection"] == "p" * 36 + "-2"


# ---- before choosing, and a cell

def test_leaving_python_for_another_engine_needs_a_decision(tmp_path):
    plan = settings_tests.Svc(tmp_path).engine_prepare("nb", "local-duckdb")
    assert plan["transition"]["needed"] is True
    assert plan["transition"]["decisions"] == ["close", "keep"]


@pytest.mark.parametrize("state, says", [("TERMINATED", "The cluster is terminated, so"),
                                         ("", "The cluster is stopped, so")])
def test_the_risk_check_names_the_clusters_state(tmp_path, state, says):
    import test_engines_service as engine_tests
    svc = engine_tests.Svc(tmp_path)
    key = svc.engine_connection_save(profile_name="P", databricks_cli="dev", cluster_id="c-1")["key"]
    svc._engine_read_target = lambda engine, t: {"name": "C", "state": state, "auto_stop": 10}
    texts = [line["text"] for line in svc.engine_prepare("nb", "databricks-connect", connection=key)["risk"]["lines"]]
    assert any(t.startswith(says) for t in texts)


@pytest.mark.parametrize("error, says", [(RuntimeError("HTTP 503 down"), "(HTTP 503)."),
                                         (RuntimeError("down"), "(RuntimeError).")])
def test_a_risk_check_that_cannot_read_says_the_status_or_the_kind_of_error(tmp_path, monkeypatch, error, says):
    import test_engines_service as engine_tests
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: engine_tests.CLI)
    svc = engine_tests.Svc(tmp_path)

    def read(engine, t):
        raise error
    svc._engine_read_target = read
    line = next(x for x in svc.engine_prepare("nb", "databricks-sql", connection="cli:dev")["risk"]["lines"]
                if x["outcome"] == "unknown")
    assert line["text"] == "Couldn't run: couldn't read the warehouse " + says


def test_a_cell_that_raises_says_how_long_it_ran(tmp_path, monkeypatch):
    import test_engines_service as engine_tests
    svc = engine_tests.Svc(tmp_path)
    svc.engine_select("nb", "local-duckdb")
    engine_tests.wait_ready(svc, "nb")
    try:
        def execute(notebook, req):
            raise OSError("gone")
        monkeypatch.setattr(svc._engine_manager(), "execute", execute)
        out = engine_tests.run(svc, "nb", "%%sql\nSELECT 1")
        assert out["status"] == "error" and 0 <= out["seconds"] < 60
    finally:
        svc.engine_close("nb")
