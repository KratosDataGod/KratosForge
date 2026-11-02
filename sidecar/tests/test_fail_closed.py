"""QA-005: shortcut rules and access reviews fail closed when what they depend on can't be read."""

import pytest

from fabric_desktop import access
from fabric_desktop.fabric_api import FabricError

from test_shortcut_center import svc  # noqa: F401 - the fixture

SRC = {"type": "oneLake", "workspace_id": "ws-dev2", "item_id": "lh-gold-dev", "path": "Tables/dbo/customer"}
ASSIGNMENTS = [{"principal": {"id": "u1", "displayName": "Ana", "type": "User"}, "role": "Contributor"},
               {"principal": {"id": "u2", "displayName": "Ben", "type": "User"}, "role": "Viewer"}]
BEN_READS = [{"name": "Readers", "decisionRules": [{"effect": "Permit", "permission": [
    {"attributeName": "Path", "attributeValueIncludedIn": ["*"]},
    {"attributeName": "Action", "attributeValueIncludedIn": ["Read"]}]}],
    "members": {"microsoftEntraMembers": [{"objectId": "u2", "tenantId": "t"}]}}]


class Broken:
    def call(self, op, **kwargs):
        raise ConnectionError("the platform didn't answer")


def test_shortcut_rules_that_cant_be_read_stop_creating_and_approving(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "_catalog", lambda self: Broken())
    out = svc.shortcut_preflight("ws-dev", SRC, "Sales Dev", "Sales Dev 2")
    assert out["unchecked"] and "couldn't be read" in out["blocked"][0]
    for via_request in (False, True):
        with pytest.raises(PermissionError, match="couldn't be read"):
            svc._create_shortcut("ws-dev", "lh-1", "Tables/dbo", "c2", SRC, "LH_Silver", "Sales Dev", "Sales Dev 2",
                                 via_request=via_request)
    assert not any(s["name"] == "c2" for s in svc.api.items["lh-1"])


def test_only_onelake_security_being_off_means_no_roles():
    off = FabricError("400", "OneLakeSecurityNotEnabled", 400)
    def fail(exc):
        def call():
            raise exc
        return call
    assert access.read_roles(fail(off)) == ([], None)
    roles, problem = access.read_roles(fail(FabricError("403 Forbidden", "InsufficientPrivileges", 403)))
    assert roles == [] and "couldn't be read" in problem
    assert access.read_roles(lambda: BEN_READS) == (BEN_READS, None)


class Fabric:
    def list_role_assignments(self, ws):
        return ASSIGNMENTS


def test_who_can_read_says_when_it_is_incomplete(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "fabric", property(lambda self: Fabric()), raising=False)
    monkeypatch.setattr(svc.api, "list_data_access_roles", lambda ws, item: BEN_READS)
    out = svc.access_who_can_read("ws-dev", "lh-1", "/Tables/dbo/customer")
    assert not out["partial"] and {r["id"] for r in out["readers"]} == {"u1", "u2"}

    def denied(ws, item):
        raise FabricError("403 Forbidden", "InsufficientPrivileges", 403)
    monkeypatch.setattr(svc.api, "list_data_access_roles", denied)
    out = svc.access_who_can_read("ws-dev", "lh-1", "/Tables/dbo/customer")
    assert out["partial"] and "403" in out["problem"] and [r["id"] for r in out["readers"]] == ["u1"]


def test_shortcut_readers_say_when_they_are_incomplete(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "fabric", property(lambda self: Fabric()), raising=False)

    def denied(ws, item):
        raise FabricError("429 Too many requests", "TooManyRequests", 429)
    monkeypatch.setattr(svc.api, "list_data_access_roles", denied)
    s = {"host_workspace_id": "ws-dev", "lakehouse_id": "lh-1", "full_path": "Tables/dbo/customer",
         "target_type": "ADLS", "exposure": "connection"}
    monkeypatch.setattr(type(svc), "_sc_assignments", lambda self, ws, cache: ASSIGNMENTS)
    out = svc._sc_readers(s, svc.api, {})
    assert out["partial"] and out["text"].startswith("Not complete:") and "429" in out["problem"]


def test_access_roles_arent_saved_over_someone_elses_change(svc, monkeypatch):  # noqa: F811
    store = {"roles": list(BEN_READS), "etag": '"v1"'}
    puts = []

    def put(ws, item, roles, etag=None):
        if etag != store["etag"]:
            raise FabricError("412", "PreconditionFailed", 412)
        puts.append(roles)
    monkeypatch.setattr(svc.api, "list_data_access_roles_tagged", lambda ws, item: (list(store["roles"]), '"v1"'),
                        raising=False)
    monkeypatch.setattr(svc.api, "put_data_access_roles", put, raising=False)
    monkeypatch.setattr(type(svc), "access_lakehouse", lambda self, ws, item: {}, raising=False)
    monkeypatch.setattr(svc.auth, "signed_in_tenant", lambda: "t", raising=False)
    svc.access_delete_role("ws-dev", "lh-1", "Readers")
    assert puts == [[]]
    store["etag"] = '"v2"'  # someone saved a change after this read
    with pytest.raises(PermissionError, match="changed this lakehouse's roles"):
        svc.access_delete_role("ws-dev", "lh-1", "Readers")
    with pytest.raises(PermissionError, match="changed this lakehouse's roles"):
        svc.access_save_role("ws-dev", "lh-1", "Readers", ["*"], ["u3"], original_name="Readers")
    assert len(puts) == 1
