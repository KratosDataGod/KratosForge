"""One workspace per environment, and jobs can't write around the framework (HD-036, findings EP-8, EP-9)."""

import pytest

from fabric_desktop import publish
from fabric_desktop.jobs import Job, Target
from fabric_desktop.meta_service import shared_workspaces
from fabric_desktop.service import Service

@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")
    return s

def test_two_environments_can_not_share_a_workspace(svc):
    svc.meta_set_environment("dev", "ws-dev", "Sales - Dev")
    svc.meta_set_environment("test", "ws-test", "Sales - Test")
    before = (svc._project().root / "environments.toml").read_text()
    with pytest.raises(ValueError, match="Dev and Prod both use workspace ws-dev"):
        svc.meta_set_environment("prod", "ws-dev", "Sales - Dev")
    with pytest.raises(ValueError, match="Dev and Test both use workspace ws-test"):
        svc.meta_set_environment("dev", "ws-test", "Sales - Test")
    assert (svc._project().root / "environments.toml").read_text() == before  # nothing was saved
    svc.meta_set_environment("prod", "ws-prod", "Sales - Prod")  # its own workspace is fine

def test_a_designation_workspace_counts_too(svc):
    svc.meta_set_environment("prod", "ws-prod", "Sales - Prod")
    with pytest.raises(ValueError, match="Dev and Prod both use workspace ws-prod"):
        svc.meta_set_designation("dev", "product", "ws-prod", "Sales - Prod")
    assert shared_workspaces({"dev": {"workspace_id": "a", "workspaces": {"ingest": {"id": "b"}}},
                              "test": {"workspace_id": "c", "workspaces": {"product": {"id": "b"}}}})
    assert not shared_workspaces({"dev": {"workspace_id": "a"}, "test": {"workspace_id": "b"}, "prod": {}})

def test_a_hand_edited_file_that_shares_a_workspace_is_refused_before_any_read(svc, monkeypatch):
    (svc._project().root / "environments.toml").write_text(
        '[dev]\ntarget = "local"\n\n[test]\nworkspace_id = "ws-test"\n\n[prod]\nworkspace_id = "ws-test"\n')
    calls = []
    fake = type("F", (), {"list_lakehouses": lambda self, ws: calls.append(ws) or []})()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    with pytest.raises(ValueError, match="Test and Prod both use workspace ws-test"):
        svc._store(svc._project(), "prod")
    assert not calls  # refused before Fabric was asked for anything

def _job(tmp_path, workspace_id, lakehouse="LH_Scratch", table="t"):
    return Job("j", "sql", tmp_path, Target(workspace_id, lakehouse, table), sql="SELECT 1")

def test_a_job_never_writes_test_or_prod(svc, tmp_path, monkeypatch):
    svc.meta_set_environment("test", "ws-test", "Sales - Test")
    ran = []
    monkeypatch.setattr(publish, "run_local", lambda job: ran.append(job))
    with pytest.raises(PermissionError, match="Test's, which changes only by promote"):
        publish.run_job(_job(tmp_path, "ws-test"), None, lambda: "tok", tmp_path, guard=svc._job_guard)
    assert not ran  # nothing ran, nothing was written

def test_a_job_never_writes_a_table_the_framework_manages(svc, tmp_path, monkeypatch):
    project = svc._project()
    e = next(e for e in project.entities.values() if e.layer in project.layer_names())
    lakehouse = project.layer(e.layer).lakehouse
    ran = []
    monkeypatch.setattr(publish, "run_local", lambda job: ran.append(job))
    job = Job("j", "sql", tmp_path, Target("ws-scratch", lakehouse, e.table, e.schema), sql="SELECT 1")
    with pytest.raises(PermissionError, match="managed by the metadata project"):
        publish.run_job(job, None, lambda: "tok", tmp_path, guard=svc._job_guard)
    assert not ran
    assert svc._job_guard("ws-scratch", "LH_Scratch", "dbo", "t") is None  # anything else may be written

def test_without_a_project_jobs_run_as_before(tmp_path):
    s = Service(root=tmp_path / "app")
    assert s._job_guard("ws-any", "LH_Bronze", "dbo", "orders") is None
