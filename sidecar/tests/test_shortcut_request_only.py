"""Only approving a shortcut request skips the "Only platform admins" rule: create_shortcut, as the
app and other local callers reach it, takes no parameter that does."""

import inspect
import json

import pytest

from fabric_desktop import rpc_params, server
from fabric_desktop.service import Service

SRC = {"type": "oneLake", "workspace_id": "ws-prod", "item_id": "lh-gold", "path": "Tables/dbo/product"}
PARAMS = {"workspace_id": "ws-dev", "item_id": "lh-1", "where": "Tables/dbo", "name": "product", "source": SRC}


def test_create_shortcut_takes_no_request_flag():
    assert "via_request" not in inspect.signature(Service.create_shortcut).parameters
    with pytest.raises(TypeError, match="unknown parameter via_request"):
        rpc_params.check("create_shortcut", Service.create_shortcut, PARAMS | {"via_request": True})


def test_the_engine_refuses_the_flag_before_anything_runs(monkeypatch):
    calls = []
    monkeypatch.setattr(Service, "_create_shortcut", lambda self, *a, **k: calls.append(k))
    svc = Service.__new__(Service)  # the call is refused before the service is used
    reply = server.handle(svc, json.dumps({"id": 1, "method": "create_shortcut", "params": PARAMS | {"via_request": True}}))
    assert "unknown parameter via_request" in reply["error"] and not calls


def test_the_private_path_is_not_an_engine_method():
    assert "_create_shortcut" not in server.METHODS and "create_shortcut" in server.METHODS
    assert inspect.signature(Service._create_shortcut).parameters["via_request"].kind is inspect.Parameter.KEYWORD_ONLY
