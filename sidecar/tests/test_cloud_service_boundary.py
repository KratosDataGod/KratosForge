"""Boundary kill test for cloud_service (mutation testing, batch 166)."""
from __future__ import annotations

import types

from test_cloud_service_mutation import GUID_LH, GUID_WS, TokenService


def test_a_table_with_no_files_is_not_called_v_ordered(tmp_path, monkeypatch):
    from fabric_desktop import cloud_service

    class Files:
        def __init__(self, token):
            pass

        def read_bytes(self, *a):
            return b"{}"
    s = TokenService(root=tmp_path)
    monkeypatch.setattr(TokenService, "auth", property(lambda self: types.SimpleNamespace(storage_token=lambda: "tok")))
    monkeypatch.setattr(cloud_service, "FileStore", Files)
    monkeypatch.setattr(s, "_schemas_enabled", lambda ws, lh: True, raising=False)
    monkeypatch.setattr(cloud_service.maintenance, "health", lambda uri, opts: {"exists": True, "files": 0,
                                                                                "last_optimize": None})
    monkeypatch.setattr(cloud_service.maintenance, "vorder_files", lambda uri, opts: {"files": 0, "v_ordered": 0,
                                                                                       "checked": 0})
    s._log_fabric_run({"kind": "Fabric step", "request_id": "r1", "job_id": "j1", "status": "Completed",
                       "workspace_id": GUID_WS, "item_id": "i", "lakehouse_id": GUID_LH, "tables": ["dbo.empty"]})
    assert s.notebook_fabric_step_result("r1")["tables"][0]["v_ordered"] is False
