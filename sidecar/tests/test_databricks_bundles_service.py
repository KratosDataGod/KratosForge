"""Source control > Bundles RPCs (engines_service.py): they run the CLI as the connection's CLI
profile, validate and plan skip the audit, deploy is audited and needs the target typed for a
plan that deletes or rebuilds."""

from __future__ import annotations

import pytest

from fabric_desktop import audit, connprofiles, server
from fabric_desktop import engines_service as es
from fabric_desktop.engines import databricks_bundles as db

from test_databricks_bundles import PLAN, SAFE, YML, FakeCli
from test_databricks_discovery import HOST

CLI = {"path": "x", "found": True, "profiles": [
    {"name": "dev", "host": HOST, "auth_type": "databricks-cli", "cluster_id": "", "warehouse_id": "", "serverless": False, "warning": ""}]}
READS = {"databricks_bundle_targets", "databricks_bundle_validate", "databricks_bundle_plan"}


class Svc(es.EnginesMixin):
    def __init__(self, tmp_path, cli):
        self._tmp = tmp_path
        self._bundle_run = cli

    def _profiles(self):
        (self._tmp / "profiles").mkdir(exist_ok=True)
        return connprofiles.ProfileStore(self._tmp / "profiles")


@pytest.fixture(autouse=True)
def cli(monkeypatch):
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: CLI)
    monkeypatch.setattr(db, "find_cli", lambda: "/usr/bin/databricks")


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "databricks.yml").write_text(YML)
    return str(root)


def test_plan_and_validate_are_read_only_and_deploy_is_audited():
    assert READS | {"databricks_bundle_deploy"} <= es.METHODS <= server.METHODS
    assert READS <= audit.READ_ONLY and "databricks_bundle_deploy" not in audit.READ_ONLY


def test_the_cli_runs_as_the_connections_profile(tmp_path, folder):
    fake = FakeCli([SAFE])
    svc = Svc(tmp_path, fake)
    assert [t["name"] for t in svc.databricks_bundle_targets(folder)["targets"]] == ["dev", "test", "prod"]
    assert svc.databricks_bundle_validate(folder, "dev", "cli:dev")["ok"]
    out = svc.databricks_bundle_plan(folder, "dev", "cli:dev")
    assert out["ok"] and not out["needs_typed_confirm"]
    assert all(c[-2:] == ["--profile", "dev"] for c in fake.calls)
    assert svc.databricks_bundle_deploy(folder, "dev", "cli:dev", out["digest"])["ok"]
    assert fake.verbs() == ["validate", "plan", "plan", "deploy"]


def test_a_destructive_plan_needs_the_target_typed(tmp_path, folder):
    fake = FakeCli([PLAN])
    svc = Svc(tmp_path, fake)
    out = svc.databricks_bundle_plan(folder, "test", "cli:dev")
    assert out["needs_typed_confirm"] and out["destructive"] == 2
    with pytest.raises(ValueError, match="Type test"):
        svc.databricks_bundle_deploy(folder, "test", "cli:dev", out["digest"], typed="tset")
    assert "deploy" not in fake.verbs()


def test_a_connection_without_a_cli_profile_is_refused(tmp_path, folder):
    svc = Svc(tmp_path, FakeCli())
    with pytest.raises(ValueError):
        svc.databricks_bundle_plan(folder, "dev", "cli:nope")
    oauth = {"platform": "databricks", "host": HOST, "credential": {"method": "oauth-u2m"}}
    svc._connection = lambda key: (oauth, {}, "P")
    with pytest.raises(ValueError, match="pick a connection that signs in with a CLI profile"):
        svc.databricks_bundle_plan(folder, "dev", "profile:P:x")
    assert svc._bundle_run.calls == []


def test_the_rpc_holds_a_bundle_with_scripts_until_allowed(tmp_path, folder):
    from test_databricks_bundles import SCRIPTS
    (tmp_path / "bundle" / "databricks.yml").write_text(SCRIPTS)
    fake = FakeCli([SAFE])
    svc = Svc(tmp_path, fake)
    held = svc.databricks_bundle_plan(folder, "dev", "cli:dev")
    assert held["needs_confirm"] and fake.calls == []
    assert svc.databricks_bundle_plan(folder, "dev", "cli:dev", allow_commands=held["commands_digest"])["ok"]
