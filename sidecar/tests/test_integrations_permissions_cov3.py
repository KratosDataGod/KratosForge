"""integrations/permissions.py: categories never offered, a call with no caller, and approvals that were
already used or expired. A fake store; nothing waits."""

from types import SimpleNamespace

import pytest

from fabric_desktop.integrations import permissions
from fabric_desktop.integrations.core import IntegrationError


def _cap(permission="READ_METADATA"):
    return SimpleNamespace(id="run_notebook", title="Run a notebook", permission=permission, public=False,
                           status="available", reason="")


def test_credentials_are_never_a_category_a_tool_gets():
    auth = permissions.PermissionAuthorizer(store=None)
    ok, why = auth._category(_cap("MANAGE_CONNECTIONS"), {"grants": ["MANAGE_CONNECTIONS"], "workspaces": []})
    assert ok is False and "never gives connections or credentials" in why


def test_no_call_context_is_no_client():
    class Store:
        def client(self, client_id):
            raise AssertionError("looked up a client without a caller")
    assert permissions.PermissionAuthorizer(Store())._client(None) is None


class _Store:
    def __init__(self, state, consumed=False):
        self.state, self.consumed = state, consumed

    def request_approval(self, client, cap_id, title, permission, summary, arguments):
        return {"id": "a1", "state": "pending", "expires_at": "later"}

    def approval(self, approval_id):
        return {"id": approval_id, "state": self.state, "expires_at": "later"}

    def consume(self, approval_id, client_id, digest):
        return self.consumed


@pytest.mark.parametrize("state, consumed", [("approved", False), ("expired", False), ("used", False)])
def test_an_approval_already_used_or_expired_asks_to_call_again(monkeypatch, state, consumed):
    monkeypatch.setattr(permissions, "summary", lambda cap, client, arguments: {"title": cap.title})
    auth = permissions.PermissionAuthorizer(_Store(state, consumed), wait_seconds=0, poll=0)
    reports = []
    ctx = SimpleNamespace(remaining=lambda: 5.0, report=lambda *a: reports.append(a), cancelled=False, approval_id=None)
    with pytest.raises(IntegrationError) as caught:
        auth._approve(_cap(), {"id": "c1"}, {"notebook": "nb"}, ctx)
    assert caught.value.code == "approval_required" and caught.value.retryable is True
    assert caught.value.details == {"approval_id": "a1"}  # the code and id, not the wording, which may change
    assert ctx.approval_id is None and reports == [(0, None, "Waiting for approval in KratosForge")]


def test_an_approval_consumed_once_lets_the_call_through(monkeypatch):
    monkeypatch.setattr(permissions, "summary", lambda cap, client, arguments: {})
    auth = permissions.PermissionAuthorizer(_Store("approved", consumed=True), wait_seconds=0, poll=0)
    ctx = SimpleNamespace(remaining=lambda: 0.0, report=lambda *a: None, cancelled=False, approval_id=None)
    auth._approve(_cap(), {"id": "c1"}, {}, ctx)
    assert ctx.approval_id == "a1"
