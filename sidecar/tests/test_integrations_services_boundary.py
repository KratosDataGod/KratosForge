"""Boundary kill test for integrations/services (mutation testing, batch 168)."""
from __future__ import annotations

import types

from fabric_desktop.integrations import services


def test_a_check_with_no_failed_rows_is_not_counted_as_failed(monkeypatch):
    monkeypatch.setattr(services, "_open_metadata", lambda svc: None)
    svc = types.SimpleNamespace(meta_run_detail=lambda run_id, env: {
        "run_id": run_id, "dq": [{"check": "a", "failed": 0}, {"check": "b", "failed": 2}, {"check": "c"}]})
    assert services.get_project_run(svc, None, "r1")["quality_failed"] == 1
