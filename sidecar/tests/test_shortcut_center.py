"""The shortcut command center: history from scans and app changes, health, rules, requests."""

import time

import pytest

from fabric_desktop import estate_service, shortcut_center as center, shortcut_service, shortcuts
from fabric_desktop.central import core
from fabric_desktop.central.client import LocalPlatform
from fabric_desktop.service import Service

LH = {"workspace_id": "ws-dev", "lakehouse_id": "lh-1", "name": "LH_Silver", "workspace_name": "Sales Dev"}
GOLD = {"oneLake": {"workspaceId": "ws-prod", "itemId": "lh-gold", "path": "Tables/dbo/customer"}}
GOLD_DEV = {"oneLake": {"workspaceId": "ws-dev2", "itemId": "lh-gold-dev", "path": "Tables/dbo/customer"}}
S3 = {"amazonS3": {"location": "https://bucket.s3.amazonaws.com", "subpath": "/events", "connectionId": "c1"}}


class FakeApi:
    """OneLake shortcuts in memory, by lakehouse."""

    def __init__(self):
        self.items = {"lh-1": [{"name": "customer", "path": "Tables/dbo", "target": GOLD},
                               {"name": "events", "path": "Files/raw", "target": S3}]}
        self.fail_create = 0

    def list_shortcuts(self, ws, item):
        return [dict(s) for s in self.items.get(item, [])]

    def create_shortcut(self, ws, item, path, name, target):
        if self.fail_create:
            self.fail_create -= 1
            raise RuntimeError("400 target not found")
        if any(s["path"] == path and s["name"] == name for s in self.items.get(item, [])):
            raise RuntimeError("409 exists")
        self.items.setdefault(item, []).append({"name": name, "path": path, "target": target})
        return {}

    def delete_shortcut(self, ws, item, path, name):
        self.items[item] = [s for s in self.items.get(item, []) if not (s["path"] == path.strip("/") and s["name"] == name)]

    def list_data_access_roles(self, ws, item):
        return []


class Auth:
    """A signed-in person, without Microsoft."""

    def _token(self, scopes):
        return "tok"

    def storage_token(self):
        return "tok"

    def signed_in_user(self):
        return ""

    def silent_token(self, scopes):
        return "tok"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    api = FakeApi()
    s._auth = Auth()
    s.api = api
    monkeypatch.setattr(type(s), "_scoped", lambda self, scopes: api)
    monkeypatch.setattr(estate_service, "FabricClient", lambda token: api)
    monkeypatch.setattr(shortcut_service, "FabricClient", lambda token: api)
    return s


def center_of(svc, refresh=False):
    out = svc.shortcut_center([LH], refresh=refresh)
    for _ in range(200):
        if not out.get("pending"):
            return out
        time.sleep(0.02)
        out = svc.shortcut_center([LH])
    raise AssertionError("never finished")


def test_first_scan_is_the_baseline_and_later_scans_find_changes_made_elsewhere(svc):
    out = center_of(svc)
    assert out["counts"]["total"] == 2 and out["counts"]["connection"] == 1
    assert svc.shortcut_changes()["changes"] == []  # what was there before isn't "added"

    svc.api.items["lh-1"].append({"name": "orders", "path": "Tables/dbo", "target": GOLD_DEV})
    svc.api.items["lh-1"][0]["target"] = GOLD_DEV
    svc.api.items["lh-1"] = [s for s in svc.api.items["lh-1"] if s["name"] != "events"]
    center_of(svc, refresh=True)
    changes = {(c["name"], c["kind"], c["how"]) for c in svc.shortcut_changes()["changes"]}
    assert changes == {("orders", "added", "scan"), ("customer", "repointed", "scan"), ("events", "removed", "scan")}
    rows = {r["name"]: r for r in center_of(svc)["rows"]}
    assert rows["customer"]["last_change"]["kind"] == "repointed"


def test_app_changes_are_recorded_once_with_who_and_can_be_undone(svc):
    center_of(svc)
    svc.delete_shortcut("ws-dev", "lh-1", "Files/raw", "events", "LH_Silver", "Sales Dev")
    center_of(svc, refresh=True)
    changes = svc.shortcut_changes()["changes"]
    assert [(c["kind"], c["how"]) for c in changes] == [("removed", "app")]  # the scan didn't count it again
    assert changes[0]["before"]["target"] == S3

    svc.shortcut_undo(changes[0]["id"])
    assert any(s["name"] == "events" and s["target"] == S3 for s in svc.api.items["lh-1"])
    assert svc.shortcut_changes()["changes"][0]["kind"] == "added"


def test_repoint_puts_the_old_shortcut_back_when_the_new_one_fails(svc):
    src = {"type": "oneLake", "workspace_id": "ws-dev2", "item_id": "lh-gold-dev", "path": "Tables/dbo/customer"}
    svc.api.fail_create = 1
    with pytest.raises(RuntimeError, match="put back as it was"):
        svc.shortcut_repoint("ws-dev", "lh-1", "Tables/dbo", "customer", src, "LH_Silver", "Sales Dev", "Sales Dev 2")
    assert next(s for s in svc.api.items["lh-1"] if s["name"] == "customer")["target"] == GOLD

    out = svc.shortcut_repoint("ws-dev", "lh-1", "Tables/dbo", "customer", src, "LH_Silver", "Sales Dev", "Sales Dev 2")
    assert out["ok"] and next(s for s in svc.api.items["lh-1"] if s["name"] == "customer")["target"] == GOLD_DEV
    change = svc.shortcut_changes()["changes"][0]
    assert change["kind"] == "repointed" and change["before"]["target"] == GOLD
    svc.shortcut_undo(change["id"])
    assert next(s for s in svc.api.items["lh-1"] if s["name"] == "customer")["target"] == GOLD



def test_undo_leaves_a_shortcut_that_changed_again_since_alone(svc):
    center_of(svc)
    svc.delete_shortcut("ws-dev", "lh-1", "Files/raw", "events", "LH_Silver", "Sales Dev")
    removed = svc.shortcut_changes()["changes"][0]
    svc.api.items["lh-1"].append({"name": "events", "path": "Files/raw", "target": GOLD})  # made again in Fabric
    with pytest.raises(RuntimeError, match="there's a shortcut at Files/raw/events again"):
        svc.shortcut_undo(removed["id"])
    assert [s["target"] for s in svc.api.items["lh-1"] if s["name"] == "events"] == [GOLD]
    # Undoing an add removes only the shortcut it added, not one pointed elsewhere since.
    svc.api.items["lh-1"] = [s for s in svc.api.items["lh-1"] if s["name"] != "events"]
    svc.shortcut_undo(removed["id"])
    added = svc.shortcut_changes()["changes"][0]
    assert added["kind"] == "added"
    next(s for s in svc.api.items["lh-1"] if s["name"] == "events")["target"] = GOLD_DEV
    with pytest.raises(RuntimeError, match="points somewhere else now, so nothing was removed"):
        svc.shortcut_undo(added["id"])
    assert any(s["name"] == "events" for s in svc.api.items["lh-1"])
    # A repoint is pointed back only while the shortcut still points where that repoint left it.
    src = {"type": "oneLake", "workspace_id": "ws-dev2", "item_id": "lh-gold-dev", "path": "Tables/dbo/customer"}
    svc.shortcut_repoint("ws-dev", "lh-1", "Tables/dbo", "customer", src, "LH_Silver", "Sales Dev", "Sales Dev 2")
    repoint = svc.shortcut_changes()["changes"][0]
    next(s for s in svc.api.items["lh-1"] if s["name"] == "customer")["target"] = S3
    with pytest.raises(RuntimeError, match="points somewhere else since that change"):
        svc.shortcut_undo(repoint["id"])
    assert next(s for s in svc.api.items["lh-1"] if s["name"] == "customer")["target"] == S3

def test_rules_stop_dev_pointing_at_prod_and_flag_it(svc):
    src = {"type": "oneLake", "workspace_id": "ws-prod", "item_id": "lh-gold", "path": "Tables/dbo/product"}
    with pytest.raises(PermissionError, match="Dev at Prod"):
        svc.create_shortcut("ws-dev", "lh-1", "Tables/dbo", "product", src, "LH_Silver", "Sales Dev", "Sales Prod")
    svc.shortcut_save_rules({"cross_env": "warn"})
    assert svc.shortcut_preflight("ws-dev", src, "Sales Dev", "Sales Prod")["warnings"]
    svc.create_shortcut("ws-dev", "lh-1", "Tables/dbo", "product", src, "LH_Silver", "Sales Dev", "Sales Prod")
    assert svc.shortcut_changes()["changes"][0]["name"] == "product"


def test_only_admins_may_save_rules_and_may_require_requests(tmp_path):
    p = LocalPlatform(tmp_path / "p.sqlite", lambda: "chris@contoso.com")
    with pytest.raises(core.PlatformError):
        p.call("save_shortcut_rules", rules={"cross_env": "sometimes"})
    p.call("save_shortcut_rules", rules={"who_creates": "admins"})
    other = LocalPlatform(tmp_path / "p.sqlite", lambda: "lee@contoso.com")
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        other.call("save_shortcut_rules", rules={"who_creates": "anyone"})
    rules = other.call("shortcut_rules")
    out = center.check_rules(rules, {"target_type": "OneLake"}, is_admin=False)
    assert out["blocked"] and "request" in out["blocked"][0]


def test_a_shortcut_request_is_approved_only_once_the_shortcut_exists(svc):
    src = {"type": "oneLake", "workspace_id": "ws-dev2", "item_id": "lh-gold-dev", "path": "Tables/dbo/supplier"}
    req = svc.shortcut_request("ws-dev", "lh-1", "Tables/dbo", "supplier", src, "Need suppliers", "LH_Silver", "Sales Dev")
    assert req["kind"] == "shortcut" and req["target"] == "lh-1|Tables/dbo/supplier"
    with pytest.raises(core.PlatformError, match="already a request"):
        svc.shortcut_request("ws-dev", "lh-1", "Tables/dbo", "supplier", src, "", "LH_Silver", "Sales Dev")
    svc.api.fail_create = 1
    with pytest.raises(RuntimeError):
        svc.platform_decide(req["id"], True)
    assert svc.platform_requests("waiting")[0]["id"] == req["id"]  # still waiting: nothing was made
    out = svc.platform_decide(req["id"], True, "ok")
    assert out["status"] == "approved" and any(s["name"] == "supplier" for s in svc.api.items["lh-1"])
    assert "Request from" in svc.shortcut_changes()["changes"][0]["note"]


def test_health_checks_record_breaks_and_fixes(svc, monkeypatch):
    statuses = {"Tables/dbo": 200, "Files/raw": 200, "Tables/dbo/customer": 404, "Files/raw/events": 403}

    class Files:
        def __init__(self, token):
            pass

        def probe(self, ws, lh, path):
            return statuses[path], "nope"
    monkeypatch.setattr(shortcut_service, "FileStore", Files)
    rows = center_of(svc)["rows"]
    out = svc.shortcut_check(rows)
    by = {r["id"]: r for r in out["results"]}
    assert by["lh-1|Tables/dbo/customer"]["health"] == "broken" and "isn't there" in by["lh-1|Tables/dbo/customer"]["detail"]
    assert by["lh-1|Files/raw/events"]["health"] == "broken" and "connection" in by["lh-1|Files/raw/events"]["detail"]
    statuses["Tables/dbo/customer"] = 200
    svc.shortcut_check(rows)
    kinds = [(c["name"], c["kind"]) for c in svc.shortcut_changes()["changes"]]
    assert ("customer", "broke") in kinds and ("customer", "fixed") in kinds
    after = {r["name"]: r for r in center_of(svc)["rows"]}
    assert after["customer"]["health"] == "healthy" and after["events"]["health"] == "broken"
    assert after["events"]["health"] == "broken" and list(after) == ["events", "customer"]  # broken first


def test_probe_results_mean_what_they_say():
    one = {"target_type": "OneLake"}
    assert center.health_from_probe(one, 403, None)["health"] == "not_checked"
    assert center.health_from_probe(one, 200, 403)["health"] == "unreachable"
    assert center.health_from_probe({"target_type": "ADLS Gen2"}, 200, 403)["health"] == "broken"
    assert center.health_from_probe(one, 200, None, "timeout")["health"] == "not_checked"
    for busy in (429, 500, 503, 504):  # OneLake busy after the probe's retries: not the shortcut's fault
        assert center.health_from_probe(one, 200, busy, "ServerBusy") == {
            "health": "not_checked",
            "detail": f"OneLake was busy (it answered {busy}), so this wasn't checked. It's checked again next time"}
    for busy in (429, 503):  # busy whatever the target
        assert center.health_from_probe({"target_type": "ADLS Gen2"}, 200, busy)["health"] == "not_checked"
    for failed in (500, 502, 504):  # an external target's storage failing can be a real break
        assert center.health_from_probe({"target_type": "ADLS Gen2"}, 200, failed, "BadGateway")["health"] == "broken"
    assert center.health_from_probe(one, 200, 404)["health"] == "broken"
    assert center.health_from_probe(one, 200, 409)["health"] == "broken"


def test_risks_find_crossed_environments_and_duplicates():
    base = {"lakehouse_id": "l1", "host_workspace_id": "w1", "target_type": "OneLake", "workspace_id": "w9",
            "item_id": "i", "target_path": "Tables/dbo/c"}
    a = base | {"name": "c", "full_path": "Tables/dbo/c", "host_env": "dev", "target_env": "prod", "lakehouse_name": "A"}
    b = base | {"name": "c2", "full_path": "Tables/dbo/c2", "lakehouse_name": "A"}
    out = center.risks([a, b], {})
    assert out["l1|Tables/dbo/c"][0]["kind"] == "cross_env" and "live data" in out["l1|Tables/dbo/c"][0]["text"]
    assert out["l1|Tables/dbo/c2"][0]["kind"] == "duplicate"
    assert center.env_of("x", "Finance PROD", {}) == "prod" and center.env_of("x", "Product", {}) == ""
    assert center.env_of("x", "Product", {"x": "test"}) == "test"


def test_deprecated_products_and_broken_shortcuts_in_use_are_needs():
    products = [{"id": "sales.customer", "name": "customer", "status": "deprecated",
                 "ports": [{"port": "main", "workspace_id": "w9", "lakehouse_id": "i", "schema_name": "dbo",
                            "table_name": "c", "env": "dev"}]}]
    idx = center.port_index(products)
    s = {"lakehouse_id": "l1", "full_path": "Tables/dbo/c", "name": "c", "target_type": "OneLake", "workspace_id": "w9",
         "item_id": "i", "target_path": "Tables/dbo/c", "health": "broken", "used_by": [{"entity": "silver.c"}]}
    s["product"] = idx[center.target_key(s)]
    kinds = [n["kind"] for n in center.needs([s], [])]
    assert kinds == ["broken_in_use", "deprecated"]
    assert center.check_rules({"deprecated_product": "block"}, {"product": s["product"]}, True)["blocked"]


def test_describe_keeps_the_target_to_make_it_again():
    d = shortcuts.describe({"name": "x", "path": "Files/raw", "target": {"type": "AmazonS3", **S3}})
    assert d["rest_target"] == S3


def test_undo_runs_only_for_a_change_its_author_could_make(svc):
    center_of(svc)
    LocalPlatform(svc._catalog().path, lambda: "chris@example.com").call("whoami")  # the first in is its admin
    eve = LocalPlatform(svc._catalog().path, lambda: "eve@example.com")
    assert not eve.call("whoami")["admin"]
    customer = {"workspace_id": "ws-dev", "lakehouse_id": "lh-1", "workspace_name": "Sales Dev",
                "lakehouse_name": "LH_Silver", "path": "Tables/dbo", "name": "customer"}
    # A non-member's repoint is only reported, with what it pointed at taken from the inventory.
    eve.call("log_shortcut_change", kind="repointed", shortcut=dict(customer, target=GOLD),
             before=dict(customer, target=S3), after=dict(customer, target=GOLD))
    reported = svc.shortcut_changes()["changes"][0]
    assert reported["note"].startswith("reported") and reported["before"]["target"] == GOLD
    assert not reported["undoable"]
    with pytest.raises(RuntimeError, match="can be undone. Nothing was changed"):
        svc.shortcut_undo(reported["id"])
    # Anyone may log an add, but undoing one a non-member logged would remove a real shortcut.
    eve.call("log_shortcut_change", kind="added", shortcut=dict(customer, target=GOLD), after=dict(customer, target=GOLD))
    added = svc.shortcut_changes()["changes"][0]
    assert added["kind"] == "added" and not added["undoable"]
    with pytest.raises(RuntimeError, match="can be undone"):
        svc.shortcut_undo(added["id"])
    assert next(s for s in svc.api.items["lh-1"] if s["name"] == "customer")["target"] == GOLD


def test_readers_gap_counts_readers_the_target_leaves_out():
    """#1423: Access lists a shortcut whose readers see it empty, so its gap is how many they are."""
    here = [{"id": "u1"}, {"id": "u2"}, {"id": "u3"}]
    out = center.readers_gap(here, [{"id": "u1"}], "identity")
    assert (out["gap"], out["target"], [m["id"] for m in out["missing"]]) == (2, 1, ["u2", "u3"])
    assert out["text"] == "2 of 3 see it empty: they have no access to the target"
    assert center.readers_gap(here, here, "identity")["gap"] == 0
