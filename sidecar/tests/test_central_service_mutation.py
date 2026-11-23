"""The platform and mesh service (central/service.py), held to exact values: which refusal from an older catalog
is retried without the new fields, a product's version for a new link, adopting keys another admin moved, the
keys move and its cancel, domains created in Fabric and the refusals on the way, the central landing workspace
and its zones, a request's notice problems and the overview's counts. Fakes only: nothing reaches Fabric."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop.central import core
from fabric_desktop.central import service as service_mod
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.service import Service
from tests.test_central_service_cov3 import Cat, bare, svc  # noqa: F401 - bare and svc are fixtures


def _saving(service, monkeypatch):
    saved = []
    monkeypatch.setattr(service, "_save_platform", lambda what, cfg: saved.append((what, cfg)))
    return saved


# ---------------------------------------------------------------- links to products


@pytest.mark.parametrize("message, unknown", [
    ("register_link() got an unexpected keyword argument 'accepts'", True),
    ("unknown argument columns", True),
    ("accepts is a version number, like 3", False),
    ("the product is deprecated", False),
])
def test_only_a_refusal_of_the_new_fields_is_an_older_catalog(message, unknown):
    assert service_mod._unknown_argument(RuntimeError(message)) is unknown


def test_a_link_the_catalog_keeps_whole_needs_no_repair(bare, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(register_link={"accepts": "3"}))
    assert bare._register_link(consumer="fin", alias="a", accepts="3", columns={}) == []
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(register_link={"alias": "a"}))
    [repair] = bare._register_link(consumer="fin", alias="a", accepts="3", columns={})
    assert repair.startswith("The link is saved, but the catalog doesn't keep the versions")


def test_an_older_catalog_registers_the_link_without_the_new_fields(bare, monkeypatch):  # noqa: F811
    def register(**kw):
        if "accepts" in kw:
            raise core.PlatformError("register_link() got an unexpected keyword argument 'accepts'")
        return {}
    cat = Cat(register_link=register)
    monkeypatch.setattr(bare, "_catalog", lambda: cat)
    assert len(bare._register_link(consumer="fin", alias="a", accepts="3", columns={})) == 1
    assert cat.calls[-1] == ("register_link", {"consumer": "fin", "alias": "a"})


def test_the_catalogs_own_refusal_of_a_link_is_raised(bare, monkeypatch):  # noqa: F811
    def register(**kw):
        if "accepts" in kw:
            raise core.PlatformError("crm/a is deprecated")
        return {}
    cat = Cat(register_link=register)
    monkeypatch.setattr(bare, "_catalog", lambda: cat)
    with pytest.raises(core.PlatformError, match="deprecated"):
        bare._register_link(consumer="fin", alias="a", accepts="", columns={})
    assert len(cat.calls) == 1, "never sent again without the link's accepts and columns"


def test_a_new_link_accepts_the_products_version_when_it_is_a_number(bare, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_products=[{"id": "crm/a", "version": " 3 "},
                                                                      {"id": "crm/b", "version": "v2"}]))
    assert bare._current_version("crm/a") == "3"
    assert bare._current_version("crm/b") == "" and bare._current_version("crm/c") == ""


# ---------------------------------------------------------------- keys


def test_keys_another_admin_made_live_are_adopted_from_a_fabric_keys_database(bare, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(bare, "_keys_config", lambda: {"functions_id": "f1"})
    monkeypatch.setattr(bare, "_local_keys_path", lambda: tmp_path / "none.sqlite")
    monkeypatch.setattr(bare, "_shared_keys", lambda: Cat(keys_state={"live": True}))
    saved = _saving(bare, monkeypatch)
    assert bare._adopt_live_keys() is True
    assert saved == [("keys", {"functions_id": "f1", "shared": True})]


def test_new_key_tables_go_in_the_shared_database_once_keys_moved(bare, monkeypatch):  # noqa: F811
    shared = Cat(ensure_key_table={"name": "orders"})
    monkeypatch.setattr(bare, "_shared_keys", lambda: shared)
    monkeypatch.setattr(bare, "_distinct_platforms", lambda: [("dev", "each")])
    monkeypatch.setattr(bare, "_keys_config", lambda: {"shared": True})
    assert bare._key_platforms() == [("keys", shared)]
    assert bare.platform_create_key_table(" Orders ", "id") == {
        "name": "orders", "environments": [{"env": "keys", "ok": True, "result": {"name": "orders"}}]}
    monkeypatch.setattr(bare, "_keys_config", lambda: {})
    assert bare._key_platforms() == [("dev", "each")]


def test_moving_keys_that_already_moved_says_so(bare, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bare, "_keys_config", lambda: {"shared": True})
    monkeypatch.setattr(bare, "platform_status", lambda: {"s": 1})
    assert bare.platform_share_keys() == {"ok": True, "already": True, "tables": [], "renumbered": 0,
                                          "status": {"s": 1}}


def test_a_cancelled_move_empties_the_shared_database_and_reopens_each_environment(bare, monkeypatch):  # noqa: F811
    dev = Cat(close_keys={})
    monkeypatch.setattr(bare, "_check_can_share", lambda: {})
    monkeypatch.setattr(bare, "_adopt_live_keys", lambda: False)
    monkeypatch.setattr(bare, "_shared_keys", lambda: Cat(clear_keys={"cleared": 2}))
    monkeypatch.setattr(bare, "_share_sources", lambda: [("dev", dev)])
    monkeypatch.setattr(bare, "platform_status", lambda: {"s": 1})
    assert bare.platform_cancel_share_keys(approve=True) == {"ok": True, "cleared": 2, "status": {"s": 1}}
    assert dev.calls == [("close_keys", {"reason": ""})]


def test_the_status_says_which_environments_are_in_fabric(bare, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bare, "_platform_config", lambda: {"dev": {"functions_id": "f1"}, "prod": {}})
    envs = bare.platform_status()["environments"]
    assert envs == {"dev": {"kind": "fabric", "functions_id": "f1"}, "prod": {"kind": "local"}}


# ---------------------------------------------------------------- domains in Fabric


class _Admin:
    def __init__(self, error=None, domains=()):
        self.error, self.domains = error, list(domains)

    def create_admin_domain(self, name, description, parent):
        raise self.error

    def list_admin_domains(self):
        return self.domains


def _domains(bare, monkeypatch, admin, known=()):  # noqa: F811
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(whoami={"admin": True}, list_domains=list(known)))
    monkeypatch.setattr(bare, "_fabric_admin", lambda write=False: admin)


def test_a_subdomain_of_a_domain_not_in_fabric_is_refused(bare, monkeypatch):  # noqa: F811
    _domains(bare, monkeypatch, _Admin(), known=[{"id": "sales", "name": "Sales"}])
    with pytest.raises(ValueError, match="^Sales isn't in Fabric, so its subdomains can't be either"):
        bare.platform_create_domain({"name": "EMEA", "parent_id": "sales"})


@pytest.mark.parametrize("error, raised, says", [
    (FabricError("denied", status=401), PermissionError, "^Only a Fabric administrator can create domains"),
    (FabricError("taken", status=409), ValueError, "^Fabric already has a domain called Sales"),
    (FabricError("taken", code="DomainAlreadyExists", status=400), ValueError, "^Fabric already has a domain called"),
    (FabricError("server error", status=500), FabricError, "^server error$"),
])
def test_fabrics_refusals_of_a_new_domain(bare, monkeypatch, error, raised, says):  # noqa: F811
    _domains(bare, monkeypatch, _Admin(error))
    with pytest.raises(raised, match=says):
        bare.platform_create_domain({"name": "Sales"})


def test_a_domain_this_request_made_is_known_by_name_parent_and_marker():
    def find(*domains):
        return service_mod.PlatformMixin._domain_made_by(_Admin(domains=domains), "[req 7]", "sales", "p1")
    right = {"displayName": " Sales ", "parentDomainId": "p1", "description": "x [req 7]"}
    assert find(right) == right
    assert find(right | {"parentDomainId": "p2"}) is None
    assert find(right | {"description": "x"}) is None
    assert find(right | {"displayName": "Other"}) is None


# ---------------------------------------------------------------- landing


def test_the_landing_workspace_another_admin_set_up_is_used(bare, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bare, "_central_landing", lambda: {})
    monkeypatch.setattr(bare, "_platform_source", lambda: "local")
    saved = _saving(bare, monkeypatch)
    shared = {"id": "shared", "workspace_id": "w1", "workspace_name": "Landing", "lakehouse_id": "l1"}
    want = {"workspace_id": "w1", "workspace_name": "Landing", "lakehouse_id": "l1"}
    assert bare._adopt_landing([{"id": "z1"}, shared]) == want and saved == [("landing", want)]
    assert bare._adopt_landing([shared | {"lakehouse_id": ""}]) == {}
    assert bare._adopt_landing([shared | {"workspace_id": ""}]) == {}
    assert len(saved) == 1


def test_the_landing_map_is_each_zone_and_each_sources_zone(bare, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bare, "_central_landing", lambda: {"workspace_id": "w1"})
    monkeypatch.setattr(bare, "_zone_call", lambda op, **kw: [{"id": "shared"}, {"id": "hr"}])
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_sources=[{"name": "crm", "zone": "hr"}, {"name": "erp"}]))
    assert bare._landing_resolver()() == {"zones": {"shared": {"id": "shared"}, "hr": {"id": "hr"}},
                                         "sources": {"crm": "hr", "erp": ""}}


def test_a_zone_called_shared_is_refused_and_a_new_one_says_what_it_made(bare, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bare, "_require_platform_admin", lambda action: None)
    monkeypatch.setattr(bare, "_central_landing", lambda: {"workspace_id": "w1", "workspace_name": "Landing"})
    monkeypatch.setattr(Service, "fabric", property(lambda self: "fabric"))
    with pytest.raises(ValueError, match="^Shared is made with the landing workspace"):
        bare.platform_create_landing_zone({"name": " Shared "})
    monkeypatch.setattr(service_mod.setup_mod, "zone_lakehouse", lambda fabric, ws, name: ({"id": "lh9"}, True))
    calls = []
    monkeypatch.setattr(bare, "_zone_call", lambda op, **kw: calls.append((op, kw)) or ["zones"])
    assert bare.platform_create_landing_zone({"name": "HR"}) == {"created": True, "lakehouse": "LH_Landing_HR",
                                                                  "zones": ["zones"]}
    assert calls[0][1]["zone"]["lakehouse_id"] == "lh9"


def test_a_zones_listing_is_its_files_under_files(bare, monkeypatch):  # noqa: F811
    asked = []

    class Store:
        def __init__(self, token):
            pass

        def list(self, ws, lh, folder, recursive=False):
            asked.append((ws, lh, folder, recursive))
            return [{"path": "Files/crm/a.csv", "size": 3, "is_dir": False, "modified": "m"},
                    {"path": "Files/crm", "size": 0, "is_dir": True},
                    {"path": "Tables/t", "size": 1, "is_dir": False},
                    {"path": "Files/b.csv", "size": 4, "is_dir": False}]
    monkeypatch.setattr(service_mod, "FileStore", Store)
    monkeypatch.setattr(Service, "auth", property(lambda self: SimpleNamespace(storage_token=lambda: "t")))
    assert bare._zone_listing("w1", "l1") == [{"path": "crm/a.csv", "size": 3, "modified": "m"},
                                              {"path": "b.csv", "size": 4, "modified": ""}]
    assert asked == [("w1", "l1", "Files", True)]


@pytest.mark.parametrize("source, zone", [("crm", "hr"), ("erp", "shared"), ("new", "shared")])
def test_landing_files_are_read_from_the_sources_own_zone(bare, monkeypatch, source, zone):  # noqa: F811
    monkeypatch.setattr(bare, "_central_landing", lambda: {"workspace_id": "w1"})
    monkeypatch.setattr(bare, "_landing_zones", lambda: [{"id": "shared"}, {"id": "hr"}])
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_sources=[{"name": "erp"}, {"name": "crm", "zone": "hr"}]))
    read = []
    monkeypatch.setattr(bare, "_zone_files", lambda zones: read.append(zones) or ([], []))
    assert bare.platform_landing_files("dev", source) == []
    assert read == [[{"id": zone}]]


# ---------------------------------------------------------------- requests and the overview


def test_requests_are_the_catalogs(bare, monkeypatch):  # noqa: F811
    cat = Cat(list_requests=[{"id": 1}])
    monkeypatch.setattr(bare, "_catalog", lambda: cat)
    assert bare.platform_requests("waiting") == [{"id": 1}]
    assert cat.calls == [("list_requests", {"status": "waiting"})]


def test_a_notice_that_couldnt_be_sent_is_a_problem_of_the_request(bare, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bare, "_governance_notify", lambda events: "Teams: offline", raising=False)
    req = {"id": 1, "kind": "data"}
    bare._notify_request(req, None)
    assert req["problems"] == ["Teams: offline"]
    monkeypatch.setattr(bare, "_governance_notify", lambda events: "", raising=False)
    req = {"id": 2}
    bare._notify_request(req, None)
    assert "problems" not in req


def test_the_overview_adds_up_keys_and_reads_the_last_eight_changes(bare, monkeypatch):  # noqa: F811
    cat = Cat(list_domains=[], list_projects=[], list_products=[], list_requests=[], audit=["a"])
    monkeypatch.setattr(bare, "_catalog", lambda: cat)
    monkeypatch.setattr(bare, "platform_whoami", lambda: {})
    monkeypatch.setattr(bare, "_keys_platform", lambda env: Cat(list_key_tables=[{"keys": 3}, {"keys": None}, {}]))
    monkeypatch.setattr(bare, "platform_landing", lambda env: {"sources": []})
    out = bare.platform_overview()
    assert out["counts"]["keys"] == 3 and out["counts"]["key_tables"] == 3
    assert out["recent"] == ["a"] and ("audit", {"limit": 8}) in cat.calls


def test_deleting_a_product_with_no_file_is_fine(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(svc, "mesh_overview", lambda: "overview")
    assert svc.mesh_delete_product("never_saved") == "overview"


def test_a_link_the_catalog_already_has_as_its_file_says_isnt_sent_again(bare, monkeypatch):  # noqa: F811
    monkeypatch.setattr(service_mod.mesh, "project_name", lambda project: "fin")
    link = SimpleNamespace(alias="a", location=lambda env: "", reads=lambda env: "prod", product="crm/a", port="p",
                           accepts="3", columns={"id": "bigint"})
    have = {"id": "fin:a", "product_id": "crm/a", "port": "p", "reads": {"dev": "prod", "test": "prod", "prod": "prod"},
            "accepts": "3", "columns": {"id": "bigint"}}
    cat = Cat(list_products=[{"consumers": [have]}])
    assert bare._sync_links(SimpleNamespace(links={"a": link}), cat) == []
    assert cat.calls == [("list_products", {})]


# ---------------------------------------------------------------- moving keys


def _move(bare, monkeypatch, source, shared, share):  # noqa: F811
    monkeypatch.setattr(bare, "_keys_config", lambda: {})
    monkeypatch.setattr(bare, "_check_can_share", lambda: {})
    monkeypatch.setattr(bare, "_adopt_live_keys", lambda: False)
    monkeypatch.setattr(bare, "_share_sources", lambda: [("prod", source)])
    monkeypatch.setattr(bare, "_shared_keys", lambda: shared)
    monkeypatch.setattr(bare, "platform_status", lambda: {"s": 1})
    monkeypatch.setattr(service_mod.keys_home, "snapshot", lambda sources: {})
    monkeypatch.setattr(service_mod.keys_home, "share", lambda sources, to: dict(share))
    return _saving(bare, monkeypatch)


def test_only_sign_in_names_not_yet_admins_are_copied_to_the_shared_keys(bare, monkeypatch):  # noqa: F811
    source = Cat(list_admins=[{"user_name": "ana@contoso.com"}, {"user_name": "old@contoso.com"},
                              {"user_name": "Platform team"}], close_keys=PermissionError("no"))
    shared = Cat(whoami={}, list_admins=[{"user_name": "old@contoso.com"}], add_admin={})
    _move(bare, monkeypatch, source, shared, {})
    with pytest.raises(PermissionError):
        bare.platform_share_keys(approve=True)
    assert [c for c in shared.calls if c[0] == "add_admin"] == [("add_admin", {"user": "ana@contoso.com"})]


@pytest.mark.parametrize("ok", [True, False])
def test_the_keys_switch_only_when_every_table_checked_out(bare, monkeypatch, ok):  # noqa: F811
    source = Cat(list_admins=[], close_keys={})
    shared = Cat(whoami={}, list_admins=[], stage_keys={})
    saved = _move(bare, monkeypatch, source, shared, {"ok": ok, "tables": [], "renumbered": 0})
    out = bare.platform_share_keys(approve=True)
    assert out["ok"] is ok
    assert [c for c in shared.calls if c[0] == "stage_keys"][-1] == (
        ("stage_keys", {"staging": False, "live": True}) if ok else ("stage_keys", {"staging": True}))
    assert [w for w, cfg in saved if cfg.get("shared")] == (["keys"] if ok else [])
    assert ("message" in out) is not ok


# ---------------------------------------------------------------- workspaces, landing and products


def test_the_suggested_domain_follows_prod_first(bare, monkeypatch):  # noqa: F811
    envs = {"dev": {"workspace_id": "w1"}, "prod": {"workspace_id": "w2"}}
    monkeypatch.setattr(bare, "_project", lambda: SimpleNamespace(environments=envs))
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_domains=[
        {"id": "sales", "fabric_domain": "fd1"}, {"id": "emea", "parent_id": "sales", "fabric_domain": "fd2"}]))
    ws = {"w1": {"displayName": "Dev", "domainId": "fd1"}, "w2": {"displayName": "Prod", "domainId": "fd2"}}
    monkeypatch.setattr(Service, "fabric", property(lambda self: SimpleNamespace(get_workspace=ws.get)))
    assert bare.mesh_workspace_domains()["suggested"] == {"domain": "sales", "subdomain": "emea"}


def test_the_central_landing_page_names_the_landing_workspace(bare, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bare, "_platform_config", lambda: {})
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_sources=[]))
    monkeypatch.setattr(bare, "_landing_zones", lambda: [{"id": "shared"}])
    monkeypatch.setattr(bare, "_adopt_landing", lambda zones: {"workspace_id": "w1", "workspace_name": "Landing"})
    monkeypatch.setattr(bare, "_zone_files", lambda zones: ([], []))
    out = bare.platform_landing("test")
    assert (out["env"], out["kind"], out["where"], out["central"]) == ("test", "fabric", "Landing", True)


def test_a_product_shows_whether_the_project_links_it_and_its_own_health(bare, monkeypatch):  # noqa: F811
    products = [{"id": "crm/a", "project": "crm", "access": "public", "status": "published",
                 "consumers": [{"consumer_project": "fin"}]},
                {"id": "crm/b", "project": "crm", "access": "public", "status": "published",
                 "consumers": [{"consumer_project": "hr"}]}]
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_products=products, list_requests=[]))
    monkeypatch.setattr(bare, "_project", lambda: "project")
    monkeypatch.setattr(service_mod.mesh, "identity", lambda project: {"name": "fin"})
    monkeypatch.setattr(bare, "_my_follows", lambda: [])
    monkeypatch.setattr(bare, "_product_groups", lambda catalog: [])
    monkeypatch.setattr(bare, "_product_health", lambda: [{"product_id": "crm/b", "env": "prod", "status": "late"}])
    out = {p["id"]: p for p in bare.platform_products()["products"]}
    assert out["crm/a"]["linked"] is True and out["crm/b"]["linked"] is False
    assert out["crm/a"]["health"] == {}
    assert out["crm/b"]["health"] == {"prod": {"status": "late", "why": "", "latest": "", "latest_at": "",
                                               "checked_at": ""}}


# ---------------------------------------------------------------- Home


def _home(bare, monkeypatch, summary=None, **catalog):  # noqa: F811
    cat = Cat(list_products=[], whoami={"user": "ana"}, **catalog)
    monkeypatch.setattr(bare, "_catalog", lambda: cat)
    monkeypatch.setattr(bare, "_project", lambda: "project")
    monkeypatch.setattr(service_mod.mesh, "identity", lambda project: {"name": "fin"})
    monkeypatch.setattr(bare, "incidents_summary", lambda: summary or {"envs": []}, raising=False)
    seen = {}
    for view in ("team", "oncall", "org"):
        monkeypatch.setattr(service_mod.home, view, lambda *a, view=view: seen.setdefault(view, a) and view)
    return cat, seen


def test_home_names_each_environment_whose_incidents_couldnt_be_counted(bare, monkeypatch):  # noqa: F811
    summary = {"envs": [
        {"env": "prod", "count": None, "incidents": [], "observation": {"state": "error", "detail": "offline"}},
        {"env": "test", "count": None, "incidents": [], "observation": {"state": "not_set_up", "label": "-"}},
        {"env": "dev", "count": 2, "incidents": [], "observation": {"state": "ok", "detail": "fine"}}]}
    _home(bare, monkeypatch, summary, list_requests=[], list_follows=[], access_reviews=[])
    got = bare.mesh_home("team")
    assert got["problems"] == ["Incidents in Prod: offline"]
    assert got["incidents_read"] is True and got["project"] == {"name": "fin"}


def test_each_home_view_reads_its_own_parts(bare, monkeypatch):  # noqa: F811
    assert "access_reviews" in service_mod.core.OPERATIONS
    cat, seen = _home(bare, monkeypatch, list_requests=[{"id": 1}], list_follows=["f"], access_reviews=["r"],
                      list_domains=RuntimeError("offline"), list_seen_shortcuts=[])
    assert bare.mesh_home("team")["team"] == "team"
    assert seen["team"][3] == [{"id": 1}] and seen["team"][5] == ["r"]
    org = bare.mesh_home("org")
    assert org["org"] == "org" and seen["org"][0] == [] and "Domains: " in org["problems"][0]
    monkeypatch.setattr(bare, "platform_landing", lambda env: {"env": env})
    monkeypatch.setattr(service_mod.core, "OPERATIONS", {k: v for k, v in service_mod.core.OPERATIONS.items()
                                                         if k not in ("access_reviews", "owner_coverage")})
    oncall = bare.mesh_home("oncall")
    assert oncall["oncall"] == "oncall" and seen["oncall"][1] == {"env": "prod"} and seen["oncall"][3] is None
    assert ("access_reviews", {}) not in cat.calls[-4:]
