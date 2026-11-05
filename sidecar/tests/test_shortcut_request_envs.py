"""Approving a shortcut request makes it in Dev, Test and Prod, each pointing at its own environment."""

import pytest

from fabric_desktop import shortcut_center as center
from fabric_desktop.central import requests_service
from fabric_desktop.fabric_api import FabricError

from test_shortcut_center import svc  # noqa: F401 - the fixture

LAKEHOUSES = [
    {"workspace_id": "ws-dev", "workspace_name": "Sales Dev", "lakehouse_id": "lh-1", "lakehouse_name": "LH_Silver"},
    {"workspace_id": "ws-test", "workspace_name": "Sales Test", "lakehouse_id": "lh-1t", "lakehouse_name": "LH_Silver"},
    {"workspace_id": "ws-prod1", "workspace_name": "Sales Prod", "lakehouse_id": "lh-1p", "lakehouse_name": "LH_Silver"},
    {"workspace_id": "ws-dev2", "workspace_name": "Finance Dev", "lakehouse_id": "lh-gold-dev", "lakehouse_name": "LH_Gold"},
    {"workspace_id": "ws-test2", "workspace_name": "Finance Test", "lakehouse_id": "lh-gold-test", "lakehouse_name": "LH_Gold"},
    {"workspace_id": "ws-prod", "workspace_name": "Finance Prod", "lakehouse_id": "lh-gold", "lakehouse_name": "LH_Gold"},
    {"workspace_id": "ws-shared", "workspace_name": "Platform Shared", "lakehouse_id": "lh-cal", "lakehouse_name": "LH_Calendar"},
]
SRC = {"type": "oneLake", "workspace_id": "ws-dev2", "item_id": "lh-gold-dev", "path": "Tables/dbo/supplier"}
DETAIL = {"workspace_id": "ws-dev", "item_id": "lh-1", "where": "Tables/dbo", "name": "supplier", "source": SRC,
          "lakehouse_name": "LH_Silver", "workspace_name": "Sales Dev", "target_workspace_name": "Finance Dev"}


def test_each_environment_gets_its_own_lakehouse_and_its_own_target():
    plan = center.env_twins(DETAIL, {}, LAKEHOUSES, [])
    assert plan["env"] == "dev" and plan["why"] == ""
    test, prod = plan["places"]
    assert (test["env"], test["lakehouse_id"], test["source"]["item_id"]) == ("test", "lh-1t", "lh-gold-test")
    assert (prod["env"], prod["lakehouse_id"], prod["source"]["workspace_id"]) == ("prod", "lh-1p", "ws-prod")
    assert prod["source"]["path"] == SRC["path"] and prod["target_lakehouse_name"] == "LH_Gold"


def test_a_place_with_no_match_or_two_is_skipped_never_guessed():
    two = LAKEHOUSES + [{"workspace_id": "ws-prod3", "workspace_name": "Sales - PROD", "lakehouse_id": "x",
                         "lakehouse_name": "LH_Silver"}]
    no_test_target = [l for l in two if l["lakehouse_id"] != "lh-gold-test"]
    test, prod = center.env_twins(DETAIL, {}, no_test_target, [])["places"]
    assert "No Test match for its target LH_Gold" in test["why"] and "source" not in test
    assert prod["why"] == "No Prod workspace matches Sales Dev"


def test_the_metadata_projects_own_pairs_come_first():
    renamed = LAKEHOUSES + [{"workspace_id": "ws-live", "workspace_name": "Revenue Live", "lakehouse_id": "lh-live",
                             "lakehouse_name": "LH_Silver"}]
    groups = [{"dev": "ws-dev", "test": "ws-test", "prod": "ws-live"}]
    prod = center.env_twins(DETAIL, {"ws-live": "prod"}, renamed, groups)["places"][1]
    assert prod["workspace_id"] == "ws-live" and prod["source"]["item_id"] == "lh-gold"


def test_a_shared_target_is_read_by_every_environment_and_storage_stays_where_asked():
    shared = DETAIL | {"source": {"type": "oneLake", "workspace_id": "ws-shared", "item_id": "lh-cal", "path": "Tables/cal"}}
    places = center.env_twins(shared, {}, LAKEHOUSES, [])["places"]
    assert [p["source"]["item_id"] for p in places] == ["lh-cal", "lh-cal"]
    s3 = center.env_twins(DETAIL | {"source": {"type": "amazonS3", "location": "https://b", "subpath": "/x"}}, {}, LAKEHOUSES, [])
    assert s3["places"] == [] and "storage shortcut" in s3["why"]
    nowhere = center.env_twins(DETAIL | {"workspace_name": "Sales"}, {}, LAKEHOUSES, [])
    assert nowhere["places"] == [] and "isn't a Dev, Test or Prod workspace" in nowhere["why"]
    crossing = center.env_twins(DETAIL | {"source": SRC | {"workspace_id": "ws-prod", "item_id": "lh-gold"}}, {}, LAKEHOUSES, [])
    assert all("points at Prod, not Dev" in p["why"] for p in crossing["places"])


@pytest.fixture
def envs_svc(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "_sc_lakehouses", lambda self: LAKEHOUSES)
    return svc


def test_approving_makes_it_in_every_environment_and_says_what_it_couldnt(envs_svc):
    svc = envs_svc
    req = svc.shortcut_request("ws-dev", "lh-1", "Tables/dbo", "supplier", SRC, "Need suppliers", "LH_Silver", "Sales Dev",
                               "Finance Dev")
    plan = svc.shortcut_request_envs(req["id"])
    assert [p["env"] for p in plan["places"]] == ["test", "prod"]
    svc.api.items["lh-1p"] = [{"name": "supplier", "path": "Tables/dbo",
                               "target": {"oneLake": {"workspaceId": "ws-x", "itemId": "lh-x", "path": "Tables/other"}}}]
    original = svc.api.create_shortcut

    def create(ws, item, path, name, target):
        if item == "lh-1p":
            raise conflict()
        return original(ws, item, path, name, target)
    svc.api.create_shortcut = create
    out = svc.platform_decide(req["id"], True, "ok")
    assert out["status"] == "approved"
    assert svc.api.items["lh-1t"][0]["target"]["oneLake"]["itemId"] == "lh-gold-test"
    assert "Also made in Test: Sales Test / LH_Silver" in out["next"]
    assert any(n.startswith("Not made in Prod: Sales Prod / LH_Silver already has a shortcut") for n in out["next"])
    notes = [c["note"] for c in svc.shortcut_changes()["changes"]]
    assert any("(Test copy of the request)" in n for n in notes)
    made = svc.platform_requests("approved")[0]["detail"]["made"]
    assert made["also"] == [{"env": "test", "workspace_name": "Sales Test", "lakehouse_name": "LH_Silver",
                             "shortcut_id": "lh-1t|Tables/dbo/supplier"}]
    left = requests_service.left_in_fabric({"kind": "shortcut", "detail": {"name": "supplier", "made": made}})
    assert any("Sales Test / LH_Silver (Test), made by this request" in x for x in left)


def test_a_twin_that_cant_be_made_never_stops_the_approval(envs_svc, monkeypatch):
    svc = envs_svc
    req = svc.shortcut_request("ws-dev", "lh-1", "Tables/dbo", "supplier", SRC, "", "LH_Silver", "Sales Dev", "Finance Dev")
    monkeypatch.setattr(type(svc), "_sc_lakehouses", lambda self: (_ for _ in ()).throw(RuntimeError("throttled")))
    out = svc.platform_decide(req["id"], True)
    assert out["status"] == "approved" and any("couldn't be listed" in n for n in out["next"])
    assert svc.api.items["lh-1"][-1]["name"] == "supplier"


def conflict():
    return FabricError("A shortcut with that name already exists", code="ItemAlreadyExists", status=409)


def test_copies_in_other_environments_follow_the_approvers_own_rights(envs_svc, monkeypatch):
    """A product owner who may approve the Dev request doesn't decide for Test and Prod: with the rule that
    only admins make shortcuts, those copies wait for someone who may."""
    svc = envs_svc
    req = svc.shortcut_request("ws-dev", "lh-1", "Tables/dbo", "supplier", SRC, "", "LH_Silver", "Sales Dev", "Finance Dev")
    catalog = type(svc)._catalog
    owner = {"user": "dana@contoso.com", "admin": False}

    class Catalog:
        def __init__(self, real):
            self.real = real

        def call(self, method, **kw):
            if method == "whoami":
                return owner
            if method == "shortcut_rules":
                return {"who_creates": "admins"}
            if method == "list_requests":
                return [r | {"can_decide": True} for r in self.real.call(method, **kw)]
            if method == "list_products":
                return [{"id": "p-dev", "owner": "dana@contoso.com", "ports": [
                    {"workspace_id": "ws-dev2", "lakehouse_id": "lh-gold-dev", "table_name": "supplier"}]},
                        {"id": "p-test", "owner": "dana@contoso.com", "ports": [
                            {"workspace_id": "ws-test2", "lakehouse_id": "lh-gold-test", "table_name": "supplier"}]},
                        {"id": "p-prod", "owner": "lee@contoso.com", "ports": [
                            {"workspace_id": "ws-prod", "lakehouse_id": "lh-gold", "table_name": "supplier"}]}]
            return self.real.call(method, **kw)

    monkeypatch.setattr(type(svc), "_catalog", lambda self: Catalog(catalog(self)))
    out = svc.platform_decide(req["id"], True)
    assert out["status"] == "approved" and svc.api.items["lh-1"][-1]["name"] == "supplier"
    assert "Also made in Test: Sales Test / LH_Silver" in out["next"]
    assert not svc.api.items.get("lh-1p")
    assert any(n.startswith("Not made in Prod") and "Only platform admins" in n for n in out["next"])

    owner["admin"] = True
    assert svc._sc_may_decide_twin(owner, {"workspace_id": "ws-prod1"})
    owner.update(admin=False, user="")
    assert not svc._sc_may_decide_twin(owner, {"workspace_id": "ws-prod1"})
