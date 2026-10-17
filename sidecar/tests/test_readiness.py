"""Home's Ready to work checklist (FD-136): read-only checks, and a write test that asks first."""

import pytest

from fabric_desktop import readiness
from fabric_desktop.server import METHODS

WS = [{"id": "a", "displayName": "Sales - Dev", "capacityId": "CAP1"}, {"id": "b", "displayName": "My workspace"}]
SETTINGS = {"channel": {"value": "stable", "source": "policy"}, "show_preview": {"value": True, "source": "default"}}


def by_key(rows):
    return {r["key"]: r for r in rows}


def test_signed_out_waits_and_offers_sign_in():
    rows = by_key(readiness.checks(None, lambda: pytest.fail("no Fabric call when signed out"), list, {}))
    assert rows["sign_in"]["status"] == "fail" and rows["sign_in"]["fix"] == "sign_in"
    assert rows["fabric"]["status"] == rows["capacity"]["status"] == "wait"
    assert rows["engine"]["status"] == "ok"


def test_all_good():
    rows = by_key(readiness.checks("pat@contoso.com", lambda: WS, lambda: [{"id": "cap1", "state": "Active"}], SETTINGS))
    assert [r["status"] for r in rows.values()] == ["ok"] * 5
    assert rows["fabric"]["text"] == "You can see 2 workspaces."
    assert rows["policy"]["text"] == "Your organization sets channel."


def test_capacity_missing_paused_or_unreadable():
    rows = by_key(readiness.checks("pat", lambda: [WS[1]], list, {}))
    assert rows["capacity"]["status"] == "fail" and "Notebooks and SQL on this PC still work" in rows["capacity"]["text"]
    rows = by_key(readiness.checks("pat", lambda: WS, lambda: [{"id": "cap1", "state": "Paused"}], {}))
    assert rows["capacity"]["status"] == "fail" and "paused" in rows["capacity"]["text"]

    def refused():
        raise PermissionError("not consented")
    rows = by_key(readiness.checks("pat", lambda: WS, refused, {}))
    assert rows["capacity"]["status"] == "ok" and "isn't visible" in rows["capacity"]["text"]


def test_fabric_errors_and_policy_problems_are_shown():
    def down():
        raise ConnectionError("timed out")
    rows = by_key(readiness.checks("pat", down, list, {"policy_problems": ["chanel isn't a setting"]}))
    assert rows["fabric"]["status"] == "fail" and "timed out" in rows["fabric"]["text"]
    assert "capacity" not in rows
    assert rows["policy"]["status"] == "warn" and "chanel" in rows["policy"]["text"]


class Store:
    def __init__(self, corrupt=False):
        self.files, self.calls, self.corrupt = {}, [], corrupt

    def upload(self, ws, lh, path, data):
        self.calls.append(("upload", path))
        self.files[path] = data

    def read_bytes(self, ws, lh, path):
        return b"other" if self.corrupt else self.files.get(path)

    def delete(self, ws, lh, path):
        self.calls.append(("delete", path))
        self.files = {k: v for k, v in self.files.items() if not k.startswith(path)}


def test_write_test_writes_reads_and_cleans_up():
    store = Store()
    assert readiness.write_test(store, "ws", "lh", "Sales - Dev")["status"] == "ok"
    assert store.calls == [("upload", readiness.CHECK_FILE), ("delete", readiness.CHECK_FOLDER)]
    assert store.files == {}
    store = Store(corrupt=True)
    assert readiness.write_test(store, "ws", "lh", "Sales - Dev")["status"] == "fail"
    assert store.files == {}


def test_write_test_never_touches_production():
    for name in ("Sales - Prod", "PRD finance", "production"):
        store = Store()
        with pytest.raises(PermissionError, match="production"):
            readiness.write_test(store, "ws", "lh", name)
        assert store.calls == []
    assert readiness.write_test_refusal("Products - Dev") is None  # "Products" isn't "prod"


def test_rpc_methods_are_allowed():
    assert {"readiness_check", "readiness_write_test"} <= METHODS
