"""The app's incident calls (incident_service.py): an edge an uncapped mutation run found unchecked
(batch 254, docs/quality/mutation-testing.md). A workload's criticality is keyed by everything after
``meta:<project>:``, even a name that holds a colon itself."""

import types

import pytest

from fabric_desktop.meta import mesh
from fabric_desktop.service import Service
from fabric_desktop.workloads import evaluate


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.root.mkdir(parents=True, exist_ok=True)
    s._auth = types.SimpleNamespace(
        signed_in_user=lambda: "chris@example.com",
        silent_token=lambda scopes: None,
        fabric_token=lambda: None,
        storage_token=lambda: None,
    )
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def test_a_criticality_is_keyed_by_the_whole_name_after_the_project(svc, monkeypatch):
    name = mesh.project_name(svc._project())
    svc.workload_store.assign(f"meta:{name}:silver.orders:eu", {"criticality": "high"})
    seen = {}

    def signals(project, specs, runs, env, criticality=None, **kw):
        seen.update(criticality or {})
        return []

    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: [])
    monkeypatch.setattr(evaluate, "signals", signals)
    svc.incidents_evaluate("dev")
    assert seen == {"silver.orders:eu": "high"}
