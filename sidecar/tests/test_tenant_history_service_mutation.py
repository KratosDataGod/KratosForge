"""Mutation-testing follow-up for tenant_history_service.py and capacity_history_service.py
(docs/quality/mutation-testing.md, batch 70).

Each test pins a behaviour that a surviving mutant changed without any test noticing.
"""

from __future__ import annotations

import pytest

from fabric_desktop.fabric_api import FabricError
from fabric_desktop.service import Service
from test_capacity_history import caps  # noqa: F401 - the capacities fixture
from test_tenant_history import svc  # noqa: F401 - the central lakehouse fixture


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def failing(monkeypatch, method, message, status):
    def raise_it(client):
        raise FabricError(message, status=status)
    monkeypatch.setattr(f"fabric_desktop.fabric_api.FabricClient.{method}", raise_it)


@pytest.mark.parametrize(("message", "status", "outcome"), [
    ("Unauthorized", 401, "unreadable"),
    ("Forbidden", 403, "unreadable"),
    ("GET /admin/tenantsettings failed with 403: Forbidden", None, "unreadable"),
    ("GET /admin/tenantsettings failed with 401: Unauthorized", None, "unreadable"),
    ("Not found", 404, "failed"),
    ("Payment required", 402, "failed"),
    ("GET /admin/tenantsettings failed with 4031: odd", None, "failed"),
])
def test_tenant_settings_a_401_or_403_by_status_or_message_is_unreadable(svc, monkeypatch, message, status, outcome):  # noqa: F811
    svc.tenant_settings_read(approve=True)
    failing(monkeypatch, "list_tenant_settings", message, status)
    out = svc.tenant_settings_read()
    assert out["outcome"] == outcome and out["changes"] == []
    assert ("Fabric administrator" in out["reason"]) is (outcome == "unreadable")


def test_tenant_settings_without_a_known_tenant_ask_to_sign_in_again(svc, monkeypatch):  # noqa: F811
    svc.tenant_settings_read(approve=True)
    monkeypatch.setattr(type(svc.auth), "signed_in_tenant", lambda self: None)
    with pytest.raises(RuntimeError, match="Sign in again"):
        svc.tenant_settings_read()


@pytest.mark.parametrize(("status", "outcome"), [(401, "unreadable"), (403, "unreadable"), (404, "failed")])
def test_capacities_a_401_or_403_is_unreadable(caps, monkeypatch, status, outcome):  # noqa: F811
    failing(monkeypatch, "list_capacities", f"GET /capacities failed with {status}", status)
    assert caps.capacity_settings_read(approve=True)["outcome"] == outcome


@pytest.mark.parametrize(("status", "outcome"), [(401, "unreadable"), (403, "unreadable"), (404, "failed")])
def test_capacity_overrides_a_401_or_403_is_unreadable(caps, monkeypatch, status, outcome):  # noqa: F811
    failing(monkeypatch, "list_capacity_tenant_setting_overrides", f"GET failed with {status}", status)
    out = caps.capacity_settings_read(approve=True, admin=True)
    assert out["outcome"] == "read" and out["overrides"] == outcome


# ---- the history, read from stand-in tables so each ordering and limit can be set exactly

def history(svc, monkeypatch, tables, conn=None):  # noqa: F811
    asked = {}
    monkeypatch.setattr(Service, "central_lakehouse", lambda self: conn or {"workspace_id": "w1", "lakehouse_id": "lh"})

    def lake(self, ws, lh, schemas=None):
        asked["lake"] = (ws, lh, schemas)
        return "lake"
    monkeypatch.setattr(Service, "_optimize_lake", lake)
    monkeypatch.setattr(Service, "_rows", staticmethod(lambda lake, name: [dict(r) for r in tables.get(name, [])]))
    return svc.tenant_settings_history(), asked


def change(n, at):
    return {"scope_type": "tenant", "scope_id": "t", "setting": f"S{n}", "changed_after": None,
            "changed_by_time": at, "old_json": None, "new_json": "1"}


def test_history_shows_200_changes_newest_first_by_default(svc, monkeypatch):  # noqa: F811
    rows = [change(n, f"2026-01-01T00:{n // 60:02d}:{n % 60:02d}Z") for n in range(201)]
    out, _ = history(svc, monkeypatch, {"fdten_change": rows})
    assert out["connected"] is True
    assert len(out["changes"]) == 200 and out["changes"][0]["setting"] == "S200"
    assert out["changes"][-1]["setting"] == "S1"


def test_history_reads_the_lakehouse_with_schemas_unless_it_says_otherwise(svc, monkeypatch):  # noqa: F811
    _, asked = history(svc, monkeypatch, {})
    assert asked["lake"] == ("w1", "lh", True)
    _, asked = history(svc, monkeypatch, {}, conn={"workspace_id": "w2", "lakehouse_id": "lh2", "schemas": False})
    assert asked["lake"] == ("w2", "lh2", False)


def test_the_last_read_is_the_newest_successful_one_and_older_failures_arent_gaps(svc, monkeypatch):  # noqa: F811
    reads = [{"read_at": "2026-01-01T00:00:00Z", "scope_type": "tenant", "outcome": "read"},
             {"read_at": "2026-01-02T00:00:00Z", "scope_type": "tenant", "outcome": "failed", "reason": "old"},
             {"read_at": "2026-01-03T00:00:00Z", "scope_type": "tenant", "outcome": "read"},
             {"read_at": "2026-01-03T00:00:00Z", "scope_type": "tenant", "outcome": "failed", "reason": "same time"},
             {"read_at": "2026-01-04T00:00:00Z", "scope_type": "tenant", "outcome": "unreadable", "reason": "new"}]
    out, _ = history(svc, monkeypatch, {"fdten_read": reads})
    assert out["last_read"] == {"tenant": "2026-01-03T00:00:00Z"}
    assert [g["reason"] for g in out["gaps"]] == ["new"]
