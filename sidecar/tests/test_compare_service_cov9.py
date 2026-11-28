"""compare_service: the store under the app's data folder, connections that aren't Snowflake's, and a Fabric
run with no run id."""

from __future__ import annotations

from fabric_desktop import compare_service as cs
from fabric_desktop import costs


class Svc(cs.CompareMixin):
    def __init__(self, profiles=(), connections=()):
        self.profiles, self.connections = list(profiles), list(connections)

    def _cost_settings(self):
        return costs.settings({})

    def engine_connections(self):
        return {"connections": self.connections}

    def engine_profiles(self):
        return {"profiles": self.profiles}


def test_the_store_is_under_the_apps_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    store = Svc()._compare_store()
    assert store.path == tmp_path / "FabricDesktop" / "platform-compare" / "links.json"
    store.link("meta:a", "snowflake", "kf:a")
    assert store.path.is_file()


def test_only_snowflake_connections_with_an_account_are_listed():
    conns = [{"id": "a", "platform": "databricks", "account": "x"},
             {"id": "b", "platform": "snowflake", "account": ""},
             {"id": "c", "platform": "snowflake", "account": "org-acct", "label": "Prod"},
             {"id": "d", "platform": "snowflake", "account": "org-2"}]
    svc = Svc(profiles=[{"id": "p1", "connections": conns}, {"id": "p2", "name": "Empty"}],
              connections=[{"key": "k", "label": "no host"}, {"key": "h", "label": "dev", "host": "https://adb"}])
    out = svc._compare_connections()
    assert out["snowflake"] == [
        {"key": "profile:p1:c", "label": "p1 · Prod", "account": "org-acct", "warehouse": ""},
        {"key": "profile:p1:d", "label": "p1 · d", "account": "org-2", "warehouse": ""}]
    assert out["databricks"] == [{"key": "h", "label": "dev", "host": "https://adb", "warehouse_id": ""}]


def test_a_fabric_run_without_a_run_id_is_left_out_of_the_fabric_runs():
    runs = [{"run_id": None, "entity": "e", "compute": "fabric", "started_at": "2026-11-01T00:00:00+00:00",
             "seconds": 60, "rows_read": 1, "logic": "v1"},
            {"run_id": "f2", "entity": "e", "compute": "fabric", "started_at": "2026-11-02T00:00:00+00:00",
             "seconds": 60, "rows_read": 1, "logic": "v1"}]
    work, pc, fabric = Svc()._compare_inputs("meta:e", runs)
    assert [f["run_id"] for f in fabric] == ["f2"]
    assert pc == [] and work["definition_version"] == "v1" and work["input_scope"] == "regular load"
