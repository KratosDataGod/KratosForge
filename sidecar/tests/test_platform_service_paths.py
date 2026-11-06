"""The platform service's less-travelled paths: admins kept in every environment's platform, setting up
and joining, older platforms, and the checks before a domain, a designation or a key move."""

from types import SimpleNamespace

import pytest

from fabric_desktop.central import core
from fabric_desktop.central import service as central_service
from fabric_desktop.central.client import LocalPlatform
from fabric_desktop.meta.project import GateError
from fabric_desktop.service import Service


class Auth:
    def __init__(self, user):
        self.user = user

    def signed_in_user(self):
        return self.user

    def _token(self, scopes):
        return "tok"

    def fabric_token(self):
        return "tok"

    def storage_token(self):
        return "tok"


class Broken:
    path = "broken"

    def call(self, op, **kwargs):
        raise ConnectionError("the platform didn't answer")


class Says:
    def __init__(self, exc):
        self.exc = exc

    def call(self, op, **kwargs):
        raise self.exc


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s._auth = Auth("chris@example.com")
    return s


def admins_in(svc, env):
    return sorted(a["user_name"] for a in LocalPlatform(svc._platform(env).path, lambda: "x").call("list_admins"))


# ---- admins live in every environment's platform: one left behind is said, never passed over

def test_admins_are_claimed_added_and_removed_in_every_environment(svc):
    assert svc.platform_claim_admin()["admin"]
    svc.platform_add_admin("ana@example.com")
    assert all(admins_in(svc, env) == ["ana@example.com", "chris@example.com"] for env in ("dev", "test", "prod"))
    svc.platform_remove_admin("ana@example.com")
    assert all(admins_in(svc, env) == ["chris@example.com"] for env in ("dev", "test", "prod"))


def test_a_removal_a_platform_didnt_take_is_said_and_finished_by_running_it_again(svc, monkeypatch):
    svc.platform_claim_admin()
    svc.platform_add_admin("ana@example.com")
    real = Service._platform
    monkeypatch.setattr(Service, "_platform", lambda self, env: Broken() if env == "dev" else real(self, env))
    with pytest.raises(RuntimeError, match=r"Done in Prod, Test, but not in Dev \(the platform didn't answer\)"):
        svc.platform_remove_admin("ana@example.com")
    monkeypatch.setattr(Service, "_platform", real)
    assert admins_in(svc, "dev") == ["ana@example.com", "chris@example.com"]  # still an admin there
    assert [a["user_name"] for a in svc.platform_admins()] == ["chris@example.com"]  # the list shows the catalog's

    svc.platform_remove_admin("ana@example.com")  # Prod and Test have done it already: Dev is reached
    assert all(admins_in(svc, env) == ["chris@example.com"] for env in ("dev", "test", "prod"))
    with pytest.raises(core.PlatformError, match="isn't a platform admin"):
        svc.platform_remove_admin("ana@example.com")


def test_an_add_a_platform_didnt_take_is_said(svc, monkeypatch):
    svc.platform_claim_admin()
    real = Service._platform
    monkeypatch.setattr(Service, "_platform", lambda self, env: Broken() if env == "test" else real(self, env))
    with pytest.raises(RuntimeError, match=r"Done in Prod, Dev, but not in Test"):
        svc.platform_add_admin("ana@example.com")


def test_a_refusal_in_the_catalog_changes_nothing(svc):
    svc.platform_claim_admin()
    svc._auth = Auth("eve@example.com")
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        svc.platform_add_admin("eve@example.com")
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        svc.platform_remove_admin("chris@example.com")
    assert all(admins_in(svc, env) == ["chris@example.com"] for env in ("dev", "test", "prod"))


def test_a_non_admin_removing_someone_already_gone_from_the_catalog_is_refused(svc, monkeypatch):
    svc.platform_claim_admin()
    svc.platform_add_admin("ana@example.com")
    LocalPlatform(svc._platform("prod").path, lambda: "chris@example.com").call("remove_admin", user="ana@example.com")
    svc._auth = Auth("eve@example.com")
    with pytest.raises(core.PlatformError, match="Only platform admins"):  # the catalog asks first
        svc.platform_remove_admin("ana@example.com")
    assert admins_in(svc, "dev") == ["ana@example.com", "chris@example.com"]


def test_shared_keys_admins_are_kept_too(svc, monkeypatch):
    svc.platform_claim_admin()
    monkeypatch.setattr(Service, "_keys_config", lambda self: {"shared": True})
    svc.platform_add_admin("ana@example.com")
    assert "ana@example.com" in [a["user_name"] for a in svc._shared_keys().call("list_admins")]


# ---- setting up and joining

def test_setting_up_joining_and_forgetting_a_platform(svc, monkeypatch):
    calls = []
    monkeypatch.setattr(central_service.setup_mod, "setup",
                        lambda fabric, token, env, name, cap, current: {"config": {"workspace_id": "w-" + env},
                                                                         "ready": True, "steps": []})
    monkeypatch.setattr(central_service.setup_mod, "join", lambda fabric, env, ws: {"workspace_id": ws})
    monkeypatch.setattr(Service, "_register_open_project", lambda self: calls.append("registered"))
    out = svc.platform_setup("prod", "Platform")
    assert out["status"]["environments"]["prod"]["workspace_id"] == "w-prod" and calls == ["registered"]
    svc.platform_setup("dev")
    assert calls == ["registered"]  # only the catalog's environment registers the project
    assert svc.platform_join("test", "w-joined")["environments"]["test"]["workspace_id"] == "w-joined"
    assert "workspace_id" not in svc.platform_forget("test")["environments"]["test"]


def test_setting_up_and_joining_the_keys_workspace(svc, monkeypatch):
    adopted = []
    monkeypatch.setattr(central_service.keys_home, "setup",
                        lambda fabric, token, name, cap, current: {"config": {"workspace_name": name or "Keys"},
                                                                    "ready": True})
    monkeypatch.setattr(central_service.keys_home, "join", lambda fabric, ws: {"workspace_id": ws})
    monkeypatch.setattr(Service, "_adopt_live_keys", lambda self: adopted.append(1) or False)
    assert svc.platform_keys_setup("Keys")["status"]["keys"]["workspace_name"] == "Keys"
    assert svc.platform_keys_join("w-keys")["keys"]["workspace_id"] == "w-keys"
    assert len(adopted) == 2


def test_an_organization_set_platform_isnt_changed_here(svc, monkeypatch):
    monkeypatch.setattr(Service, "_platform_source", lambda self: "policy")
    with pytest.raises(PermissionError, match="Your organization sets the platform"):
        svc.platform_forget("dev")
    svc.platform_claim_admin()
    monkeypatch.setattr(Service, "_central_landing", lambda self: {})
    with pytest.raises(PermissionError, match="platform.landing"):
        svc.platform_landing_setup()


def test_a_landing_zone_the_catalog_cant_record_leaves_setup_unfinished(svc, monkeypatch):
    svc.platform_claim_admin()
    monkeypatch.setattr(central_service.setup_mod, "landing_setup", lambda fabric, name, cap, current: {
        "config": {"workspace_id": "w-land", "lakehouse_id": "lh-land"}, "ready": True, "steps": []})

    def old(self, op, **kwargs):
        raise LookupError("The platform can't use landing zones yet")
    monkeypatch.setattr(Service, "_zone_call", old)
    out = svc.platform_landing_setup()
    assert not out["ready"] and out["steps"][-1]["status"] == "failed"
    assert not svc._central_landing()  # runs read as before until the zone is recorded


def test_an_older_platform_says_to_finish_or_repair_for_landing_zones(svc, monkeypatch):
    for exc, said in ((core.PlatformError("No operation list_landing_zones"), LookupError),
                      (core.PlatformError("Only platform admins remove landing zones"), core.PlatformError)):
        monkeypatch.setattr(Service, "_catalog", lambda self, exc=exc: Says(exc))
        with pytest.raises(said):
            svc._zone_call("list_landing_zones")


# ---- domains

def test_creating_a_domain_checks_who_and_where_before_fabric(svc, monkeypatch):
    def no_fabric(self, write=False):
        raise AssertionError("Fabric isn't asked")
    monkeypatch.setattr(Service, "_fabric_admin", no_fabric)
    svc.platform_claim_admin()
    svc.platform_save_domain({"name": "Sales"})
    svc.platform_save_domain({"name": "Sales EU", "parent_id": "sales"})
    with pytest.raises(ValueError, match="doesn't exist"):
        svc.platform_create_domain({"name": "Retail", "parent_id": "nope"})
    monkeypatch.setattr(Service, "platform_domains", lambda self: [
        {"id": "sales", "name": "Sales", "fabric_domain": "f1"},
        {"id": "sales-eu", "name": "Sales EU", "fabric_domain": "f2", "parent_id": "sales"}])
    with pytest.raises(ValueError, match="one level deep"):
        svc.platform_create_domain({"name": "Retail", "parent_id": "sales-eu"})
    svc._auth = Auth("eve@example.com")
    with pytest.raises(PermissionError, match="Only platform admins can create domains"):
        svc.platform_create_domain({"name": "Retail"})


def test_deleting_a_domain(svc):
    svc.platform_claim_admin()
    svc.platform_save_domain({"name": "Sales"})
    assert svc.platform_delete_domain("sales") == []


# ---- older platforms and reads that don't stop a page

def test_access_reviews_on_an_older_platform_say_what_to_do(svc, monkeypatch):
    monkeypatch.setattr(Service, "_catalog", lambda self: Says(LookupError("UDF_Platform wasn't found")))
    out = svc.platform_access_reviews()
    assert not out["available"] and "Finish or repair" in out["problem"]
    with pytest.raises(LookupError, match="Finish or repair"):
        svc.platform_confirm_access("p1", ["crm"])
    assert svc._product_health() is None and svc._my_follows() is None
    monkeypatch.setattr(Service, "_catalog", lambda self: Says(core.PlatformError("No operation list_follows")))
    assert svc._my_follows() is None
    monkeypatch.setattr(Service, "_catalog", lambda self: Says(core.PlatformError("No operation list_product_status")))
    assert svc._product_health() is None
    monkeypatch.setattr(Service, "_catalog", lambda self: Says(core.PlatformError("Only members")))
    for read in (svc._product_health, svc._my_follows):
        with pytest.raises(core.PlatformError, match="Only members"):
            read()


def test_revoking_access_is_for_the_owner_or_an_admin(svc):
    svc.platform_claim_admin()
    svc._auth = Auth("eve@example.com")
    with pytest.raises(core.PlatformError):
        svc.platform_revoke("sales.customer", "crm")


def test_noting_a_visit_never_gets_in_the_way(svc, monkeypatch):
    monkeypatch.setattr(Service, "_catalog", lambda self: Broken())
    svc._note_seen("chris@example.com")  # logged, not raised


def test_registering_the_open_project(svc, monkeypatch):
    def none(self):
        raise LookupError("no project")
    monkeypatch.setattr(Service, "_project", none, raising=False)
    svc._register_open_project()  # nothing open: nothing to register

    kept = []
    project = SimpleNamespace(info={"name": "crm"})
    monkeypatch.setattr(Service, "_project", lambda self: project, raising=False)
    monkeypatch.setattr(central_service.mesh, "identity", lambda p: {"name": "crm"})
    monkeypatch.setattr(central_service.mesh, "landing_readers", lambda p: [])
    monkeypatch.setattr(Service, "_keep_catalog_domain", staticmethod(lambda p, out: kept.append(out)))
    monkeypatch.setattr(Service, "_catalog", lambda self: Says(core.PlatformError("Not a member")))
    svc._register_open_project()  # not a member: setting up still finishes
    assert kept == []

    class Registers:
        def call(self, op, **kwargs):
            return {"registered": kwargs["project"]["name"]}
    monkeypatch.setattr(Service, "_catalog", lambda self: Registers())
    svc._register_open_project()
    assert kept == [{"registered": "crm"}]


# ---- designations and keys

def test_a_designation_names_a_known_environment_and_kind(svc):
    with pytest.raises(ValueError, match="environment must be one of"):
        svc.meta_set_designation("qa", "ingest")
    with pytest.raises(ValueError, match="designation must be one of"):
        svc.meta_set_designation("dev", "nope")


def test_a_new_designation_workspace_is_named_for_the_project(svc, monkeypatch):
    made, set_ = [], []
    monkeypatch.setattr(Service, "_project", lambda self: SimpleNamespace(), raising=False)
    monkeypatch.setattr(central_service.mesh, "identity", lambda p: {"subdomain": "", "domain": "sales-ops", "name": "crm"})
    designation = central_service.mesh.DESIGNATIONS[0]
    fabric = SimpleNamespace(create_workspace=lambda name, cap, desc: made.append(name) or {"id": "w-new"})
    monkeypatch.setattr(Service, "fabric", property(lambda self: fabric))
    monkeypatch.setattr(Service, "meta_set_designation", lambda self, *a: set_.append(a) or {"ok": True})
    assert svc.meta_create_designation_workspace("dev", designation, "cap") == {"ok": True}
    label = central_service.mesh.DESIGNATION_LABELS[designation]
    assert made == [f"Sales Ops {label} - Dev"] and set_ == [("dev", designation, "w-new", f"Sales Ops {label} - Dev")]


def test_keys_go_back_to_the_lakehouse_when_the_platform_holds_none(svc, monkeypatch):
    written = []
    project = SimpleNamespace(environments={"dev": {"keys": "platform", "keys_moved_at": "x"}})
    monkeypatch.setattr(Service, "_project", lambda self: project, raising=False)
    monkeypatch.setattr(Service, "_keys_in_platform", lambda self, p, env: False, raising=False)
    monkeypatch.setattr(Service, "_write_environments", lambda self, p, envs: written.append(envs), raising=False)
    assert svc.meta_keys_to_lakehouse("dev") == {"env": "dev", "keys": "lakehouse"}
    assert written == [{"dev": {"keys": "lakehouse"}}]
    monkeypatch.setattr(Service, "_keys_in_platform", lambda self, p, env: True, raising=False)
    assert svc.meta_keys_to_platform("dev") == {"env": "dev", "ok": True, "already": True, "tables": []}


def test_links_that_dont_resolve_say_why(svc, monkeypatch):
    link = SimpleNamespace(alias="cust", product="sales.customer", port="main", location=lambda env: "",
                           reads=lambda env: env)
    project = SimpleNamespace(links={"cust": link})
    monkeypatch.setattr(Service, "_project", lambda self: project, raising=False)

    class NoPort:
        def call(self, op, **kwargs):
            return None
    monkeypatch.setattr(Service, "_catalog", lambda self: NoPort())
    out = svc.mesh_resolve_links("dev")
    assert out == [{"alias": "cust", "ok": False,
                    "message": "sales.customer hasn't published port main in dev yet. Its owner publishes it when "
                               "they promote to dev."}]
    with pytest.raises(LookupError, match="isn't in metadata/links"):
        svc._link_table(project, "dev")("nope")


def test_saving_text_for_an_unknown_entity_is_refused(svc, monkeypatch):
    monkeypatch.setattr(Service, "_project", lambda self: SimpleNamespace(entities={}), raising=False)
    with pytest.raises(LookupError, match="isn't an entity"):
        svc.mesh_save_about("gold.nope", {"description": "x"})


def test_cancelling_a_key_move_that_went_live_is_refused(svc, monkeypatch):
    monkeypatch.setattr(Service, "_check_can_share", lambda self: {})
    monkeypatch.setattr(Service, "_adopt_live_keys", lambda self: True)
    with pytest.raises(GateError, match="already moved"):
        svc.platform_cancel_share_keys(approve=True)
