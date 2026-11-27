"""The integration audit trail at its edges: a system with no user name still records every call (with an
empty os_user), and arguments that can't be walked keep the call's line with "(given)" in their place."""

from __future__ import annotations

import time

import pytest

from fabric_desktop.integrations import audit as audit_mod
from fabric_desktop.integrations.audit import GIVEN, IntegrationAudit, arguments
from fabric_desktop.integrations.core import CallContext, Caller
from fabric_desktop.integrations.store import IntegrationStore


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))


@pytest.fixture
def store(tmp_path):
    return IntegrationStore(tmp_path / "integrations" / "integrations.sqlite")


def test_no_user_name_on_this_system_still_records_the_call(store, monkeypatch):
    def nobody():
        raise KeyError("getpwuid(): uid not found: 1000")
    monkeypatch.setattr(audit_mod.getpass, "getuser", nobody)
    trail = IntegrationAudit(store)
    assert trail.os_user == ""
    entry = trail.record(CallContext(Caller("", "api", "curl")), "list_workspaces", "READ_METADATA",
                         {"workspace_id": "ws1"}, time.time(), 0.01)
    assert entry["os_user"] == "" and entry["client"] == "curl" and entry["outcome"] == "ok"
    assert trail.recent(1)[0]["hash"] == entry["hash"]
    assert trail.verify()["ok"] is True


def test_arguments_too_deep_for_python_to_walk_are_given_not_lost(monkeypatch):
    def too_deep_to_walk(values):
        raise RecursionError("maximum recursion depth exceeded")
    monkeypatch.setattr(audit_mod, "target", too_deep_to_walk)
    assert arguments({"workspace_id": "ws1"}) == {"arguments": GIVEN}


def test_arguments_that_arent_an_object_are_given():
    assert arguments(["ws1"]) == {"arguments": GIVEN}  # type: ignore[arg-type]
    assert arguments(None) == {}
