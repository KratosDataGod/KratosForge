"""Optimize's app calls (optimize_service.py): what mutation testing (batch 19,
docs/quality/mutation-testing.md) showed no test pinned. The outbox kept open, the other tables a
lakehouse holds, which failures are worth trying again, the overhead a sync records, the central
lakehouse's schemas default and role-change refusals, and the asks before sharing runs or an analysis.
A local lakehouse folder and fakes only: nothing reaches Fabric or OneLake."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest


from fabric_desktop.fabric_api import FabricError
from fabric_desktop.optimize import local_runs, schema, setup
from fabric_desktop.optimize_service import CHANGED_SINCE
from fabric_desktop.service import Service
from tests.test_optimize_service import svc  # noqa: F401 - the fixture
from tests.test_optimize_share_analysis import records


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata-env"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _drop_schemas_key(svc):  # noqa: F811
    """A connection saved before the registry kept whether the lakehouse has schemas."""
    reg = svc.optimize_registry
    data = reg._load()
    for c in data["connections"]:
        c.pop("schemas", None)
    reg._save(data)
    assert "schemas" not in reg.active()


# ---- the outbox and the lakehouses in a workspace


def test_the_outbox_is_opened_once(svc):  # noqa: F811
    assert svc.optimize_outbox is svc.optimize_outbox


def test_discover_counts_the_tables_that_arent_any_familys(svc, monkeypatch):  # noqa: F811
    class Lake:
        def table_names(self):
            return ["fdopt_schema_version", "fdopt_findings", "orders", "customers", "products"]

    monkeypatch.setattr(Service, "_optimize_lake", lambda self, ws, lh, schemas=None, token=None: Lake())
    [entry] = svc.optimize_discover("w1")
    assert entry["optimize"] is True and entry["other_tables"] == 3


def test_connecting_says_it_created_nothing(svc):  # noqa: F811
    out = svc.optimize_connect("w1", "lh-empty", initialize=True)
    assert out["connected"] is True and out["created"] is False


# ---- which failures are worth trying again


def test_a_refused_write_is_not_tried_again():
    assert Service._optimize_transient(PermissionError("no")) is False
    # a network-looking failure that says it was refused is still refused
    assert Service._optimize_transient(ConnectionError("403 Forbidden")) is False
    assert Service._optimize_transient(ConnectionError("reset")) is True


def test_a_sync_records_the_rows_and_commits_it_added(monkeypatch):
    seen = []

    class Shared:
        rows_sent, commits = 10, 3

        def record_local_executions(self, rows):
            self.rows_sent += len(rows)
            self.commits += 1
            return len(rows)

        def record_measurements(self, ms):
            seen.extend(ms)

    sent = Service._optimize_send(Shared(), "local_executions", {"rows": [{}, {}]})
    assert sent == 2
    figures = {m["metric"]: m["value"] for m in seen}
    assert figures["optimize_sync_rows"] == 2 and figures["optimize_sync_commits"] == 1


# ---- the central lakehouse


def test_a_connection_without_the_schemas_flag_has_schemas(svc):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    _drop_schemas_key(svc)
    lake, _me = svc.central_family(setup.CORE)
    assert lake.schemas is True


def _restrict_setup(svc, monkeypatch, put=None, listing=None):  # noqa: F811
    fam = schema.Family("fdtst_", "Access cache", [schema.Migration(1, "Create", {
        "who": {"name": pl.String}, "schema_version": schema.TABLES_V1["schema_version"]})], schema="access")
    monkeypatch.setitem(schema.FAMILIES, fam.prefix, fam)
    everyone = {"name": "DefaultReader", "decisionRules": [{"effect": "Permit", "permission": [
        {"attributeName": "Path", "attributeValueIncludedIn": ["*"]},
        {"attributeName": "Action", "attributeValueIncludedIn": ["Read"]}]}],
        "members": {"fabricItemMembers": [{"itemAccess": ["ReadAll"]}]}}
    api = type("Api", (), {
        "list_data_access_roles_tagged": listing or (lambda self, ws, lh: ([everyone], '"v1"')),
        "put_data_access_roles": put or (lambda self, ws, lh, r, etag=None: None)})()
    monkeypatch.setattr(Service, "_scoped", lambda self, scopes: api, raising=False)
    monkeypatch.setattr(svc.auth, "signed_in_tenant", lambda: "tenant", raising=False)
    svc.optimize_create("w1", workspace_name="Platform")
    svc.central_family(fam, approve=True)
    return fam


def test_restrict_reads_a_connection_without_the_schemas_flag_as_having_them(svc, monkeypatch):  # noqa: F811
    def unreadable(self, ws, lh):
        raise RuntimeError("x" * 300)

    fam = _restrict_setup(svc, monkeypatch, listing=unreadable)
    _drop_schemas_key(svc)
    with pytest.raises(PermissionError, match="Couldn't read") as e:  # not "no schemas"
        svc.central_restrict(fam, "Readers", ["obj-1"])
    assert str(e.value).endswith(" " + "x" * 200)  # the reason, cut to 200 characters


@pytest.mark.parametrize("code, status", [("PreconditionFailed", 500), ("Other", 412)])
def test_a_role_write_refused_as_stale_says_the_roles_changed(svc, monkeypatch, code, status):  # noqa: F811
    def put(self, ws, lh, r, etag=None):
        raise FabricError("PUT failed", code, status)

    fam = _restrict_setup(svc, monkeypatch, put=put)
    with pytest.raises(PermissionError) as asked:
        svc.central_restrict(fam, "Readers", ["obj-1"])
    with pytest.raises(PermissionError) as e:
        svc.central_restrict(fam, "Readers", ["obj-1"], approve=True, shown=asked.value.changes)
    assert str(e.value) == CHANGED_SINCE


def test_another_role_write_failure_is_raised_as_it_is(svc, monkeypatch):  # noqa: F811
    def put(self, ws, lh, r, etag=None):
        raise FabricError("PUT failed", "Other", 500)

    fam = _restrict_setup(svc, monkeypatch, put=put)
    with pytest.raises(PermissionError) as asked:
        svc.central_restrict(fam, "Readers", ["obj-1"])
    with pytest.raises(FabricError, match="PUT failed"):
        svc.central_restrict(fam, "Readers", ["obj-1"], approve=True, shown=asked.value.changes)


# ---- sharing this PC's runs


def _a_notebook_write(svc):  # noqa: F811
    from tests.test_optimize_local_runs import WRITE
    import json
    (svc.root / "history.jsonl").write_text(json.dumps(WRITE) + "\n", encoding="utf-8")


def test_sharing_runs_reads_the_whole_history(svc, monkeypatch):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    limits = []
    monkeypatch.setattr(svc, "history", lambda limit: limits.append(limit) or [], raising=False)
    assert svc.optimize_share_local_runs()["new"] == 0
    assert limits == [100000]


def test_sharing_runs_needs_approve_as_well_as_the_confirm(svc):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    _a_notebook_write(svc)
    with pytest.raises(PermissionError) as asked:
        svc.optimize_share_local_runs()
    confirm = asked.value.would["confirm"]
    assert len(confirm) == 32 and all(c in "0123456789abcdef" for c in confirm)
    assert "to FabricDesktop_Central (1 notebook write)" in str(asked.value)
    with pytest.raises(PermissionError, match="^Adds 1 run "):
        svc.optimize_share_local_runs(confirm=confirm)
    assert svc._optimize_shared().local_execution_ids() == set()


def test_sharing_an_analysis_needs_approve_as_well_as_the_confirm(svc):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    with pytest.raises(PermissionError) as asked:
        svc._optimize_share_analysis(records(), "this assessment")
    confirm = asked.value.would["confirm"]
    assert len(confirm) == 32
    assert "from this assessment to FabricDesktop_Central, about" in str(asked.value)
    with pytest.raises(PermissionError, match="^Adds"):
        svc._optimize_share_analysis(records(), "this assessment", confirm=confirm)
    assert svc.optimize_findings() == []


def test_the_analysis_confirm_doesnt_depend_on_key_order(svc):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    rows = records()
    flipped = dict(rows, run=dict(reversed(list(rows["run"].items()))))
    with pytest.raises(PermissionError) as one:
        svc._optimize_share_analysis(rows, "this assessment")
    with pytest.raises(PermissionError) as two:
        svc._optimize_share_analysis(flipped, "this assessment")
    assert one.value.would["confirm"] == two.value.would["confirm"]
    out = svc._optimize_share_analysis(flipped, "this assessment", approve=True, confirm=one.value.would["confirm"])
    assert out["shared"] == 1


# ---- metadata runs kept on this PC


class _Log:
    def __init__(self, env):
        self.frame = pl.DataFrame({"run_id": [f"r-{env}"], "env": [None]}, schema={"run_id": pl.String, "env": pl.String})

    def meta(self, name):
        assert name == "run_log"
        return self.frame


@pytest.mark.parametrize("environments, read", [({}, ["dev"]), ({"test": {"target": "local"}}, ["dev", "test"]),
                                                ({"dev": {"target": "onelake"}}, [])])
def test_metadata_runs_come_only_from_environments_kept_on_this_pc(svc, monkeypatch, tmp_path, environments, read):  # noqa: F811
    project = type("Project", (), {"environments": environments, "root": Path(tmp_path / "crm")})()
    asked, given = [], []
    monkeypatch.setattr(svc, "_project", lambda: project, raising=False)
    monkeypatch.setattr(svc, "_store", lambda p, env: asked.append(env) or _Log(env), raising=False)
    monkeypatch.setattr(local_runs, "from_run_log", lambda rows, name, me: given.extend(rows) or [])
    assert svc._optimize_metadata_runs("inst") == []
    assert asked == read
    assert given == [{"run_id": f"r-{env}", "env": env} for env in read]  # a row without its env gets the one it was read from
