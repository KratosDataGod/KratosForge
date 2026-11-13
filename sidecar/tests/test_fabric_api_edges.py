"""The Fabric REST client (fabric_api.py), the paths the main tests don't reach: a long lakehouse name,
a retry after a refused token whose connection then drops, notebooks that are gone or renamed, items,
semantic models and variable libraries made with a description and looked up by name, and the thin
calls (domains, roles, Git, shortcuts, connections) with the method, URL and body each sends. Against
a fake HTTP session: nothing reaches Fabric."""

from __future__ import annotations

import base64

import pytest
import requests

from fabric_desktop import fabric_api as F
from fabric_desktop.fabric_api import API, FabricClient, FabricError, WriteOutcomeUnknown

from test_fabric_api import FakeSession, Resp


def client(*responses, refresh=None):
    session = FakeSession(responses)
    return FabricClient(lambda: "tok", session=session, sleep=lambda s: None, refresh=refresh), session


def ok(body=None):
    return Resp(200, {} if body is None else body)


# ---- names


def test_a_lakehouse_name_over_256_characters_is_refused_by_its_length():
    assert F.lakehouse_name_problem("a" * 257) == "A lakehouse name can be at most 256 characters."


# ---- a refused token, then a dropped connection


class Dropping(FakeSession):
    """Answers 401 to the first request, then drops the connection."""

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("headers", {}).get("Authorization")))
        if len(self.calls) == 1:
            return Resp(401, {"errorCode": "TokenExpired"})
        raise requests.ConnectionError("reset")


def _dropping():
    session = Dropping([])
    return FabricClient(lambda: "old", session=session, sleep=lambda s: None, refresh=lambda: "new"), session


def test_a_read_sent_again_with_a_new_token_whose_connection_drops_says_so():
    c, s = _dropping()
    with pytest.raises(requests.ConnectionError) as exc:
        c.get_workspace("ws1")
    assert not isinstance(exc.value, WriteOutcomeUnknown)
    assert [a for _, _, a in s.calls][:2] == ["Bearer old", "Bearer new"]


def test_a_write_sent_again_with_a_new_token_whose_connection_drops_is_an_unknown_outcome():
    c, s = _dropping()
    with pytest.raises(WriteOutcomeUnknown) as exc:
        c.delete_item("ws1", "it1")
    assert exc.value.code == "WriteOutcomeUnknown" and "DELETE /workspaces/ws1/items/it1" in str(exc.value)
    assert len(s.calls) == 2


# ---- notebooks


def test_a_notebook_that_is_gone_or_in_another_workspace_is_none():
    c, _ = client(Resp(404, {"errorCode": "ItemNotFound"}), ok({"id": "nb", "workspaceId": "other"}),
                  ok({"id": "nb", "workspaceId": "ws1"}))
    assert c.get_notebook("ws1", "nb") is None
    assert c.get_notebook("ws1", "nb") is None
    assert c.get_notebook("ws1", "nb") == {"id": "nb", "workspaceId": "ws1"}


def test_a_notebook_read_fabric_refuses_for_another_reason_raises():
    c, _ = client(Resp(403, {"errorCode": "Forbidden"}))
    with pytest.raises(FabricError) as exc:
        c.get_notebook("ws1", "nb")
    assert exc.value.status == 403


def test_saving_a_notebook_under_a_new_name_renames_it_first():
    c, s = client(ok(), ok())
    assert c.update_notebook("ws1", {"id": "nb", "displayName": "old"}, "new", b"{}") == {"id": "nb",
                                                                                         "displayName": "new"}
    assert [(m, u[len(API):], b) for m, u, b in s.calls[:2]] == [
        ("PATCH", "/workspaces/ws1/notebooks/nb", {"displayName": "new"}),
        ("POST", "/workspaces/ws1/notebooks/nb/updateDefinition",
         {"definition": F._notebook_definition(b"{}")})]


# ---- items made and looked up


def _payload(text):
    return base64.b64encode(text.encode()).decode()


def test_an_item_made_without_an_id_back_is_looked_up_by_name_with_its_definition_and_description():
    c, s = client(ok(), ok({"value": [{"id": "db1", "type": "SQLDatabase", "displayName": "sales"}]}))
    got = c.create_item("ws1", "SQLDatabase", "sales", definition={"parts": []}, description="Sales data")
    assert got["id"] == "db1"
    assert s.calls[0][2] == {"displayName": "sales", "type": "SQLDatabase", "definition": {"parts": []},
                             "description": "Sales data"}


def test_saving_a_new_item_sends_its_parts_and_looks_it_up_when_no_id_comes_back():
    c, s = client(ok({"value": []}), ok(), ok({"value": [{"id": "f1", "type": "UserDataFunction",
                                                          "displayName": "fx"}]}))
    got = c.save_item("ws1", "UserDataFunction", "fx", {"a.py": "x"}, description="Functions")
    assert got == {"id": "f1", "type": "UserDataFunction", "displayName": "fx", "created": True}
    assert s.calls[1][2]["definition"]["parts"] == [{"path": "a.py", "payload": _payload("x"),
                                                     "payloadType": "InlineBase64"}]
    assert s.calls[1][2]["description"] == "Functions"


def test_saving_an_existing_item_with_parts_replaces_its_definition():
    c, s = client(ok({"value": [{"id": "f1", "type": "UserDataFunction", "displayName": "fx"}]}), ok())
    got = c.save_item("ws1", "UserDataFunction", "fx", {"a.py": "y"})
    assert got["created"] is False
    assert s.calls[1][:2] == ("POST", f"{API}/workspaces/ws1/items/f1/updateDefinition")


def test_a_new_semantic_model_is_made_with_its_description():
    c, s = client(ok({"value": []}), ok({"id": "sm1"}))
    assert c.save_semantic_model("ws1", "Sales", {"model.tmdl": "m"}, description="Sales model") == {
        "id": "sm1", "created": True}
    assert s.calls[1][2]["description"] == "Sales model"


def test_a_new_variable_library_without_an_id_back_is_looked_up_by_name():
    c, s = client(ok({"value": []}), ok(), ok({"value": [{"id": "vl1", "displayName": "vars"}]}))
    got = c.save_variable_library("ws1", "vars", {"variables.json": "{}"}, description="Settings")
    assert got == {"id": "vl1", "displayName": "vars", "created": True}
    assert s.calls[1][2]["description"] == "Settings"


def test_a_new_lakehouse_carries_its_description():
    c, s = client(ok({"id": "lh1"}))
    assert c.create_lakehouse("ws1", "Sales", description="Sales data")["id"] == "lh1"
    assert s.calls[0][2]["description"] == "Sales data"


def test_a_kql_queryset_is_made_with_its_description():
    c, s = client(ok({"id": "q1"}))
    assert c.create_kql_queryset("ws1", "q", {"parts": []}, description="Queries") == {"id": "q1"}
    assert s.calls[0][2] == {"displayName": "q", "definition": {"parts": []}, "description": "Queries"}


def test_a_domain_is_made_under_its_parent_with_its_description():
    c, s = client(ok({"id": "d2"}))
    assert c.create_admin_domain("Finance", "Money", parent_id="d1") == {"id": "d2"}
    assert s.calls[0] == ("POST", f"{API}/admin/domains", {"displayName": "Finance", "description": "Money",
                                                            "parentDomainId": "d1"})


def test_domains_come_from_either_list_key():
    c, _ = client(ok({"domains": [{"id": "d1"}]}), ok({"value": [{"id": "d2"}]}))
    assert c.list_admin_domains() == [{"id": "d1"}] and c.list_admin_domains() == [{"id": "d2"}]


def test_a_role_change_with_no_body_back_is_an_empty_answer():
    c, s = client(Resp(200, None))
    assert c.update_role_assignment("ws1", "ra1", "Viewer") == {}
    assert s.calls[0] == ("PATCH", f"{API}/workspaces/ws1/roleAssignments/ra1", {"role": "Viewer"})


# ---- the thin calls: one request each


LISTS = [
    (lambda c: c.list_tables("ws1", "lh1"), "/workspaces/ws1/lakehouses/lh1/tables", "data"),
    (lambda c: c.list_kql_databases("ws1"), "/workspaces/ws1/kqlDatabases", "value"),
    (lambda c: c.list_kql_querysets("ws1"), "/workspaces/ws1/kqlQuerysets", "value"),
    (lambda c: c.list_schedules("ws1", "it1", "Pipeline"), "/workspaces/ws1/items/it1/jobs/Pipeline/schedules",
     "value"),
    (lambda c: c.list_admin_domain_role_assignments("d1"), "/admin/domains/d1/roleAssignments", "value"),
    (lambda c: c.list_tenant_settings(), "/admin/tenantsettings", "value"),
    (lambda c: c.list_capacity_tenant_setting_overrides(), "/admin/capacities/delegatedTenantSettingOverrides",
     "value"),
    (lambda c: c.list_role_assignments("ws1"), "/workspaces/ws1/roleAssignments", "value"),
    (lambda c: c.list_data_access_roles("ws1", "lh1"), "/workspaces/ws1/items/lh1/dataAccessRoles", "value"),
    (lambda c: c.list_shortcuts("ws1", "lh1"), "/workspaces/ws1/items/lh1/shortcuts", "value"),
    (lambda c: c.list_connections(), "/connections", "value"),
    (lambda c: c.list_gateways(), "/gateways", "value"),
]


@pytest.mark.parametrize("call, path, key", LISTS)
def test_each_list_reads_its_one_url_and_key(call, path, key):
    c, s = client(ok({key: [{"id": "x"}]}))
    assert call(c) == [{"id": "x"}]
    assert s.calls == [("GET", f"{API}{path}", None)]


CALLS = [
    (lambda c: c.assign_to_capacity("ws1", "cap1"), "POST", "/workspaces/ws1/assignToCapacity",
     {"capacityId": "cap1"}),
    (lambda c: c.get_warehouse("ws1", "wh1"), "GET", "/workspaces/ws1/warehouses/wh1", None),
    (lambda c: c.get_kql_database("ws1", "k1"), "GET", "/workspaces/ws1/kqlDatabases/k1", None),
    (lambda c: c.update_item_definition("ws1", "it1", {"parts": []}), "POST",
     "/workspaces/ws1/items/it1/updateDefinition", {"definition": {"parts": []}}),
    (lambda c: c.delete_schedule("ws1", "it1", "Pipeline", "s1"), "DELETE",
     "/workspaces/ws1/items/it1/jobs/Pipeline/schedules/s1", None),
    (lambda c: c.git_connection("ws1"), "GET", "/workspaces/ws1/git/connection", None),
    (lambda c: c.git_status("ws1"), "GET", "/workspaces/ws1/git/status", None),
    (lambda c: c.admin_get_workspace("ws1"), "GET", "/admin/workspaces/ws1", None),
    (lambda c: c.add_role_assignment("ws1", "u1", "User", "Member"), "POST", "/workspaces/ws1/roleAssignments",
     {"principal": {"id": "u1", "type": "User"}, "role": "Member"}),
    (lambda c: c.delete_role_assignment("ws1", "ra1"), "DELETE", "/workspaces/ws1/roleAssignments/ra1", None),
]


@pytest.mark.parametrize("call, method, path, body", CALLS)
def test_each_call_sends_its_one_request(call, method, path, body):
    c, s = client(ok({"id": "x"}))
    call(c)
    assert s.calls == [(method, f"{API}{path}", body)]
