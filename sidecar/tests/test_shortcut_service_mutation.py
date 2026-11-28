"""Shortcut command center paths that mutation testing (batch 15, docs/quality/mutation-testing.md)
showed no test checked: the history windows asked of the platform, the environments a workspace is
placed in, finding where a lost lakehouse went, repointing several at once, who reads a shortcut, undo
of a change it can't name, and a shortcut request whose shortcut Fabric says is there already. Fabric
and the platform are the fakes from test_shortcut_center; no tenant is called."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop import shortcut_service
from fabric_desktop.central import core
from fabric_desktop.central.client import LocalPlatform
from fabric_desktop.fabric_api import FabricError

from test_shortcut_center import LH, center_of, svc  # noqa: F401 - svc is the fixture


@pytest.fixture(autouse=True)
def app_data(tmp_path, monkeypatch):
    """App data (Databricks sync pairs, settings) goes to this test's folder, never the real one."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

DEV2 = {"type": "oneLake", "workspace_id": "ws-dev2", "item_id": "lh-gold-dev", "path": "Tables/dbo/product"}
SRC = {"type": "oneLake", "workspace_id": "ws-dev2", "item_id": "lh-gold-dev", "path": "Tables/dbo/supplier"}
WANTED = {"oneLake": {"workspaceId": "ws-dev2", "itemId": "lh-gold-dev", "path": "Tables/dbo/supplier"}}
KEPT = "supplier was already in LH_Silver, pointing where this request asks, so it was kept"


def _calls(monkeypatch, op):
    seen = []
    real = LocalPlatform.call

    def call(self, name, **kwargs):
        if name == op:
            seen.append(kwargs)
        return real(self, name, **kwargs)
    monkeypatch.setattr(LocalPlatform, "call", call)
    return seen


# ---- what is asked of the platform

def test_history_reads_ask_the_platform_for_these_windows(svc, monkeypatch):  # noqa: F811
    assert isinstance(svc._catalog(), LocalPlatform)
    seen = _calls(monkeypatch, "shortcut_changes")
    svc.shortcut_changes()
    assert seen == [{"days": 30, "limit": 2000, "shortcut": ""}]
    center_of(svc)
    assert {"days": 7} in seen and {"days": 3650, "limit": 5000} in seen
    svc.delete_shortcut("ws-dev", "lh-1", "Files/raw", "events", "LH_Silver", "Sales Dev")
    change = svc.shortcut_changes()["changes"][0]
    seen.clear()
    svc.shortcut_undo(change["id"])
    assert seen[0] == {"days": 3650, "limit": 100000}


def test_saving_rules_answers_with_the_rules_now(svc):  # noqa: F811
    out = svc.shortcut_save_rules({"cross_env": "warn"})
    assert out["rules"]["cross_env"] == "warn" and out["text"] and out["problems"] == []


def test_a_repoint_the_rules_warn_about_says_so(svc):  # noqa: F811
    svc.shortcut_save_rules({"cross_env": "warn"})
    prod = {"type": "oneLake", "workspace_id": "ws-prod", "item_id": "lh-gold", "path": "Tables/dbo/product"}
    out = svc.shortcut_repoint("ws-dev", "lh-1", "Tables/dbo", "customer", prod, "LH_Silver", "Sales Dev", "Sales Prod")
    assert out["ok"] and isinstance(out["warnings"], list) and out["warnings"]


# ---- the command center's rows and counts

def test_rows_keep_when_they_were_first_seen_and_place_the_target_by_its_workspace_id(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "_sc_envs", lambda self: {"ws-dev": "dev", "ws-prod": "prod"})
    rows = {r["name"]: r for r in center_of(svc)["rows"]}
    assert rows["customer"]["first_seen"] and rows["customer"]["target_env"] == "prod"


def test_the_weeks_count_is_shortcuts_added_removed_or_repointed(svc, monkeypatch):  # noqa: F811
    center_of(svc)
    svc.shortcut_repoint("ws-dev", "lh-1", "Tables/dbo", "customer", DEV2, "LH_Silver", "Sales Dev", "Sales Dev 2")

    class Files:
        def __init__(self, token):
            pass

        def probe(self, ws, lh, path):
            return 404, "gone"
    monkeypatch.setattr(shortcut_service, "FileStore", Files)
    svc.shortcut_check(center_of(svc, refresh=True)["rows"])  # both break: changes, but not ones the count counts
    assert sum(c["kind"] == "broke" for c in svc.shortcut_changes()["changes"]) == 2
    assert center_of(svc)["counts"]["changed_7d"] == 1


def test_only_known_environments_from_the_platform_place_a_workspace(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "_project", lambda self: (_ for _ in ()).throw(LookupError("no project")))
    monkeypatch.setattr(type(svc), "_platform_config", lambda self: {
        "sandbox": {"workspace_id": "ws-s"}, "prod": {"workspace_id": "ws-p"}, "test": {}})
    assert svc._sc_envs() == {"ws-p": "prod"}


def test_a_layer_in_one_environment_only_is_not_a_group(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "_project", lambda self: SimpleNamespace(environments={
        "dev": {"workspace_id": "ws-d", "workspaces": {"gold": {"id": "ws-dg"}}}, "prod": {"workspace_id": "ws-p"}}))
    assert svc._sc_env_groups() == [{"dev": "ws-d", "prod": "ws-p"}]


def test_export_makes_the_downloads_folder_and_its_parents(svc, tmp_path, monkeypatch):  # noqa: F811
    folder = tmp_path / "home" / "me" / "Downloads"
    monkeypatch.setattr(shortcut_service.known_folders, "downloads", lambda: folder)
    center_of(svc)
    out = svc.shortcut_export([LH])
    assert out["rows"] == 2 and (folder / out["path"].split("\\")[-1].split("/")[-1]).is_file()


# ---- where a lost lakehouse went

LOST = [{"id": "s1", "target_lakehouse_name": "LH_Gold", "item_id": "lh-gold", "target_path": "Tables/a"},
        {"id": "s2", "target_lakehouse_name": "LH_Gold", "item_id": "lh-gold", "target_path": "Tables/b"}]


def _found(svc, monkeypatch, n, statuses):  # noqa: F811
    monkeypatch.setattr(type(svc), "_sc_lakehouses", lambda self: [
        {"workspace_id": f"ws-{i}", "workspace_name": f"W{i}", "lakehouse_id": f"c{i}", "lakehouse_name": "LH_Gold"}
        for i in range(1, n + 1)])

    class Files:
        def __init__(self, token):
            pass

        def probe(self, ws, lh, path):
            return statuses.get((lh, path), 404), ""
    monkeypatch.setattr(shortcut_service, "FileStore", Files)


def test_finding_a_moved_lakehouse_without_its_name_lists_nothing(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "_sc_lakehouses", lambda self: pytest.fail("the lakehouses were listed"))
    shortcuts = [{"id": "s1", "item_id": "lh-gold", "target_path": "Tables/a"}]
    assert svc.shortcut_find_moved(shortcuts) == {"candidate": None, "found": [], "missing": ["s1"], "name": ""}


def test_the_lakehouse_with_more_of_the_tables_wins_and_a_300_isnt_there(svc, monkeypatch):  # noqa: F811
    _found(svc, monkeypatch, 2, {("c1", "Tables/a"): 200, ("c1", "Tables/b"): 300,
                                 ("c2", "Tables/a"): 204, ("c2", "Tables/b"): 200})
    out = svc.shortcut_find_moved(LOST)
    assert out["candidate"]["lakehouse_id"] == "c2" and out["found"] == ["s1", "s2"] and out["missing"] == []


def test_only_the_first_five_lakehouses_with_the_name_are_asked(svc, monkeypatch):  # noqa: F811
    _found(svc, monkeypatch, 6, {("c6", "Tables/a"): 200, ("c6", "Tables/b"): 200})
    out = svc.shortcut_find_moved(LOST)
    assert out["candidate"]["lakehouse_id"] == "c1" and out["found"] == [] and out["missing"] == ["s1", "s2"]


# ---- several at once, and who reads one

def test_repointing_several_says_each_warning_and_problem_once(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "shortcut_repoint",
                        lambda self, *a, **k: {"ok": True, "warnings": ["w"], "problems": ["p"]})
    items = [{"host_workspace_id": "ws-dev", "lakehouse_id": "lh-1", "path": "Tables/dbo", "name": n,
              "target_path": "Tables/x"} for n in ("a", "b")]
    assert svc.shortcut_repoint_many(items, "ws-x", "lh-x") == {"done": ["a", "b"], "failed": [], "warnings": ["w"],
                                                                "problems": ["p"]}


def test_readers_are_checked_against_a_onelake_target_whose_roles_can_be_read(svc, monkeypatch):  # noqa: F811
    people = [{"principal": {"id": "u1", "displayName": "Ana", "type": "User"}, "role": "Contributor"}]
    monkeypatch.setattr(type(svc), "_sc_assignments", lambda self, ws, cache: people)
    out = svc.shortcut_readers({"host_workspace_id": "ws-dev", "lakehouse_id": "lh-1", "full_path": "Tables/dbo/customer",
                                "target_type": "OneLake", "workspace_id": "ws-prod", "item_id": "lh-gold",
                                "target_path": "Tables/dbo/customer"})
    assert out["mode"] == "identity" and out["target"] == 1 and out["gap"] == 0 and out["text"] == "Matches"


# ---- undo

def test_undo_of_a_change_with_nothing_to_go_back_to_is_refused(svc, monkeypatch):  # noqa: F811
    real = LocalPlatform.call

    def call(self, name, **kwargs):
        if name == "shortcut_changes":
            return [{"id": "c1", "kind": "repointed", "before": None, "after": None, "undoable": True,
                     "workspace_name": "", "lakehouse_name": ""}]
        return real(self, name, **kwargs)
    monkeypatch.setattr(LocalPlatform, "call", call)
    with pytest.raises(ValueError, match="Only an added, removed or repointed shortcut can be undone"):
        svc.shortcut_undo("c1")


def test_undo_of_an_add_looks_for_that_name_not_just_that_folder(svc):  # noqa: F811
    svc.create_shortcut("ws-dev", "lh-1", "Tables/dbo", "product", DEV2, "LH_Silver", "Sales Dev", "Sales Dev 2")
    added = svc.shortcut_changes()["changes"][0]
    svc.api.items["lh-1"] = [s for s in svc.api.items["lh-1"] if s["name"] != "product"]  # removed in Fabric
    with pytest.raises(RuntimeError, match="Tables/dbo/product is already gone"):
        svc.shortcut_undo(added["id"])


# ---- a request whose shortcut Fabric says is there already

@pytest.fixture
def conflicts(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "_sc_make_twins", lambda self, req, why: [])
    state = {"status": 409}
    real = svc.api.create_shortcut

    def create(ws, item, path, name, target):
        try:
            return real(ws, item, path, name, target)
        except RuntimeError as exc:
            if str(exc).startswith("409"):
                raise FabricError("A shortcut with that name already exists", code="ItemAlreadyExists",
                                  status=state["status"]) from exc
            raise
    svc.api.create_shortcut = create
    return state


def _ask(svc):  # noqa: F811
    return svc.shortcut_request("ws-dev", "lh-1", "Tables/dbo", "supplier", SRC, "Need suppliers", "LH_Silver", "Sales Dev")


def test_already_exists_is_read_from_the_code_whatever_the_status(svc, conflicts):  # noqa: F811
    conflicts["status"] = 400
    req = _ask(svc)
    svc.api.items["lh-1"].append({"name": "supplier", "path": "Tables/dbo", "target": WANTED})
    out = svc.platform_decide(req["id"], True)
    assert out["status"] == "approved" and out["next"] == [KEPT]


def test_one_pointing_elsewhere_is_named_by_its_lakehouse(svc, conflicts):  # noqa: F811
    req = _ask(svc)
    svc.api.items["lh-1"].append({"name": "supplier", "path": "Tables/dbo", "target": {}})
    with pytest.raises(core.PlatformError, match="^LH_Silver already has a shortcut Tables/dbo/supplier"):
        svc.platform_decide(req["id"], True)


def test_one_added_before_the_request_is_not_taken_for_the_requests_own(svc, conflicts, monkeypatch):  # noqa: F811
    # Made in the app and removed again before the request; then made again by hand in Fabric.
    svc.create_shortcut("ws-dev", "lh-1", "Tables/dbo", "supplier", SRC, "LH_Silver", "Sales Dev", "Sales Dev 2")
    svc.delete_shortcut("ws-dev", "lh-1", "Tables/dbo", "supplier", "LH_Silver", "Sales Dev")
    svc.api.items["lh-1"].append({"name": "supplier", "path": "Tables/dbo", "target": WANTED})
    monkeypatch.setattr(core, "_now", lambda: "2099-01-01T00:00:00+00:00")  # the request comes well after
    req = _ask(svc)
    out = svc.platform_decide(req["id"], True)
    assert out["status"] == "approved" and out["next"] == [KEPT]
    made = svc.platform_requests("approved")[0]["detail"]["made"]
    assert made == {"shortcut_id": "lh-1|Tables/dbo/supplier", "found": True}
    added = [c for c in svc.shortcut_changes()["changes"] if c["kind"] == "added" and c["name"] == "supplier"]
    assert len(added) == 2  # the app's earlier one, and this request's record of the one it found


def test_one_added_in_the_same_second_before_the_request_is_not_taken_for_the_requests_own(svc, conflicts, monkeypatch):  # noqa: F811
    # The request's time is kept to the second, the change's to the microsecond: as text, ".829233" sorts after "+"
    svc.create_shortcut("ws-dev", "lh-1", "Tables/dbo", "supplier", SRC, "LH_Silver", "Sales Dev", "Sales Dev 2")
    svc.delete_shortcut("ws-dev", "lh-1", "Tables/dbo", "supplier", "LH_Silver", "Sales Dev")
    svc.api.items["lh-1"].append({"name": "supplier", "path": "Tables/dbo", "target": WANTED})
    earlier = next(c["at"] for c in svc.shortcut_changes()["changes"] if c["kind"] == "added")
    assert "." in earlier
    monkeypatch.setattr(core, "_now", lambda: earlier.split(".")[0] + "+00:00")  # the same second, truncated
    req = _ask(svc)
    out = svc.platform_decide(req["id"], True)
    assert out["status"] == "approved" and out["next"] == [KEPT]
    made = svc.platform_requests("approved")[0]["detail"]["made"]
    assert made == {"shortcut_id": "lh-1|Tables/dbo/supplier", "found": True}
    added = [c for c in svc.shortcut_changes()["changes"] if c["kind"] == "added" and c["name"] == "supplier"]
    assert len(added) == 2


@pytest.mark.parametrize("requested_at, changes, ours", [
    # as text ".829233" sorts after "+": made and removed in the request's second, before it
    ("2026-11-14T16:01:58+00:00", [("16:01:58.100000", "added"), ("16:01:58.829233", "removed")], False),
    ("2026-11-14T16:01:58+00:00", [("16:01:57.999999", "added")], False),  # before the request
    ("2026-11-14T16:01:58+00:00", [("16:01:58.300000", "added")], True),  # this approval, the same second
    ("2026-11-14T16:01:58+00:00", [("16:01:57", "added"), ("16:01:57.5", "removed"), ("16:02:03", "added")], True),
    ("2026-11-14T16:01:58+00:00", [("16:02:03", "added"), ("16:02:09", "removed")], False),  # gone again since
    ("2026-11-14T16:01:58+00:00", [("16:02:03", "added"), ("16:02:09", "repointed")], True),
    ("", [("16:02:03", "added")], False),  # no request time: nothing counts
    ("2026-11-14T16:01:58+00:00", [("not a time", "added")], False),
])
def test_request_and_change_times_compare_as_times(requested_at, changes, ours):
    from fabric_desktop.shortcut_service import _added_since
    logged = [{"at": at if at == "not a time" else f"2026-11-14T{at}+00:00", "kind": kind} for at, kind in reversed(changes)]
    assert _added_since(logged, requested_at) is ours


def test_an_environments_copy_already_there_is_read_from_the_code_whatever_the_status(svc, monkeypatch):  # noqa: F811
    from test_shortcut_request_envs import LAKEHOUSES
    monkeypatch.setattr(type(svc), "_sc_lakehouses", lambda self: LAKEHOUSES)
    req = svc.shortcut_request("ws-dev", "lh-1", "Tables/dbo", "supplier", SRC, "", "LH_Silver", "Sales Dev", "Finance Dev")
    svc.api.items["lh-1p"] = [{"name": "supplier", "path": "Tables/dbo",
                               "target": {"oneLake": {"workspaceId": "ws-x", "itemId": "lh-x", "path": "Tables/other"}}}]
    real = svc.api.create_shortcut

    def create(ws, item, path, name, target):
        if item == "lh-1p":
            raise FabricError("A shortcut with that name already exists", code="ItemAlreadyExists", status=400)
        return real(ws, item, path, name, target)
    svc.api.create_shortcut = create
    out = svc.platform_decide(req["id"], True)
    assert out["status"] == "approved"
    assert ("Not made in Prod: Sales Prod / LH_Silver already has a shortcut Tables/dbo/supplier pointing somewhere else"
            in out["next"])
