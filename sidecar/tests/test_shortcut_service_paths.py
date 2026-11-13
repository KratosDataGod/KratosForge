"""The shortcut command center's less-travelled paths: undo under the platform's shortcut rules, the
platform or Fabric not answering, who reads a shortcut, and the other environments' copies."""

import time
from types import SimpleNamespace

import pytest

from fabric_desktop import shortcut_service, shortcuts
from fabric_desktop.central import core
from fabric_desktop.fabric_api import FabricError

from test_shortcut_center import GOLD, GOLD_DEV, LH, S3, center_of, svc  # noqa: F401 - svc is the fixture

DEV2 = {"type": "oneLake", "workspace_id": "ws-dev2", "item_id": "lh-gold-dev", "path": "Tables/dbo/customer"}
NAMES = {"ws-prod": "Sales Prod", "ws-dev": "Sales Dev", "ws-dev2": "Sales Dev 2"}
ASSIGNMENTS = [{"principal": {"id": "u1", "displayName": "Ana", "type": "User"}, "role": "Contributor"},
               {"principal": {"id": "u2", "displayName": "Ben", "type": "User"}, "role": "Viewer"}]


def as_user(svc, user):  # noqa: F811
    svc._auth.signed_in_user = lambda: user


def named(svc):  # noqa: F811
    """Fabric names the workspaces, so a workspace's name places it in its environment."""
    svc.api.get_workspace = lambda ws: {"id": ws, "displayName": NAMES[ws]}


def target_of(svc, name):  # noqa: F811
    return next(s for s in svc.api.items["lh-1"] if s["name"] == name)["target"]


class Broken:
    def call(self, op, **kwargs):
        raise ConnectionError("the platform didn't answer")


# ---- undo makes a shortcut again, so the rules apply to it as to making one

def test_undo_of_a_removal_follows_only_admins_create(svc):  # noqa: F811
    as_user(svc, "chris@example.com")
    center_of(svc)
    assert svc._catalog().call("whoami")["admin"]  # the first in is its admin
    svc.delete_shortcut("ws-dev", "lh-1", "Files/raw", "events", "LH_Silver", "Sales Dev")
    removed = svc.shortcut_changes()["changes"][0]
    svc.shortcut_save_rules({"who_creates": "admins"})

    as_user(svc, "eve@example.com")
    assert not svc._catalog().call("whoami")["admin"]
    assert svc.shortcut_changes()["changes"][0]["undoable"]  # an admin's change: undo is offered
    with pytest.raises(PermissionError, match="Not undone: .*Only platform admins.*Nothing was changed"):
        svc.shortcut_undo(removed["id"])
    assert not any(s["name"] == "events" for s in svc.api.items["lh-1"])

    as_user(svc, "chris@example.com")
    svc.shortcut_undo(removed["id"])
    assert target_of(svc, "events") == S3


def test_undo_of_a_repoint_doesnt_point_dev_back_at_prod(svc):  # noqa: F811
    named(svc)
    svc.shortcut_repoint("ws-dev", "lh-1", "Tables/dbo", "customer", DEV2, "LH_Silver", "Sales Dev", "Sales Dev 2")
    repoint = svc.shortcut_changes()["changes"][0]
    with pytest.raises(PermissionError, match="Not undone: .*Dev at Prod.*Nothing was changed"):
        svc.shortcut_undo(repoint["id"])
    assert target_of(svc, "customer") == GOLD_DEV

    svc.shortcut_save_rules({"cross_env": "warn"})
    svc.shortcut_undo(repoint["id"])
    assert target_of(svc, "customer") == GOLD


def test_undo_of_a_removal_follows_the_allowed_connections(svc):  # noqa: F811
    svc.delete_shortcut("ws-dev", "lh-1", "Files/raw", "events", "LH_Silver", "Sales Dev")
    removed = svc.shortcut_changes()["changes"][0]
    svc.shortcut_save_rules({"connections": "block", "allowed_connections": ["c2"]})
    with pytest.raises(PermissionError, match="connection isn't one the platform team allows"):
        svc.shortcut_undo(removed["id"])
    assert not any(s["name"] == "events" for s in svc.api.items["lh-1"])


def test_undo_when_the_target_workspace_cant_be_named_still_checks_by_id(svc, monkeypatch):  # noqa: F811
    def gone(ws):
        raise FabricError("404", "WorkspaceNotFound", 404)
    svc.api.get_workspace = gone
    monkeypatch.setattr(type(svc), "_sc_envs", lambda self: {"ws-dev": "dev", "ws-prod": "prod", "ws-dev2": "dev"})
    svc.shortcut_repoint("ws-dev", "lh-1", "Tables/dbo", "customer", DEV2, "LH_Silver", "Sales Dev", "Sales Dev 2")
    with pytest.raises(PermissionError, match="Dev at Prod"):
        svc.shortcut_undo(svc.shortcut_changes()["changes"][0]["id"])
    assert target_of(svc, "customer") == GOLD_DEV


def test_source_is_the_dialogs_source_for_a_kept_target():
    assert shortcuts.source(GOLD) == {"type": "oneLake", "workspace_id": "ws-prod", "item_id": "lh-gold",
                                      "path": "Tables/dbo/customer"}
    assert shortcuts.source(S3) == {"type": "amazonS3", "location": "https://bucket.s3.amazonaws.com",
                                    "subpath": "/events", "connection_id": "c1"}
    for t in (GOLD, S3):
        assert shortcuts.same_target(shortcuts.target(shortcuts.source(t)), t)
    assert shortcuts.source({})["type"] == ""


# ---- undo's other answers

def test_undo_says_when_the_change_is_gone_or_the_platform_needs_repair(svc, monkeypatch):  # noqa: F811
    with pytest.raises(LookupError, match="isn't in the history"):
        svc.shortcut_undo("nope")

    class Old:
        def call(self, op, **kwargs):
            return [{"id": "c1", "kind": "removed"}]
    monkeypatch.setattr(type(svc), "_catalog", lambda self: Old())
    with pytest.raises(RuntimeError, match="Finish or repair"):
        svc.shortcut_undo("c1")


def test_undo_of_an_add_removes_it(svc):  # noqa: F811
    center_of(svc)
    svc.delete_shortcut("ws-dev", "lh-1", "Files/raw", "events", "LH_Silver", "Sales Dev")
    svc.shortcut_undo(svc.shortcut_changes()["changes"][0]["id"])
    added = svc.shortcut_changes()["changes"][0]
    assert added["kind"] == "added"
    out = svc.shortcut_undo(added["id"])
    assert out == {"ok": True, "problems": []}
    assert not any(s["name"] == "events" for s in svc.api.items["lh-1"])
    assert svc.shortcut_changes()["changes"][0]["kind"] == "removed"


def test_a_failed_point_back_puts_the_shortcut_as_it_was(svc):  # noqa: F811
    svc.shortcut_repoint("ws-dev", "lh-1", "Tables/dbo", "customer", DEV2, "LH_Silver", "Sales Dev", "Sales Dev 2")
    svc.api.fail_create = 1
    with pytest.raises(RuntimeError, match="The shortcut is as it was"):
        svc.shortcut_undo(svc.shortcut_changes()["changes"][0]["id"])
    assert target_of(svc, "customer") == GOLD_DEV


def test_only_added_removed_or_repointed_can_be_undone(svc, monkeypatch):  # noqa: F811
    class Health:
        def call(self, op, **kwargs):
            return [{"id": "c1", "kind": "broke", "undoable": True}]
    monkeypatch.setattr(type(svc), "_catalog", lambda self: Health())
    with pytest.raises(ValueError, match="Only an added, removed or repointed"):
        svc.shortcut_undo("c1")


# ---- repoint

def test_repoint_refuses_a_missing_shortcut_or_the_same_target(svc):  # noqa: F811
    with pytest.raises(LookupError, match="isn't in LH_Silver any more"):
        svc.shortcut_repoint("ws-dev", "lh-1", "Tables/dbo", "nope", DEV2, "LH_Silver")
    same = {"type": "oneLake", "workspace_id": "ws-prod", "item_id": "lh-gold", "path": "Tables/dbo/customer"}
    with pytest.raises(ValueError, match="already points there"):
        svc.shortcut_repoint("ws-dev", "lh-1", "Tables/dbo", "customer", same, "LH_Silver")


def test_repoint_that_cant_put_the_old_one_back_says_to_recreate(svc):  # noqa: F811
    svc.api.fail_create = 2
    with pytest.raises(RuntimeError, match="Putting it back failed too: use Recreate"):
        svc.shortcut_repoint("ws-dev", "lh-1", "Tables/dbo", "customer", DEV2, "LH_Silver", "Sales Dev", "Sales Dev 2")
    assert not any(s["name"] == "customer" for s in svc.api.items["lh-1"])
    change = svc.shortcut_changes()["changes"][0]
    assert change["kind"] == "removed" and change["before"]["target"] == GOLD


def test_a_change_the_platform_cant_record_is_still_made_and_said(svc, monkeypatch):  # noqa: F811
    real = svc._catalog()

    class NoLog:
        def call(self, op, **kwargs):
            if op == "log_shortcut_change":
                raise ConnectionError("the platform didn't answer")
            return real.call(op, **kwargs)
    monkeypatch.setattr(type(svc), "_catalog", lambda self: NoLog())
    out = svc.shortcut_repoint("ws-dev", "lh-1", "Tables/dbo", "customer", DEV2, "LH_Silver", "Sales Dev", "Sales Dev 2")
    assert out["ok"] and "couldn't be recorded" in out["problems"][0]
    assert target_of(svc, "customer") == GOLD_DEV


# ---- the platform or Fabric not answering

def test_the_center_shows_the_shortcuts_when_the_platform_doesnt_answer(svc, monkeypatch):  # noqa: F811
    center_of(svc)
    monkeypatch.setattr(type(svc), "_catalog", lambda self: Broken())
    svc._sc_recorded = None
    out = svc.shortcut_center([LH])
    assert len(out["rows"]) == 2 and not out["admin"]
    assert any("couldn't list seen shortcuts" in p for p in out["problems"])
    assert len(out["problems"]) == len(set(out["problems"]))  # each said once
    assert all(r["health"] == "not_checked" for r in out["rows"])


def test_lakehouses_read_for_an_earlier_list_are_left_out(svc):  # noqa: F811
    center_of(svc)
    out = svc.shortcut_center([LH | {"lakehouse_id": "lh-other"}])
    assert out["rows"] == []


def test_changes_the_platform_skipped_are_said_once(svc, monkeypatch):  # noqa: F811
    real = svc._catalog()

    class Skips:
        def call(self, op, **kwargs):
            if op == "record_shortcuts":
                return {"skipped": [{"id": "a"}, {"id": "b"}]}
            if op == "record_shortcut_health":
                return [{"kind": "skipped"}]
            return real.call(op, **kwargs)
    monkeypatch.setattr(type(svc), "_catalog", lambda self: Skips())
    out = center_of(svc)
    assert any("didn't record 2 changes" in p for p in out["problems"])
    assert "didn't record 1 change " in shortcut_service._skipped_problem([{}])

    class Files:
        def __init__(self, token):
            pass

        def probe(self, ws, lh, path):
            return 200, None
    monkeypatch.setattr(shortcut_service, "FileStore", Files)
    monkeypatch.setattr(type(svc), "_governance_notify", lambda self, build: "Teams didn't answer")
    out = svc.shortcut_check(out["rows"])
    assert out["changes"] == [] and "didn't record 1 change " in out["problems"][0]
    assert out["problems"][1] == "Teams didn't answer"


def test_export_waits_while_the_shortcuts_are_read(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "shortcut_center", lambda self, lakehouses, refresh=False: {"pending": True})
    with pytest.raises(ValueError, match="still being read"):
        svc.shortcut_export([LH])


def test_environments_come_from_the_project_and_the_platform(svc, monkeypatch):  # noqa: F811
    project = SimpleNamespace(environments={"dev": {"workspace_id": "w-dev", "workspaces": {"gold": {"id": "w-dev-gold"},
                                                                                           "bad": "x"}},
                                            "prod": {"workspace_id": "w-prod", "workspaces": {"gold": {"id": "w-prod-gold"}}}})
    monkeypatch.setattr(type(svc), "_project", lambda self: project, raising=False)
    monkeypatch.setattr(type(svc), "_platform_config", lambda self: {"test": {"workspace_id": "w-test"},
                                                                      "dev": {"workspace_id": "w-dev"}})
    assert svc._sc_envs() == {"w-dev": "dev", "w-dev-gold": "dev", "w-prod": "prod", "w-prod-gold": "prod",
                              "w-test": "test"}
    assert sorted(map(sorted, map(dict.items, svc._sc_env_groups()))) == [
        [("dev", "w-dev"), ("prod", "w-prod")], [("dev", "w-dev-gold"), ("prod", "w-prod-gold")]]

    def none(self):
        raise LookupError("no project")

    def broken(self):
        raise OSError("settings unreadable")
    monkeypatch.setattr(type(svc), "_project", none, raising=False)
    monkeypatch.setattr(type(svc), "_platform_config", broken)
    assert svc._sc_envs() == {} and svc._sc_env_groups() == []


def test_a_workspace_whose_lakehouses_cant_be_listed_is_skipped(svc, monkeypatch):  # noqa: F811
    def lakehouses(ws):
        if ws == "ws-paused":
            raise FabricError("403", "CapacityNotActive", 403)
        return [{"id": "lh-1", "displayName": "LH_Silver"}]
    svc.api.list_workspaces = lambda: [{"id": "ws-paused", "displayName": "Paused"}, {"id": "ws-dev", "displayName": "Sales Dev"}]
    svc.api.list_lakehouses = lakehouses
    svc._auth.fabric_token = lambda: "tok"
    assert svc._sc_lakehouses() == [{"workspace_id": "ws-dev", "workspace_name": "Sales Dev", "lakehouse_id": "lh-1",
                                     "lakehouse_name": "LH_Silver"}]


def test_admin_is_no_when_the_platform_cant_say(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "_catalog", lambda self: Broken())
    assert svc._sc_admin() is False
    assert svc._sc_me() == {"user": "", "admin": False}
    assert not svc._sc_may_decide_twin({"user": "", "admin": False}, {})


def test_a_product_list_that_cant_be_read_leaves_the_product_out(svc, monkeypatch):  # noqa: F811
    real = svc._catalog()

    class NoProducts:
        def call(self, op, **kwargs):
            if op == "list_products":
                raise ConnectionError("the platform didn't answer")
            return real.call(op, **kwargs)
    monkeypatch.setattr(type(svc), "_catalog", lambda self: NoProducts())
    out = svc._sc_new("ws-dev", "Sales Dev", DEV2, "Sales Dev 2")
    assert out["product"] is None and out["target_type"] == "OneLake"


def test_a_platform_error_that_isnt_a_missing_function_is_raised(svc, monkeypatch):  # noqa: F811
    class Says:
        def __init__(self, text):
            self.text = text

        def call(self, op, **kwargs):
            raise core.PlatformError(self.text)
    monkeypatch.setattr(type(svc), "_catalog", lambda self: Says("No operation accept_shortcut_risk"))
    with pytest.raises(LookupError, match="no accept_shortcut_risk function"):
        svc.shortcut_accept_risk("s", "cross_env", "on purpose")
    monkeypatch.setattr(type(svc), "_catalog", lambda self: Says("Only platform admins accept a shortcut's risk"))
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        svc.shortcut_accept_risk("s", "cross_env", "on purpose")


# ---- who reads a shortcut

class Fabric:
    def list_role_assignments(self, ws):
        if ws == "ws-hidden":
            raise FabricError("403", "InsufficientPrivileges", 403)
        return ASSIGNMENTS


ROW = {"id": "lh-1|Tables/dbo/customer", "host_workspace_id": "ws-dev", "lakehouse_id": "lh-1",
       "full_path": "Tables/dbo/customer", "target_type": "OneLake", "workspace_id": "ws-prod", "item_id": "lh-gold",
       "target_path": "Tables/dbo/customer"}


def test_readers_compare_the_shortcut_with_its_target(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "fabric", property(lambda self: Fabric()), raising=False)
    out = svc.shortcut_readers(ROW)
    assert {r["id"] for r in out["readers"]} == {"u1"} and not out["partial"]
    hidden = svc.shortcut_readers(ROW | {"host_workspace_id": "ws-hidden"})
    assert hidden["text"] == "You can't see the roles of the workspace it's in" and hidden["here"] is None


def test_access_is_read_in_the_background_and_kept(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "fabric", property(lambda self: Fabric()), raising=False)
    bad = ROW | {"id": "bad"}
    del bad["full_path"]
    assert svc.shortcut_access([ROW, bad]) == {"pending": True, "items": {}}
    for _ in range(200):
        out = svc.shortcut_access([ROW, bad])
        if not out["pending"]:
            break
        time.sleep(0.02)
    assert not out["pending"] and "readers" not in out["items"][ROW["id"]]
    assert out["items"]["bad"]["gap"] is None and "full_path" in out["items"]["bad"]["text"]
    assert svc.shortcut_access([ROW], refresh=True)["pending"]


# ---- approving a request, and the other environments' copies

def test_only_someone_who_may_decide_approves_a_shortcut_request(svc, monkeypatch):  # noqa: F811
    class Waiting:
        def call(self, op, **kwargs):
            return [{"id": "r1", "kind": "shortcut", "can_decide": False, "detail": {}}]
    monkeypatch.setattr(type(svc), "_catalog", lambda self: Waiting())
    with pytest.raises(PermissionError, match="Only a platform admin, or the owner"):
        svc.platform_decide("r1", True)
    with pytest.raises(ValueError, match="isn't waiting any more"):
        svc.shortcut_request_envs("r2")


REQ = {"id": "r1", "kind": "shortcut", "detail": {"where": "Tables/dbo", "name": "supplier"}}
PLACE = {"env": "test", "workspace_id": "ws-test", "workspace_name": "Sales Test", "lakehouse_id": "lh-t",
         "lakehouse_name": "LH_Silver", "target_workspace_name": "Sales Test",
         "source": {"type": "oneLake", "workspace_id": "ws-gold-test", "item_id": "lh-gt", "path": "Tables/dbo/supplier"}}


def test_twins_say_why_they_werent_made(svc, monkeypatch):  # noqa: F811
    def unlisted(self, req):
        raise FabricError("401", "TokenExpired", 401)
    monkeypatch.setattr(type(svc), "_sc_twin_plan", unlisted)
    assert "lakehouses couldn't be listed" in svc._sc_make_twins(REQ, "why")[0]

    monkeypatch.setattr(type(svc), "_sc_twin_plan", lambda self, req: {"why": "No other environments", "places": []})
    assert svc._sc_make_twins(REQ, "why") == ["No other environments"]

    plan = {"why": "", "places": [PLACE | {"env": "prod", "why": "no matching lakehouse"}, PLACE]}
    monkeypatch.setattr(type(svc), "_sc_twin_plan", lambda self, req: plan)

    def refused(self, *args, **kwargs):
        raise FabricError("400 Bad request", "BadRequest", 400)
    monkeypatch.setattr(type(svc), "_create_shortcut", refused)
    assert svc._sc_make_twins(REQ, "why") == ["Not made in Prod: no matching lakehouse",
                                              "Not made in Test (Sales Test / LH_Silver): FabricError: 400 Bad request"]


def test_a_twin_already_there_and_pointing_at_its_match_is_kept(svc, monkeypatch):  # noqa: F811
    svc.api.items["lh-t"] = [{"name": "supplier", "path": "Tables/dbo", "target": shortcuts.target(PLACE["source"])}]
    assert svc._sc_twin_there(PLACE, REQ["detail"], "Sales Test / LH_Silver") == (
        "Test already had it in Sales Test / LH_Silver, pointing at its Test match, so it was kept")
    svc.api.items["lh-t"][0]["target"] = GOLD
    assert "pointing somewhere else" in svc._sc_twin_there(PLACE, REQ["detail"], "Sales Test / LH_Silver")


def test_a_platform_admin_may_decide_every_twin(svc):  # noqa: F811
    assert svc._sc_may_decide_twin({"user": "", "admin": True}, {})


def test_a_platform_not_set_up_says_so_in_its_own_words(svc, monkeypatch):  # noqa: F811
    from fabric_desktop.errors import NotSetUp

    class Unset:
        def call(self, op, **kwargs):
            raise NotSetUp("Set up the platform first")
    monkeypatch.setattr(type(svc), "_catalog", lambda self: Unset())
    with pytest.raises(NotSetUp, match="Set up the platform first"):
        svc.shortcut_withdraw_risk("s", "cross_env")


# ---- what a shortcut may be

def test_a_shortcut_goes_in_a_schema_or_under_files_and_points_somewhere_real():
    assert shortcuts.location("") == "Tables/dbo" and shortcuts.location("/Files/raw/") == "Files/raw"
    for bad in ("Other", "Tables/dbo/deeper"):
        with pytest.raises(ValueError):
            shortcuts.location(bad)
    with pytest.raises(ValueError, match="Pick the lakehouse"):
        shortcuts.target({"type": "oneLake", "workspace_id": "w", "item_id": "i", "path": "/"})
    with pytest.raises(ValueError, match="starting with https://"):
        shortcuts.target({"type": "adlsGen2", "location": "http://acct.dfs.core.windows.net", "connection_id": "c"})
    with pytest.raises(ValueError, match="Pick the cloud connection"):
        shortcuts.target({"type": "amazonS3", "location": "https://b.s3.amazonaws.com"})
    with pytest.raises(ValueError, match="OneLake, ADLS Gen2 or Amazon S3"):
        shortcuts.target({"type": "dataverse"})
    with pytest.raises(ValueError, match="without"):
        shortcuts.check_name("a/b")


def test_only_storage_connections_are_offered_for_a_shortcut():
    rows = shortcuts.connection_rows([
        {"id": "2", "displayName": "s3 events", "connectionDetails": {"type": "AmazonS3", "path": "https://b"}},
        {"id": "1", "connectionDetails": {"type": "AzureDataLakeStorage", "path": "https://a"}},
        {"id": "3", "displayName": "SQL", "connectionDetails": {"type": "SQL"}},
        {"id": "4"}])
    assert rows == [{"id": "1", "name": "1", "type": "adlsGen2", "path": "https://a"},
                    {"id": "2", "name": "s3 events", "type": "amazonS3", "path": "https://b"}]
