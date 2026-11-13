"""Estate service edges the main estate tests don't reach: who made a change when the sign-in has no
name, a refused or stale role write, shortcut and access reads that fail part way, and the variable
library when there's no project. Fakes stand in for Fabric, Power BI and Graph; nothing is sent."""

import threading
import time
import types

import pytest
from deltalake.exceptions import TableNotFoundError

from fabric_desktop import estate_service as es
from fabric_desktop.fabric_api import FabricClient, FabricError
from fabric_desktop.meta.store import OneLakeStore

WS = "11111111-2222-3333-4444-555555555555"
LH = "aaaaaaaa-2222-3333-4444-555555555555"
OID = "bbbbbbbb-2222-3333-4444-555555555555"


class Svc(es.EstateMixin):
    """The mixin on its own, with the service parts it calls given as plain attributes."""

    def __init__(self, project=None, fabric=None, auth=None, settings=None, root=None):
        self.project, self.fabric, self.auth = project, fabric, auth
        self.settings, self.root, self.saved = dict(settings or {}), root, []

    def _project(self):
        if isinstance(self.project, Exception):
            raise self.project
        return self.project

    def _settings(self):
        return self.settings

    def _save_settings(self, **kw):
        self.saved.append(kw)
        self.settings.update(kw)


def _answer(status, body=None, text=""):
    return types.SimpleNamespace(status_code=status, ok=status < 400, headers={}, text=text,
                                 json=lambda: body if body is not None else {})


# ---- who, scoped clients and role writes

def test_who_falls_back_to_the_os_user_when_the_sign_in_has_no_name(monkeypatch):
    def boom():
        raise RuntimeError("signed out")
    monkeypatch.setenv("USERNAME", "")
    monkeypatch.setenv("USER", "chris")
    assert es._who(types.SimpleNamespace(auth=types.SimpleNamespace(signed_in_user=boom))) == "chris"
    monkeypatch.delenv("USER")
    monkeypatch.delenv("USERNAME")
    assert es._who(types.SimpleNamespace(auth=types.SimpleNamespace(signed_in_user=boom))) == ""


def test_a_scoped_client_asks_for_its_own_scopes():
    asked = []
    svc = Svc(auth=types.SimpleNamespace(_token=lambda scopes: asked.append(scopes) or "tok"))
    client = svc._scoped(["scope/a"])
    assert isinstance(client, FabricClient)
    assert client._token() == "tok" and asked == [["scope/a"]]


def test_a_silent_client_is_none_when_signed_out_or_not_granted():
    def boom(scopes):
        raise RuntimeError("no client configured")
    assert Svc(auth=types.SimpleNamespace(silent_token=boom))._silent_client(["s"]) is None
    assert Svc(auth=types.SimpleNamespace(silent_token=lambda s: None))._silent_client(["s"]) is None
    assert Svc(auth=types.SimpleNamespace(silent_token=lambda s: "t"))._silent_client(["s"])._token() == "t"


@pytest.mark.parametrize("err", [FabricError("changed", code="PreconditionFailed"), FabricError("changed", status=412)])
def test_a_role_write_over_someone_elses_change_is_refused(err):
    def put(*a, **k):
        raise err
    with pytest.raises(PermissionError, match="Someone changed this lakehouse's roles"):
        es._put_roles(types.SimpleNamespace(put_data_access_roles=put), WS, LH, [], "etag-1")


def test_any_other_role_write_failure_is_raised_as_it_is():
    err = FabricError("busy", status=500)

    def put(*a, **k):
        raise err
    with pytest.raises(FabricError) as raised:
        es._put_roles(types.SimpleNamespace(put_data_access_roles=put), WS, LH, [], None)
    assert raised.value is err


# ---- table history

class _Entity:
    def __init__(self, name="silver.orders", layer="silver", files=None, mode="full"):
        self.name, self.layer, self.schema, self.table, self.mode = name, layer, "dbo", "orders", mode
        self.spec = {"source": {"files": files}} if files else {}


class _Project:
    def __init__(self, entities=(), environments=None, lakehouse="LH_Silver"):
        self.entities = {e.name: e for e in entities}
        self.environments = environments or {}
        self._lh = lakehouse
        self.version = 3

    def layer(self, name):
        return types.SimpleNamespace(lakehouse=self._lh, retention_days=7)

    def layer_names(self):
        return ["bronze", "silver", "gold"]


def test_table_history_of_a_table_never_written_and_unreadable_restores(monkeypatch):
    def no_table(*a):
        raise TableNotFoundError("no table")

    class Store:
        def uri(self, *parts):
            return "/".join(parts)

        def options(self):
            return {}

        def meta_where(self, *a):
            raise OSError("restores table unreadable")
    monkeypatch.setattr(es.restore_mod, "versions", no_table)
    monkeypatch.setattr(es, "impact", lambda project, name: [])
    svc = Svc(project=_Project([_Entity()]))
    svc._store = lambda project, env: Store()
    out = svc.meta_table_history("silver.orders")
    assert out["versions"] == [] and out["restores"] == [] and out["retention_days"] == 7
    assert out["incremental"] is False


# ---- cost

def test_capacity_health_shows_a_reading_even_when_it_cant_be_saved(tmp_path, monkeypatch):
    project = _Project(environments={"dev": {"workspace_id": WS}})
    svc = Svc(project=project, root=tmp_path, auth=types.SimpleNamespace(silent_token=lambda s: None))
    doc = {"observed_at": "now", "environments": [], "partial": False}
    monkeypatch.setattr(es.capacity_mod, "merge_cached", lambda fresh, cached, now: doc)

    def full(path, d):
        raise OSError("disk full")
    monkeypatch.setattr(es.capacity_mod, "save", full)
    assert svc.capacity_health(refresh=True) is doc


def test_capacities_say_when_the_capacity_scope_isnt_granted(tmp_path, monkeypatch):
    project = _Project(environments={"dev": {"workspace_id": WS}})
    tokens = {tuple(es.FABRIC_SCOPES): "fab"}
    svc = Svc(project=project, root=tmp_path,
              auth=types.SimpleNamespace(silent_token=lambda s: tokens.get(tuple(s))))
    seen = []

    def observe(envs, get_workspace, list_caps, now):
        with pytest.raises(PermissionError, match="Capacity.Read.All"):
            list_caps()
        seen.append(get_workspace)
        return {"observed_at": "now", "environments": [], "partial": True}
    monkeypatch.setattr(es.capacity_mod, "observe", observe)
    assert svc.capacity_health(refresh=True)["partial"] is True
    assert seen and seen[0] is not None


def test_cost_report_works_without_capacities_or_imported_usage(monkeypatch):
    svc = Svc(settings={})
    svc.meta_runs_all = lambda limit: {"runs": [], "unavailable": {"dev": "no store"}}
    svc.fabric_runs = lambda limit, refresh: []
    svc.history = lambda limit: []

    def no_caps(refresh=False):
        raise RuntimeError("signed out")

    def no_usage(*a):
        raise RuntimeError("usage export unreadable " + "x" * 400)
    svc.capacity_health = no_caps
    svc._usage_reconcile = no_usage
    out = svc.cost_report(days=7)
    assert out["capacities"] == {"observed_at": None, "environments": [], "partial": True}
    assert out["usage"]["error"].startswith("usage export unreadable") and len(out["usage"]["error"]) == 300
    assert out["unavailable"] == {"dev": "no store"}


# ---- shortcuts

def test_managed_tables_need_a_project_and_one_of_its_workspaces(monkeypatch):
    logged = []
    monkeypatch.setattr(es._log, "info", lambda *a, **k: logged.append(a))
    assert Svc(project=RuntimeError("no project"))._managed_in(WS, "LH_Silver") == []
    assert logged and "No metadata project" in logged[0][0]
    project = _Project([_Entity(files="Files/raw/*.csv"), _Entity("gold.x", layer="platinum")],
                       environments={"dev": {"workspace_id": WS}})
    assert Svc(project=project)._managed_in("other-ws", "LH_Silver") == []
    assert Svc(project=project)._managed_in(WS, "LH_Silver") == [
        {"name": "silver.orders", "files": "Files/raw/*.csv", "schema": "dbo", "table": "orders"}]


class _Api:
    def __init__(self, shortcuts=(), error=None):
        self.shortcuts, self.error, self.calls = list(shortcuts), error, []

    def list_shortcuts(self, ws, item):
        self.calls.append(("list", ws, item))
        if self.error:
            raise self.error
        return self.shortcuts

    def list_connections(self):
        return [{"id": "c2", "displayName": "b store", "connectionDetails": {"type": "AmazonS3", "path": "s3://b"}},
                {"id": "c1", "connectionDetails": {"type": "AzureDataLakeStorage", "path": "https://a"}},
                {"id": "c3", "connectionDetails": {"type": "SQL"}}]


_SC = {"name": "raw", "path": "/Files", "target": {"oneLake": {"workspaceId": WS, "itemId": LH, "path": "Files/raw"}}}


def test_list_shortcuts_and_connections_go_through_the_onelake_and_connection_scopes():
    api = _Api([_SC])
    svc = Svc(project=RuntimeError("no project"))
    asked = []
    svc._scoped = lambda scopes: asked.append(scopes) or api
    rows = svc.list_shortcuts(WS, LH, "LH_Silver")
    assert [r["full_path"] for r in rows] == ["Files/raw"] and rows[0]["used_by"] == []
    assert [c["id"] for c in svc.shortcut_connections()] == ["c2", "c1"]  # by name: "b store" before "c1"
    assert asked == [es.ONELAKE_SCOPES, es.CONNECTION_SCOPES]


def test_all_shortcuts_lists_the_rest_when_one_lakehouse_fails(monkeypatch):
    svc = Svc(project=RuntimeError("no project"), auth=types.SimpleNamespace(_token=lambda s: "tok"))

    def rows(api, ws, item, name):
        if item == "bad":
            raise RuntimeError("capacity paused")
        return [{"full_path": "Files/raw"}]
    svc._shortcut_rows = rows
    first = svc.all_shortcuts([{"workspace_id": WS, "lakehouse_id": "good", "name": "A"},
                               {"workspace_id": WS, "lakehouse_id": "bad"}])
    assert first == {"pending": True, "items": {}, "errors": {}}
    deadline = time.monotonic() + 10
    while svc.all_shortcuts([])["pending"] and time.monotonic() < deadline:
        time.sleep(0.01)
    done = svc.all_shortcuts([])
    assert done["items"] == {"good": [{"full_path": "Files/raw"}]}
    assert done["errors"] == {"bad": "capacity paused"}
    assert isinstance(svc.__dict__["_shortcuts_lock"], type(threading.Lock()))


def _entity_shortcuts_svc(files="Files/raw/*.csv", token="tok", store=None, lakehouse="LH_Silver"):
    project = _Project([_Entity(files=files)], environments={"dev": {"workspace_id": WS}}, lakehouse=lakehouse)
    svc = Svc(project=project, auth=types.SimpleNamespace(silent_token=lambda s: token))
    svc._store = store if callable(store) else (lambda project, env: store)
    return svc


def _onelake_store():
    s = OneLakeStore.__new__(OneLakeStore)
    s.workspace_id, s.lakehouse_ids = WS, {"LH_Silver": LH}
    return s


def test_entity_shortcuts_need_files_a_silent_sign_in_and_a_onelake_store(monkeypatch):
    assert _entity_shortcuts_svc(files=None).meta_entity_shortcuts("silver.orders") == []
    assert _entity_shortcuts_svc(token=None).meta_entity_shortcuts("silver.orders") == []

    def no_store(project, env):
        raise RuntimeError("no workspace for dev")
    logged = []
    monkeypatch.setattr(es._log, "info", lambda *a, **k: logged.append(a))
    assert _entity_shortcuts_svc(store=no_store).meta_entity_shortcuts("silver.orders") == []
    assert "No store" in logged[0][0]
    assert _entity_shortcuts_svc(store=object()).meta_entity_shortcuts("silver.orders") == []
    assert _entity_shortcuts_svc(store=_onelake_store(), lakehouse="LH_Other").meta_entity_shortcuts("silver.orders") == []


def test_entity_shortcuts_keep_only_the_ones_the_entity_reads(monkeypatch):
    svc = _entity_shortcuts_svc(store=_onelake_store())
    seen = []

    def rows(api, ws, item, name):
        seen.append((api._token(), ws, item, name))
        return [{"full_path": "Files/raw", "used_by": [{"entity": "silver.orders"}]},
                {"full_path": "Files/other", "used_by": [{"entity": "silver.x"}]}]
    svc._shortcut_rows = rows
    assert [r["full_path"] for r in svc.meta_entity_shortcuts("silver.orders")] == ["Files/raw"]
    assert seen == [("tok", WS, LH, "LH_Silver")]


# ---- access

def test_review_targets_without_a_project_take_the_extra_workspaces_once():
    svc = Svc(project=RuntimeError("no project"))
    targets = svc._review_targets([{"workspace_id": WS}, {"workspace_id": WS, "workspace_name": "again"},
                                   {"workspace_id": ""}, {"workspace_id": "w2", "workspace_name": "Two"}])
    assert [(t["key"], t["label"]) for t in targets] == [(WS, "Workspace"), ("w2", "Two")]


def test_access_review_marks_a_workspace_whose_roles_cant_be_listed():
    class Fabric:
        def list_role_assignments(self, ws):
            raise FabricError("Viewer can't list roles", status=403)
    svc = Svc(project=RuntimeError("no project"), fabric=Fabric())
    out = svc.access_review([{"workspace_id": WS, "workspace_name": "Sales"}])
    assert "Viewer can't list roles" in out["workspaces"][0]["error"]


class _Roles:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name, *a))


def test_set_role_changes_removes_and_adds_by_id():
    fabric = _Roles()
    svc = Svc(fabric=fabric)
    with pytest.raises(ValueError, match="role must be one of"):
        svc.access_set_role(WS, role="Owner")
    svc.access_set_role(WS, role="Member", assignment_id=OID)
    svc.access_set_role(WS, assignment_id=OID)
    svc.access_set_role(WS, role="Viewer", principal_id=OID, principal_type="")
    with pytest.raises(ValueError, match="Pick a person and a role"):
        svc.access_set_role(WS, role="Viewer")
    assert fabric.calls == [("update_role_assignment", WS, OID, "Member"), ("delete_role_assignment", WS, OID),
                            ("add_role_assignment", WS, OID, "User", "Viewer")]


def test_adding_by_email_needs_an_at_sign_and_names_a_missing_person(monkeypatch):
    svc = Svc(auth=types.SimpleNamespace(_token=lambda s: "tok"))
    with pytest.raises(ValueError, match="Type a person's email"):
        svc._add_user_by_email(WS, "not-an-email", "Viewer")
    monkeypatch.setattr(es.http_session, "session", lambda: types.SimpleNamespace(post=lambda *a, **k: _answer(404)))
    with pytest.raises(LookupError, match="No one with the email bo@contoso.com"):
        svc._add_user_by_email(WS, "bo@contoso.com", "Viewer")


def test_find_person_takes_an_object_id_as_a_group():
    assert Svc().access_find_person(f"  {OID} ") == {"id": OID, "name": OID, "email": "", "type": "Group"}


def _graph(monkeypatch, answers):
    """A fake Graph that gives ``answers`` in turn and keeps giving the last one (a retried 5xx)."""
    from fabric_desktop import retrying
    asked = []

    def get(url, params, headers, timeout):
        asked.append((url, params))
        return answers.pop(0) if len(answers) > 1 else answers[0]
    monkeypatch.setattr(es.http_session, "session", lambda: types.SimpleNamespace(get=get))
    monkeypatch.setattr(retrying.time, "sleep", lambda s: None)
    return asked


def test_find_person_goes_to_the_directory_when_roles_cant_be_listed(monkeypatch):
    class Fabric:
        def list_role_assignments(self, ws):
            raise FabricError("forbidden", status=403)
    svc = Svc(fabric=Fabric(), auth=types.SimpleNamespace(_token=lambda s: "tok"))
    asked = _graph(monkeypatch, [_answer(200, {"id": OID, "displayName": "Bo", "mail": "bo@contoso.com"})])
    assert svc.access_find_person("bo@contoso.com", workspace_id=WS) == {
        "id": OID, "name": "Bo", "email": "bo@contoso.com", "type": "User"}
    assert len(asked) == 1


def test_find_person_says_when_the_directory_has_no_one_by_that_mail(monkeypatch):
    svc = Svc(auth=types.SimpleNamespace(_token=lambda s: "tok"))
    _graph(monkeypatch, [_answer(404), _answer(200, {"value": []})])
    with pytest.raises(LookupError, match="No one with the email bo@contoso.com"):
        svc.access_find_person("bo@contoso.com")
    _graph(monkeypatch, [_answer(404), _answer(500)])
    with pytest.raises(LookupError, match="No one with the email"):
        svc.access_find_person("bo@contoso.com")


def test_find_person_reports_a_directory_failure_in_one_short_line(monkeypatch):
    svc = Svc(auth=types.SimpleNamespace(_token=lambda s: "tok"))
    _graph(monkeypatch, [_answer(503, text="Service\nUnavailable")])
    with pytest.raises(RuntimeError, match="Microsoft Graph answered 503") as raised:
        svc.access_find_person("bo@contoso.com")
    assert "\n" not in str(raised.value)


def test_lakehouse_paths_list_each_schemas_tables(monkeypatch):
    class Catalog:
        def __init__(self, token):
            self.token = token

        def list_schemas(self, ws, item):
            return ["dbo", "sales"]

        def list_tables(self, ws, item, schema):
            return {"dbo": ["a"], "sales": ["b", "c"]}[schema]
    monkeypatch.setattr(es, "TableCatalog", Catalog)
    svc = Svc(auth=types.SimpleNamespace(storage_token=lambda: "st"))
    assert svc._lakehouse_paths(WS, LH) == (["/Tables/dbo", "/Tables/sales"],
                                            ["/Tables/dbo/a", "/Tables/sales/b", "/Tables/sales/c"])


def test_lakehouse_access_still_shows_when_its_parts_cant_be_read(monkeypatch):
    warned = []
    monkeypatch.setattr(es._log, "warning", lambda *a, **k: warned.append(a[0]))

    class Fabric:
        def list_role_assignments(self, ws):
            raise FabricError("forbidden", status=403)

    class Api:
        def list_data_access_roles(self, ws, item):
            raise FabricError("OneLake security isn't on", status=400)
    svc = Svc(fabric=Fabric())
    svc._scoped = lambda scopes: Api()

    def no_paths(ws, item):
        raise RuntimeError("storage unreachable")
    svc._lakehouse_paths = no_paths
    out = svc.access_lakehouse(WS, LH)
    assert out["people"] == [] and out["schemas"] == [] and out["tables"] == []
    assert out["enabled"] is False and "OneLake security" in out["problem"]
    assert len(warned) == 2


def test_lakehouse_access_lists_people_roles_and_tables():
    class Fabric:
        def list_role_assignments(self, ws):
            return [{"role": "Admin", "principal": {"id": OID, "displayName": "Ana", "type": "User"}}]

    class Api:
        def list_data_access_roles(self, ws, item):
            return [{"name": "Readers", "members": {"microsoftEntraMembers": [{"objectId": OID}]}}]
    svc = Svc(fabric=Fabric())
    svc._scoped = lambda scopes: Api()
    svc._lakehouse_paths = lambda ws, item: (["/Tables/dbo"], ["/Tables/dbo/a"])
    out = svc.access_lakehouse(WS, LH)
    assert out["people"][0]["role"] == "Admin" and out["enabled"] is True
    assert out["roles"][0]["name"] == "Readers" and out["roles"][0]["entra"] == [OID]


class _RoleApi:
    def __init__(self, roles):
        self.roles, self.put = roles, []

    def list_data_access_roles_tagged(self, ws, item):
        return list(self.roles), "etag-1"

    def put_data_access_roles(self, ws, item, roles, etag=None):
        self.put.append(([r["name"] for r in roles], etag))


def test_saving_a_role_refuses_a_name_already_taken_and_keeps_the_others():
    api = _RoleApi([{"name": "DefaultReader", "members": {}}, {"name": "Sales", "members": {}}])
    svc = Svc(auth=types.SimpleNamespace(signed_in_tenant=lambda: None))
    svc._scoped = lambda scopes: api
    svc.access_lakehouse = lambda ws, item: {"ok": True}
    with pytest.raises(ValueError, match="already a role named Sales"):
        svc.access_save_role(WS, LH, "Sales", ["/Tables/dbo"], [OID])
    with pytest.raises(ValueError, match="already a role named Sales"):
        svc.access_save_role(WS, LH, "Sales", ["/Tables/dbo"], [OID], original_name="DefaultReader")
    assert svc.access_save_role(WS, LH, "Sales", ["/Tables/dbo"], [OID], original_name="Sales") == {"ok": True}
    assert svc.access_save_role(WS, LH, "Finance", ["/Tables/fin"], [OID]) == {"ok": True}
    assert api.put == [(["DefaultReader", "Sales"], "etag-1"), (["DefaultReader", "Sales", "Finance"], "etag-1")]


def test_deleting_a_role_writes_the_rest_with_the_etag_read():
    api = _RoleApi([{"name": "DefaultReader"}, {"name": "Sales"}])
    svc = Svc()
    svc._scoped = lambda scopes: api
    svc.access_lakehouse = lambda ws, item: {"ok": True}
    assert svc.access_delete_role(WS, LH, "Sales") == {"ok": True}
    assert api.put == [(["DefaultReader"], "etag-1")]


# ---- project tests and the variable library

def test_project_tests_with_an_unreadable_results_file_read_as_never_run(tmp_path, monkeypatch):
    project = types.SimpleNamespace(local_data=tmp_path / "local", root=tmp_path, version=2)
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / "test_results.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(es.testing_mod, "discover", lambda p: [tmp_path / "tests" / "test_a.py"])
    monkeypatch.setattr(es.testing_mod, "referenced", lambda p: {})
    out = Svc(project=project).meta_tests()
    assert out["tests"] == [] and out["at"] is None and out["stale"] is False
    assert out["files"] == ["tests/test_a.py"]


def test_publishing_the_variable_library_needs_a_known_environment():
    with pytest.raises(ValueError, match="environment must be one of"):
        Svc().meta_publish_varlib("staging")


def test_notebook_variables_without_a_project_or_attached_lakehouse_are_none(monkeypatch):
    from fabric_desktop import lakehouse
    logged = []
    monkeypatch.setattr(es._log, "info", lambda *a, **k: logged.append(a))
    monkeypatch.setattr(lakehouse, "_attached", None)
    assert Svc(project=RuntimeError("no project"))._notebook_variables(es.varlib_mod.LIBRARY) is None
    assert "No metadata project" in logged[0][0]
    assert Svc()._notebook_variables("VL_Other") is None


def test_a_missing_library_in_the_attached_workspace_is_named(monkeypatch):
    from fabric_desktop import lakehouse
    monkeypatch.setattr(lakehouse, "_attached", types.SimpleNamespace(workspace_id=WS, workspace_name="Sales"))
    svc = Svc(fabric=types.SimpleNamespace(read_variable_library=lambda ws, lib: None))
    with pytest.raises(LookupError, match="no variable library named VL_Other"):
        svc._notebook_variables("VL_Other")


@pytest.mark.parametrize("resp, expected", [
    (types.SimpleNamespace(json=lambda: (_ for _ in ()).throw(ValueError("not json"))), ""),
    (types.SimpleNamespace(json=lambda: None), ""),
    (types.SimpleNamespace(json=lambda: {"error": "plain text"}), ""),
    (types.SimpleNamespace(json=lambda: {"error": {}}), ""),
    (types.SimpleNamespace(json=lambda: {"error": {"message": "two\nlines"}}), ": two lines"),
])
def test_power_bi_error_text_only_from_a_well_formed_error(resp, expected):
    assert es._powerbi_error(resp) == expected


def test_who_can_read_is_marked_partial_when_the_roles_cant_be_read():
    class Fabric:
        def list_role_assignments(self, ws):
            return [{"role": "Viewer", "principal": {"id": OID, "displayName": "Ana", "type": "User"}}]

    class Api:
        def list_data_access_roles(self, ws, item):
            raise FabricError("throttled", status=429)
    svc = Svc(fabric=Fabric())
    svc._scoped = lambda scopes: Api()
    out = svc.access_who_can_read(WS, LH, "dbo.orders")
    assert out["partial"] is True and "couldn't be read" in out["problem"]


def test_publishing_the_variable_library_needs_a_workspace_for_the_environment():
    from fabric_desktop.errors import NotSetUp
    with pytest.raises(NotSetUp, match="Assign a workspace to Test first"):
        Svc(project=_Project(environments={"dev": {"workspace_id": WS}})).meta_publish_varlib("test")


def test_a_recent_capacity_reading_for_the_same_workspaces_is_used_as_it_is(tmp_path, monkeypatch):
    project = _Project(environments={"dev": {"workspace_id": WS}})
    cached = {"observed_at": "earlier", "environments": [{"env": "dev", "workspace_id": WS},
                                                         {"env": "test", "workspace_id": None},
                                                         {"env": "prod", "workspace_id": None}]}
    monkeypatch.setattr(es.capacity_mod, "load", lambda path: cached)
    monkeypatch.setattr(es.capacity_mod, "freshness", lambda e, now: {"stale": False, "age_s": 5})
    monkeypatch.setattr(es.capacity_mod, "observe", lambda *a: pytest.fail("not read again"))
    out = Svc(project=project, root=tmp_path).capacity_health()
    assert out["observed_at"] == "earlier" and all(e["age_s"] == 5 for e in out["environments"])
