"""Mutation tests for central/service.py (batch 451): moving and cancelling shared keys, the landing zone's
file listing, domains Fabric refuses, Data mesh > Home's views, the overview and the platform status.
Fakes only: nothing reaches Fabric."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop.central import service as service_mod
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.meta.project import GateError
from fabric_desktop.service import Service
from tests.test_central_service_cov3 import Cat, bare, svc  # noqa: F401 - the fixtures
from tests.test_domain_in_fabric import _Admin

STATUS = {"status": "stub"}


def _no_status(s, monkeypatch):
    monkeypatch.setattr(s, "platform_status", lambda: STATUS)


# ---------------------------------------------------------------- shared keys


def test_keys_already_shared_say_so_with_nothing_renumbered(bare, monkeypatch):  # noqa: F811
    _no_status(bare, monkeypatch)
    monkeypatch.setattr(bare, "_keys_config", lambda: {"shared": True})
    assert bare.platform_share_keys(approve=True) == {"ok": True, "already": True, "tables": [], "renumbered": 0,
                                                      "status": STATUS}


def test_a_cancelled_move_is_ok_and_reopens_every_environment(bare, monkeypatch):  # noqa: F811
    _no_status(bare, monkeypatch)
    source = Cat(close_keys=None)
    monkeypatch.setattr(bare, "_check_can_share", lambda: {})
    monkeypatch.setattr(bare, "_adopt_live_keys", lambda: False)
    monkeypatch.setattr(bare, "_shared_keys", lambda: Cat(clear_keys={"cleared": ["customer"]}))
    monkeypatch.setattr(bare, "_share_sources", lambda: [("dev", source)])
    out = bare.platform_cancel_share_keys(approve=True)
    assert out == {"ok": True, "cleared": ["customer"], "status": STATUS}
    assert source.calls == [("close_keys", {"reason": ""})]


# ---------------------------------------------------------------- landing zone files


def test_a_zone_listing_has_the_files_under_files_only(bare, monkeypatch):  # noqa: F811
    asked = []

    class Store:
        def __init__(self, token):
            pass

        def list(self, ws, lh, root, recursive=False):
            asked.append((ws, lh, root, recursive))
            return [{"path": "Files/a.csv", "size": 3, "modified": "m", "is_dir": False},
                    {"path": "Files/sub", "size": 0, "is_dir": True},
                    {"path": "Tables/t", "size": 9, "is_dir": False}]
    monkeypatch.setattr(service_mod, "FileStore", Store)
    assert bare._zone_listing("w", "l") == [{"path": "a.csv", "size": 3, "modified": "m"}]
    assert asked == [("w", "l", "Files", True)]


# ---------------------------------------------------------------- domains


def test_any_other_fabric_refusal_of_a_new_domain_is_raised_as_it_is(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(Service, "_fabric_admin", lambda self, write=False: _Admin(500))
    with pytest.raises(FabricError, match="failed with 500"):
        svc.platform_create_domain({"name": "Finance"})
    assert svc.platform_domains() == []


@pytest.mark.parametrize("status", [401, 403])
def test_fabric_refusing_this_account_says_only_an_administrator_can(svc, monkeypatch, status):  # noqa: F811
    monkeypatch.setattr(Service, "_fabric_admin", lambda self, write=False: _Admin(status))
    with pytest.raises(PermissionError, match="Only a Fabric administrator"):
        svc.platform_create_domain({"name": "Finance"})


def test_an_already_exists_code_without_409_is_still_a_domain_fabric_has(svc, monkeypatch):  # noqa: F811
    class Admin:
        def create_admin_domain(self, name, description="", parent_id=""):
            raise FabricError("POST /admin/domains failed", code="AlreadyExists", status=400)
    monkeypatch.setattr(Service, "_fabric_admin", lambda self, write=False: Admin())
    with pytest.raises(ValueError, match="Fabric already has a domain called Finance"):
        svc.platform_create_domain({"name": "Finance"})


def test_the_suggested_domain_is_prods_even_when_dev_has_another(bare, monkeypatch):  # noqa: F811
    project = SimpleNamespace(environments={"dev": {"workspace_id": "w1"}, "prod": {"workspace_id": "w3"}})

    class Fabric:
        def get_workspace(self, wid):
            return {"w1": {"domainId": "fd-hr"}, "w3": {"domainId": "fd-sales"}}[wid]
    monkeypatch.setattr(Service, "fabric", property(lambda self: Fabric()))
    monkeypatch.setattr(bare, "_project", lambda: project)
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_domains=[{"id": "hr", "fabric_domain": "fd-hr"},
                                                                    {"id": "sales", "fabric_domain": "fd-sales"}]))
    assert bare.mesh_workspace_domains()["suggested"] == {"domain": "sales", "subdomain": ""}


# ---------------------------------------------------------------- products


def test_deleting_a_product_that_has_no_file_is_not_an_error(svc):  # noqa: F811
    assert "products" in svc.mesh_delete_product("never_made")


def _products(bare, monkeypatch, products):
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_products=products, list_requests=[]))
    monkeypatch.setattr(bare, "_project", lambda: object())
    monkeypatch.setattr(service_mod.mesh, "identity", lambda project: {"name": "fin", "domain": "sales"})
    monkeypatch.setattr(bare, "_my_follows", lambda: None)
    monkeypatch.setattr(bare, "_product_groups", lambda catalog: None)
    monkeypatch.setattr(bare, "_product_health", lambda: None)
    return {p["id"]: p for p in bare.platform_products()["products"]}


def test_a_product_is_linked_only_when_this_project_consumes_it(bare, monkeypatch):  # noqa: F811
    base = {"project": "crm", "access": "public", "status": "published"}
    got = _products(bare, monkeypatch, [base | {"id": "crm/a", "consumers": [{"consumer_project": "fin"}]},
                                        base | {"id": "crm/b", "consumers": [{"consumer_project": "hr"}]}])
    assert got["crm/a"]["linked"] is True and got["crm/b"]["linked"] is False


def test_a_link_the_catalog_already_has_with_its_version_isnt_sent_again(bare, monkeypatch):  # noqa: F811
    monkeypatch.setattr(service_mod.mesh, "project_name", lambda project: "fin")
    link = SimpleNamespace(alias="orders", location=lambda env: "", reads=lambda env: "prod", product="crm/a",
                           port="daily", accepts="1.0", columns={"id": "int"})
    recorded = {"id": "fin:orders", "product_id": "crm/a", "port": "daily", "accepts": "1.0",
                "columns": {"id": "int"}, "reads": {"dev": "prod", "test": "prod", "prod": "prod"}}
    sent = []
    monkeypatch.setattr(bare, "_register_link", lambda **kw: sent.append(kw) or [])
    cat = Cat(list_products=[{"consumers": [recorded]}])
    assert bare._sync_links(SimpleNamespace(links={"orders": link}), cat) == [] and sent == []


# ---------------------------------------------------------------- keys back to LH_Keys


def _back(bare, monkeypatch, top, have):
    project = SimpleNamespace(environments={"dev": {"keys": "platform", "keys_moved_at": "t"}},
                              key_tables={"customer": SimpleNamespace(name="customer")}, reload=lambda: None)
    written = []
    monkeypatch.setattr(bare, "_project", lambda: project)
    monkeypatch.setattr(bare, "_keys_in_platform", lambda project, env: True)
    monkeypatch.setattr(bare, "_platform_keys", lambda env: SimpleNamespace(stats=lambda name: top))
    monkeypatch.setattr(bare, "_keys_store", lambda project, env: None)
    monkeypatch.setattr(service_mod.keys_mod, "key_stats", lambda store, kt: have)
    monkeypatch.setattr(bare, "_write_environments", lambda project, envs: written.append(envs))
    return bare.meta_keys_to_lakehouse("dev"), written


def test_lh_keys_without_stats_counts_from_1_so_going_back_is_allowed(bare, monkeypatch):  # noqa: F811
    out, written = _back(bare, monkeypatch, {"next": 1}, {})
    assert out == {"env": "dev", "keys": "lakehouse"} and written == [{"dev": {"keys": "lakehouse"}}]


def test_keys_only_the_platform_issued_are_named_up_to_the_last_one(bare, monkeypatch):  # noqa: F811
    with pytest.raises(GateError) as caught:
        _back(bare, monkeypatch, {"next": 5}, {"next": 3})
    assert "customer (keys 3 to 4)" in str(caught.value)


# ---------------------------------------------------------------- Data mesh > Home


def _home(bare, monkeypatch, view, **answers):
    def none():
        raise LookupError("no project")
    monkeypatch.setattr(bare, "_project", none)
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_products=[], whoami={"user": "u"}, **answers))
    seen = {}

    def team(user, project, products, requests, follows, reviews, reach):
        seen["reviews"] = reviews
        return {}

    def org(domains, products, requests, reviews, reach, now):
        seen["domains"] = domains
        return {}
    monkeypatch.setattr(service_mod.home, "team", team)
    monkeypatch.setattr(service_mod.home, "org", org)
    out = bare.mesh_home(view)
    return out, seen


def test_the_team_view_gets_the_access_reviews(bare, monkeypatch):  # noqa: F811
    out, seen = _home(bare, monkeypatch, "team", access_reviews=[{"id": "r1"}], list_requests=[], list_follows=[])
    assert seen["reviews"] == [{"id": "r1"}] and out["problems"] == []


def test_the_org_view_gets_the_domains(bare, monkeypatch):  # noqa: F811
    out, seen = _home(bare, monkeypatch, "org", access_reviews=[], list_requests=[],
                      list_domains=[{"id": "sales"}])
    assert seen["domains"] == [{"id": "sales"}] and out["problems"] == []


# ---------------------------------------------------------------- overview and status


def test_the_overview_counts_keys_and_shows_the_last_eight_audit_entries(bare, monkeypatch):  # noqa: F811
    cat = Cat(list_domains=[], list_projects=[], list_products=[], list_requests=[], audit=lambda **kw: kw)
    monkeypatch.setattr(bare, "_catalog", lambda: cat)
    monkeypatch.setattr(bare, "platform_whoami", lambda: {"admin": True})
    monkeypatch.setattr(bare, "_keys_platform", lambda env: Cat(list_key_tables=[{"keys": 3}, {"keys": None}]))
    monkeypatch.setattr(bare, "platform_landing", lambda env: {"sources": []})
    out = bare.platform_overview()
    assert out["recent"] == {"limit": 8}
    assert out["counts"]["key_tables"] == 2 and out["counts"]["keys"] == 3


def test_an_environment_with_functions_is_fabric_and_one_without_is_local(bare, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bare, "_platform_config", lambda: {"dev": {"functions_id": "f1"}, "test": {}})
    envs = bare.platform_status()["environments"]
    assert envs == {"dev": {"kind": "fabric", "functions_id": "f1"}, "test": {"kind": "local"}}
