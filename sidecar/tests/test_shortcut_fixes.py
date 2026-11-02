"""Shortcut fixes: find a lakehouse that moved, repoint many at once, and accept a crossed environment."""

import time
from contextlib import closing

import pytest

from fabric_desktop import estate_service, shortcut_center as center, shortcut_service
from fabric_desktop.central import core
from fabric_desktop.central.client import LocalPlatform
from fabric_desktop.service import Service

GONE = {"oneLake": {"workspaceId": "ws-old", "itemId": "lh-gone", "path": "Tables/dbo/customer"}}
GONE2 = {"oneLake": {"workspaceId": "ws-old", "itemId": "lh-gone", "path": "Tables/dbo/orders"}}
GONE3 = {"oneLake": {"workspaceId": "ws-old", "itemId": "lh-gone", "path": "Tables/dbo/returns"}}
PROD = {"oneLake": {"workspaceId": "ws-prod", "itemId": "lh-gold", "path": "Tables/dbo/product"}}
HOST = {"workspace_id": "ws-dev", "lakehouse_id": "lh-1", "name": "LH_Silver", "workspace_name": "Sales Dev"}
OLD = {"workspace_id": "ws-old", "lakehouse_id": "lh-gone", "name": "LH_Gold", "workspace_name": "Sales Old"}
GOLD = {"workspace_id": "ws-prod", "lakehouse_id": "lh-gold", "name": "LH_Gold", "workspace_name": "Sales Prod"}
GOLD_DEV = {"workspace_id": "ws-dev2", "lakehouse_id": "lh-gold-dev", "name": "LH_Gold", "workspace_name": "Sales Dev 2"}


class FakeApi:
    """Workspaces, lakehouses and shortcuts in memory."""

    def __init__(self):
        self.items = {"lh-1": [{"name": "customer", "path": "Tables/dbo", "target": GONE},
                               {"name": "orders", "path": "Tables/dbo", "target": GONE2},
                               {"name": "returns", "path": "Tables/dbo", "target": GONE3},
                               {"name": "product", "path": "Tables/dbo", "target": PROD}]}
        self.lakehouses = {"ws-dev": [{"id": "lh-1", "displayName": "LH_Silver"}],
                           "ws-new": [{"id": "lh-new", "displayName": "LH_Gold"}],
                           "ws-prod": [{"id": "lh-gold", "displayName": "LH_Gold_Prod"}]}
        self.fail_create_for: set[str] = set()

    def list_workspaces(self):
        return [{"id": w, "displayName": {"ws-new": "Sales New"}.get(w, w)} for w in self.lakehouses]

    def list_lakehouses(self, ws):
        return list(self.lakehouses[ws])

    def list_shortcuts(self, ws, item):
        return [dict(s) for s in self.items.get(item, [])]

    def create_shortcut(self, ws, item, path, name, target):
        if name in self.fail_create_for and target["oneLake"]["itemId"] == "lh-new":
            raise RuntimeError("400 target not found")
        self.items.setdefault(item, []).append({"name": name, "path": path, "target": target})
        return {}

    def delete_shortcut(self, ws, item, path, name):
        self.items[item] = [s for s in self.items.get(item, []) if not (s["path"] == path.strip("/") and s["name"] == name)]

    def list_data_access_roles(self, ws, item):
        return []


class Auth:
    user = "ana@contoso.com"

    def _token(self, scopes):
        return "tok"

    def fabric_token(self):
        return "tok"

    def storage_token(self):
        return "tok"

    def signed_in_user(self):
        return self.user

    def silent_token(self, scopes):
        return "tok"


class Files:
    """OneLake listings: paths that are there, by lakehouse."""
    there: dict[str, set[str]] = {}

    def __init__(self, token):
        pass

    def probe(self, ws, lh, path):
        return (200, "") if path in self.there.get(lh, set()) else (404, "PathNotFound")


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    api = FakeApi()
    s._auth = Auth()
    s.api = api
    monkeypatch.setattr(type(s), "_scoped", lambda self, scopes: api)
    monkeypatch.setattr(estate_service, "FabricClient", lambda token: api)
    monkeypatch.setattr(shortcut_service, "FabricClient", lambda token: api)
    monkeypatch.setattr(shortcut_service, "FileStore", Files)
    monkeypatch.setattr(type(s), "_sc_envs", lambda self: {"ws-dev": "dev", "ws-dev2": "dev", "ws-prod": "prod"})
    Files.there = {}
    return s


def center_of(svc, lakehouses, refresh=False):
    out = svc.shortcut_center(lakehouses, refresh=refresh)
    for _ in range(200):
        if not out.get("pending"):
            return out
        time.sleep(0.02)
        out = svc.shortcut_center(lakehouses)
    raise AssertionError("never finished")


def gone_rows(svc):
    """The first read sees the old lakehouse (and keeps its name); then it's deleted."""
    center_of(svc, [HOST, OLD])
    rows = {r["name"]: r for r in center_of(svc, [HOST], refresh=True)["rows"]}
    return [rows[n] for n in ("customer", "orders", "returns")]


# ---- find the moved lakehouse

def test_a_gone_lakehouse_keeps_its_name_and_is_found_with_every_table(svc):
    rows = gone_rows(svc)
    assert {r["target_lakehouse_name"] for r in rows} == {"LH_Gold"}
    assert {r["target_workspace_name"] for r in rows} == {"Sales Old"}
    Files.there = {"lh-new": {"Tables/dbo/customer", "Tables/dbo/orders", "Tables/dbo/returns"}}
    out = svc.shortcut_find_moved(rows)
    assert out["candidate"] == {"workspace_id": "ws-new", "workspace_name": "Sales New", "lakehouse_id": "lh-new",
                                "lakehouse_name": "LH_Gold"}
    assert sorted(out["found"]) == sorted(r["id"] for r in rows) and out["missing"] == []


def test_find_moved_says_which_tables_are_missing(svc):
    rows = gone_rows(svc)
    Files.there = {"lh-new": {"Tables/dbo/customer", "Tables/dbo/orders"}}
    out = svc.shortcut_find_moved(rows)
    assert out["candidate"]["lakehouse_id"] == "lh-new"
    assert len(out["found"]) == 2 and out["missing"] == ["lh-1|Tables/dbo/returns"]


def test_find_moved_without_a_lakehouse_of_that_name(svc):
    rows = gone_rows(svc)
    svc.api.lakehouses.pop("ws-new")
    out = svc.shortcut_find_moved(rows)
    assert out["candidate"] is None and out["found"] == [] and len(out["missing"]) == 3
    # nor without a name to look for
    assert svc.shortcut_find_moved([dict(rows[0], target_lakehouse_name="")])["candidate"] is None


def test_candidates_are_other_lakehouses_with_the_name():
    lakehouses = [{"lakehouse_id": "a", "lakehouse_name": "lh_gold"}, {"lakehouse_id": "gone", "lakehouse_name": "LH_Gold"},
                  {"lakehouse_id": "b", "lakehouse_name": "LH_Silver"}]
    assert [l["lakehouse_id"] for l in center.moved_candidates("LH_Gold", "gone", lakehouses)] == ["a"]
    assert center.moved_candidates("", "gone", lakehouses) == []


# ---- repoint many

def test_repoint_many_carries_on_past_a_failure(svc):
    rows = gone_rows(svc)
    svc.api.fail_create_for = {"orders"}
    out = svc.shortcut_repoint_many(rows, "ws-new", "lh-new", "Sales New", "LH_Gold")
    assert out["done"] == ["customer", "returns"]
    assert [f["name"] for f in out["failed"]] == ["orders"] and "put back as it was" in out["failed"][0]["error"]
    now = {s["name"]: s["target"]["oneLake"] for s in svc.api.items["lh-1"]}
    assert now["customer"] == {"workspaceId": "ws-new", "itemId": "lh-new", "path": "Tables/dbo/customer"}
    assert now["returns"]["itemId"] == "lh-new" and now["orders"] == GONE2["oneLake"]  # put back as it was
    changes = svc.shortcut_changes()["changes"]
    assert sorted(c["name"] for c in changes if c["kind"] == "repointed") == ["customer", "returns"]
    assert all("2 more at LH_Gold" in c["note"] for c in changes if c["kind"] == "repointed")


# ---- accept a crossed environment

def test_cross_env_offers_the_same_environments_lakehouse(svc):
    rows = {r["name"]: r for r in center_of(svc, [HOST, GOLD, GOLD_DEV])["rows"]}
    product = rows["product"]
    assert product["risks"][0]["kind"] == "cross_env"
    assert product["same_env_target"] == {"workspace_id": "ws-dev2", "workspace_name": "Sales Dev 2",
                                          "lakehouse_id": "lh-gold-dev", "lakehouse_name": "LH_Gold", "env": "dev"}


def test_accepting_drops_the_need_and_repointing_ends_it(svc):
    out = center_of(svc, [HOST, GOLD])
    assert out["admin"] is True
    sid = "lh-1|Tables/dbo/product"
    assert [n["kind"] for n in out["needs"] if n["id"] == sid] == ["cross_env"]
    with pytest.raises(core.PlatformError, match="why"):
        svc.shortcut_accept_risk(sid, "cross_env", "  ")
    accepted = svc.shortcut_accept_risk(sid, "cross_env", "Reads the live price list on purpose")
    assert accepted["by"] == "ana@contoso.com"
    out = center_of(svc, [HOST, GOLD])
    row = next(r for r in out["rows"] if r["id"] == sid)
    assert row["risks"][0]["accepted"]["reason"] == "Reads the live price list on purpose"
    assert not [n for n in out["needs"] if n["id"] == sid] and out["counts"]["risky"] == 0

    svc.shortcut_withdraw_risk(sid, "cross_env")
    assert [n["kind"] for n in center_of(svc, [HOST, GOLD])["needs"] if n["id"] == sid] == ["cross_env"]

    svc.shortcut_accept_risk(sid, "cross_env", "Still fine")
    svc.shortcut_save_rules({"cross_env": "warn"})
    src = {"type": "oneLake", "workspace_id": "ws-prod", "item_id": "lh-gold", "path": "Tables/dbo/product2"}
    svc.shortcut_repoint("ws-dev", "lh-1", "Tables/dbo", "product", src, "LH_Silver", "Sales Dev", "Sales Prod")
    assert svc._catalog().call("list_shortcut_risks") == []  # a new target is judged afresh
    out = center_of(svc, [HOST, GOLD], refresh=True)
    assert [n["kind"] for n in out["needs"] if n["id"] == sid] == ["cross_env"]
    actions = [a["action"] for a in svc._catalog().call("audit", limit=20)]
    assert {"accept shortcut risk", "withdraw shortcut risk", "end accepted shortcut risk"} <= set(actions)


def test_only_platform_admins_accept_or_withdraw(svc):
    center_of(svc, [HOST, GOLD])
    sid = "lh-1|Tables/dbo/product"
    svc.shortcut_accept_risk(sid, "cross_env", "On purpose")
    svc._auth.user = "lee@contoso.com"
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        svc.shortcut_accept_risk(sid, "cross_env", "Mine too")
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        svc.shortcut_withdraw_risk(sid, "cross_env")
    assert center_of(svc, [HOST, GOLD])["admin"] is False
    svc._auth.user = "ana@contoso.com"
    with pytest.raises(core.PlatformError, match="Only cross_env"):
        svc.shortcut_accept_risk(sid, "duplicate", "Fine")


def test_an_older_platform_gets_the_new_table_and_columns(tmp_path):
    import sqlite3
    path = tmp_path / "p.sqlite"
    LocalPlatform(path, lambda: "ana@contoso.com")
    conn = sqlite3.connect(path)
    conn.execute('DROP TABLE "catalog__shortcut_risk"')
    conn.execute('ALTER TABLE "catalog__shortcut" DROP COLUMN target_lakehouse_name')
    conn.commit()
    conn.close()
    p = LocalPlatform(path, lambda: "ana@contoso.com")
    assert p.call("list_shortcut_risks") == []
    with closing(sqlite3.connect(path)) as conn:
        cols = {r[1] for r in conn.execute('PRAGMA table_info("catalog__shortcut")')}
    assert {"target_workspace_name", "target_lakehouse_name"} <= cols


def test_a_platform_in_fabric_without_the_new_functions_says_to_finish_or_repair(svc, monkeypatch):
    from fabric_desktop.central.client import FabricPlatform

    class NotFound:
        status_code = 404
        content = b""
        text = "Function not found"

    class Session:
        def post(self, url, headers, json, timeout):  # noqa: A002 - requests' own name
            return NotFound()

    fabric = FabricPlatform(lambda: "tok", "ws", "fn", session=Session())
    local = svc._catalog()

    class Catalog:
        """The platform as it is in Fabric before Finish or repair: everything but accepted risks."""

        def call(self, op, **kwargs):
            return (fabric if "shortcut_risk" in op else local).call(op, **kwargs)

    monkeypatch.setattr(type(svc), "_catalog", lambda self: Catalog())
    out = center_of(svc, [HOST, GOLD])
    assert [n["kind"] for n in out["needs"]] == ["cross_env"]  # carries on, every risk flagged
    assert [p for p in out["problems"] if "risk" in p] == [
        "The platform can't keep accepted shortcut risks yet: UDF_Platform has no list_shortcut_risks function. "
        "After an update, a platform admin runs Platform > Settings > Finish or repair once."]
    for call in (lambda: svc.shortcut_accept_risk("lh-1|Tables/dbo/product", "cross_env", "On purpose"),
                 lambda: svc.shortcut_withdraw_risk("lh-1|Tables/dbo/product", "cross_env")):
        with pytest.raises(LookupError, match="can't keep accepted shortcut risks yet.*Finish or repair once"):
            call()
