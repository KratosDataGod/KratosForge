"""Restore and retention, cost, shortcuts, access review, tests that gate promote, VL_Sources."""

import datetime as dt
import json
import types

import polars as pl
import pytest
import requests
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import access, costs, maintenance, shortcuts
from fabric_desktop.meta import GateError, restore, varlib
from fabric_desktop.meta.project import Layer
from fabric_desktop.notebookutils_local import Host, build
from fabric_desktop.service import Service
from test_fabric_api import Resp, client


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")
    assert [r["status"] for r in s.meta_run()] == ["ok"] * 6
    return s


# ---- restore and retention

def test_restore_a_bad_load_and_move_the_watermark_back(svc):
    project = svc._project()
    store = svc._store(project, "dev")
    good = store.read("LH_Silver", "dbo", "customers")
    hist = svc.meta_table_history("silver.customers")
    assert hist["retention_days"] == 7 and hist["incremental"] and hist["versions"][0]["current"]
    good_version = hist["versions"][0]["version"]

    # A bad load: one row's watermark jumps far ahead and the rest disappear.
    bad = good.head(1).with_columns(pl.lit("2099-01-01").alias("updated_at")) if good.schema["updated_at"] == pl.String \
        else good.head(1).with_columns(pl.col("updated_at").max() + pl.duration(days=30000))
    store.write("LH_Silver", "dbo", "customers", bad, mode="overwrite")
    store.meta_append("watermarks", [{"entity": "silver.customers", "value": "2099-01-01", "run_id": "bad"}])

    versions = svc.meta_table_history("silver.customers")["versions"]
    target = next(v for v in versions if v["version"] == good_version)
    assert target["restorable"] and not target["current"]

    out = svc.meta_restore("silver.customers", "dev", good_version, reason="bad export")
    assert out["to_version"] == good_version and out["new_version"] == good_version + 2
    assert "gold.dim_customer" in out["downstream"]
    assert store.read("LH_Silver", "dbo", "customers").height == good.height
    wm = store.meta("watermarks").filter(pl.col("entity") == "silver.customers")["value"][-1]
    assert wm == out["watermark"] and not wm.startswith("2099")
    again = svc.meta_table_history("silver.customers")
    assert again["restores"][0]["reason"] == "bad export" and again["restores"][0]["to_version"] == good_version
    assert again["versions"][0]["restored_to"] == str(good_version)
    with pytest.raises(ValueError):
        svc.meta_restore("silver.customers", "dev", again["versions"][0]["version"])


def test_versions_vacuumed_away_cannot_be_restored(tmp_path):
    uri = str(tmp_path / "t")
    for i in range(3):
        write_deltalake(uri, pl.DataFrame({"a": [i]}).to_arrow(), mode="overwrite")
    table = DeltaTable(uri)
    table.vacuum(retention_hours=0, dry_run=False, enforce_retention_duration=False)
    rows = {v["version"]: v for v in restore.versions(uri, None, 7)}
    assert rows[4]["current"] and not rows[4]["restorable"]  # 3 and 4 are VACUUM's own commits
    assert rows[2]["restorable"]  # the files it reads are still there
    assert not rows[0]["restorable"] and "VACUUM" in rows[0]["why_not"]


def test_layer_retention_is_saved_checked_and_used_by_maintenance(svc, tmp_path):
    layers = [l.to_dict() for l in svc._project().layers]
    layers[1]["retention_days"] = 30
    svc.meta_set_layers(layers)
    project = svc._project()
    assert project.layer("silver").retention_days == 30 and project.layer("bronze").retention_days == 7
    assert "retention_days = 30" in (project.root / "layers.toml").read_text()
    layers[1]["retention_days"] = 3
    with pytest.raises(GateError, match="at least 7"):
        svc.meta_set_layers(layers)

    svc.meta_optimize("dev", ["silver.customers"])
    uri = svc._store(project, "dev").uri("LH_Silver", "dbo", "customers")
    config = DeltaTable(uri).metadata().configuration
    assert config["delta.deletedFileRetentionDuration"] == "interval 30 days"
    assert config["delta.logRetentionDuration"] == "interval 30 days"
    assert maintenance.fabric_retention(30) == "30.01:00:00"
    assert not maintenance.set_retention(uri, 30)  # already set: no new commit


# ---- cost

def test_cost_estimates_fabric_runs_and_the_local_scenario():
    now = dt.datetime(2026, 10, 15, 12, tzinfo=dt.timezone.utc)
    cfg = costs.settings({"price_per_cu_hour": 0.2})
    meta = [{"entity": "silver.orders", "env": "dev", "compute": "local", "seconds": 3600, "started_at": "2026-10-14T10:00:00+00:00"},
            {"entity": "silver.orders", "env": "prod", "compute": "fabric", "seconds": 1800, "started_at": "2026-10-14T11:00:00+00:00"},
            {"entity": "old", "env": "prod", "compute": "fabric", "seconds": 1800, "started_at": "2026-08-01T11:00:00+00:00"}]
    fabric = [{"kind": "Table maintenance", "table": "dbo.orders", "env": "prod", "started_at": "2026-10-14T12:00:00Z",
               "finished_at": "2026-10-14T12:15:00Z"},
              {"kind": "Notebook run", "name": "nb", "engine": "python", "started_at": "2026-10-14T13:00:00Z",
               "finished_at": "2026-10-14T13:30:00Z"},
              {"kind": "Metadata run", "name": "runner", "started_at": "2026-10-14T11:00:00Z", "finished_at": "2026-10-14T11:40:00Z"},
              {"kind": "Notebook run", "name": "still running", "started_at": "2026-10-14T13:00:00Z", "finished_at": None}]
    rows = costs.activities(meta, fabric, [], cfg)
    out = costs.report(rows, cfg, 30, now, {"prod": {"name": "Cap", "sku": "F2"}})
    t = out["total"]
    # Fabric: 0.5 h x 1 CU + 0.25 h x 8 CU + 0.5 h x 1 CU = 3 CU hours; the runner job isn't counted twice.
    assert t["cu_hours"] == 3.0 and t["cost"] == 0.6
    assert t["scenario_cu_hours"] == 1.0 and t["scenario_saved"] == 0.2 and t["runs"] == 5
    # The run that is still going is counted, with its capacity use unknown rather than 0.
    assert t["unknown_runs"] == 1 and not t["evidence"]["fabric_job_cu"]["complete"]
    prod = next(e for e in out["by_env"] if e["env"] == "prod")
    assert prod["sku"] == "F2" and prod["capacity_share"] == pytest.approx(2.5 / (2 * 30 * 24) * 100, abs=1e-3)
    assert out["by_item"][0]["name"] == "dbo.orders"
    assert costs.sku_cu("f64") == 64


def test_cost_report_through_the_service(svc):
    svc.set_cost_settings(price_per_cu_hour=0.5, currency="eur")
    out = svc.cost_report(7)
    assert out["settings"]["price_per_cu_hour"] == 0.5 and out["settings"]["currency"] == "EUR"
    assert out["total"]["local_runs"] == 6 and out["total"]["cost"] == 0
    with pytest.raises(ValueError):
        svc.set_cost_settings(spark_cu=0)


# ---- shortcuts

def test_shortcut_targets_and_descriptions():
    one = shortcuts.target({"type": "oneLake", "workspace_id": "w", "item_id": "i", "path": "/Tables/dbo/orders/"})
    assert one == {"oneLake": {"workspaceId": "w", "itemId": "i", "path": "Tables/dbo/orders"}}
    adls = shortcuts.target({"type": "adlsGen2", "location": "https://acct.dfs.core.windows.net/", "subpath": "raw/x",
                             "connection_id": "c1"})
    assert adls["adlsGen2"] == {"location": "https://acct.dfs.core.windows.net", "subpath": "/raw/x", "connectionId": "c1"}
    with pytest.raises(ValueError):
        shortcuts.target({"type": "adlsGen2", "location": "acct", "connection_id": "c1"})
    assert shortcuts.location("Tables") == "Tables/dbo" and shortcuts.location("Files/landing") == "Files/landing"
    with pytest.raises(ValueError):
        shortcuts.location("Tables/dbo/x")
    d = shortcuts.describe({"name": "orders", "path": "Files/landing",
                            "target": {"type": "AdlsGen2", "adlsGen2": {"location": "https://a", "subpath": "/raw", "connectionId": "c"}}})
    assert d["kind"] == "folder" and d["target_type"] == "ADLS Gen2" and d["target"] == "https://a/raw"
    managed = [{"name": "bronze.orders", "files": "landing/orders/*.csv", "schema": "dbo", "table": "orders"}]
    assert shortcuts.used_by(d, managed)[0]["entity"] == "bronze.orders"
    clash = shortcuts.describe({"name": "orders", "path": "Tables/dbo", "target": {"oneLake": {"path": "Tables/dbo/o"}}})
    assert "same name" in shortcuts.used_by(clash, managed)[0]["how"]


def test_shortcut_rest_calls():
    c, s = client([Resp(201, {"name": "x"}), Resp(200, {})])
    c.create_shortcut("ws", "lh", "Tables/dbo", "x", {"oneLake": {"workspaceId": "w", "itemId": "i", "path": "Tables/dbo/x"}})
    assert s.calls[0][0] == "POST" and s.calls[0][1].endswith("/workspaces/ws/items/lh/shortcuts")
    assert s.calls[0][2] == {"path": "Tables/dbo", "name": "x", "target": {"oneLake": {"workspaceId": "w", "itemId": "i", "path": "Tables/dbo/x"}}}
    c.delete_shortcut("ws", "lh", "Files/landing/", "orders")
    assert s.calls[1][:2] == ("DELETE", "https://api.fabric.microsoft.com/v1/workspaces/ws/items/lh/shortcuts/Files/landing/orders")


# ---- access

def _assignment(pid, name, role, kind="User", upn=None):
    p = {"id": pid, "displayName": name, "type": kind}
    if kind == "User":
        p["userDetails"] = {"userPrincipalName": upn or f"{name.lower()}@contoso.com"}
    return {"id": f"a-{pid}", "principal": p, "role": role}


def test_access_review_flags_what_needs_a_second_look():
    out = access.review([
        {"key": "dev", "label": "Dev", "env": "dev", "workspace_id": "d",
         "assignments": [_assignment("1", "Chris", "Admin"), _assignment("3", "Eng", "Member", "Group")]},
        {"key": "prod", "label": "Prod", "env": "prod", "workspace_id": "p",
         "assignments": [_assignment("1", "Chris", "Admin"), _assignment("2", "Pat", "Contributor"),
                         _assignment("4", "Guest", "Viewer", upn="g_x.com#EXT#@contoso.com"),
                         _assignment("3", "Eng", "Member", "Group")]},
        {"key": "test", "label": "Test", "env": "test", "workspace_id": "t", "error": "403"}])
    rows = {r["name"]: r for r in out["people"]}
    assert rows["Chris"]["roles"] == {"dev": {"role": "Admin", "assignment_id": "a-1"}, "prod": {"role": "Admin", "assignment_id": "a-1"}}
    assert any("Can change Prod directly" in f for f in rows["Chris"]["flags"])
    assert "Can change Prod but has no access to Dev" in rows["Pat"]["flags"]
    assert rows["Guest"]["flags"] == ["Guest account from another organization"]
    assert rows["Eng"]["flags"] == [] and out["flagged"] == 3
    assert out["workspaces"][2]["error"] == "403"


def test_data_access_roles_and_who_can_read():
    role = access.make_role("SalesReaders", ["Tables/dbo"], ["oid-9"], "tenant")
    assert role["decisionRules"][0]["permission"][0]["attributeValueIncludedIn"] == ["/Tables/dbo"]
    assert role["members"]["microsoftEntraMembers"] == [{"tenantId": "tenant", "objectId": "oid-9"}]
    default = {"name": "DefaultReader", "decisionRules": [{"effect": "Permit", "permission": [
        {"attributeName": "Path", "attributeValueIncludedIn": ["*"]}, {"attributeName": "Action", "attributeValueIncludedIn": ["Read"]}]}],
        "members": {"fabricItemMembers": [{"itemAccess": ["ReadAll"], "sourcePath": "w/i"}]}}
    kept = access.make_role("DefaultReader", ["*"], [], "tenant", keep=default)
    assert kept["members"]["fabricItemMembers"] == default["members"]["fabricItemMembers"]
    with pytest.raises(ValueError):
        access.make_role("bad name", ["*"], ["x"], "t")
    with pytest.raises(ValueError):
        access.make_role("Empty", ["*"], [], "t")
    who = access.who_can_read("/Tables/dbo/orders", [_assignment("1", "Chris", "Admin"), _assignment("9", "Ann", "Viewer")],
                              [role, access.make_role("Other", ["/Tables/sales"], ["oid-8"], "t")])
    assert [(w["name"], w["how"]) for w in who] == [("oid-9", "Data access role SalesReaders"), ("Chris", "Workspace Admin")]


def test_access_review_uses_the_environment_workspaces(svc, monkeypatch):
    svc.meta_set_environment("test", "ws-test", "Sales Test")
    svc.meta_set_keys_workspace("test", "ws-keys", "Keys Test")

    class Fake:
        def list_role_assignments(self, ws):
            return [_assignment("1", "Chris", "Admin")]
    monkeypatch.setattr(type(svc), "fabric", property(lambda self: Fake()))
    out = svc.access_review()
    assert [w["key"] for w in out["workspaces"]] == ["test", "test-keys"]
    assert out["people"][0]["roles"]["test-keys"]["role"] == "Admin"


def test_access_by_email_needs_no_directory_permission(svc, monkeypatch):
    from fabric_desktop import estate_service

    class Auth:
        asked = []

        def _token(self, scopes):
            self.asked.append(scopes)
            if "graph.microsoft.com" in scopes[0]:
                raise RuntimeError("AADSTS65001: consent_required")
            return "tok"

    class Fake:
        def list_role_assignments(self, ws):
            return [{"id": "a1", "role": "Viewer", "principal": {"id": "oid-ana", "displayName": "Ana", "type": "User",
                                                                  "userDetails": {"userPrincipalName": "ana@contoso.com"}}}]
    posted = []
    http = types.SimpleNamespace(
        post=lambda url, json, headers, timeout: posted.append((url, json)) or types.SimpleNamespace(status_code=200, ok=True))
    monkeypatch.setattr(estate_service.http_session, "session", lambda: http)
    monkeypatch.setattr(type(svc), "fabric", property(lambda self: Fake()))
    svc._auth = Auth()

    svc.access_set_role(WS_ID, role="Contributor", email="bo@contoso.com")
    assert posted == [(f"https://api.powerbi.com/v1.0/myorg/groups/{WS_ID}/users",
                       {"emailAddress": "bo@contoso.com", "groupUserAccessRight": "Contributor", "principalType": "User"})]
    assert svc.access_find_person("ANA@contoso.com", workspace_id=WS_ID)["id"] == "oid-ana"
    with pytest.raises(LookupError, match="object ID"):
        svc.access_find_person("bo@contoso.com", workspace_id=WS_ID)


WS_ID = "11111111-2222-3333-4444-555555555555"


@pytest.mark.parametrize("bad", ["ws1", "../admin/groups", f"{WS_ID}/users?x=1", f"{WS_ID}\n", f" {WS_ID}", "", None])
def test_a_workspace_id_that_isnt_a_guid_never_reaches_a_url(svc, monkeypatch, bad):
    from fabric_desktop import estate_service
    monkeypatch.setattr(estate_service.http_session, "session", lambda: pytest.fail("nothing is sent"))
    monkeypatch.setattr(type(svc), "fabric", property(lambda self: pytest.fail("Fabric isn't called")))
    with pytest.raises(ValueError, match="workspace id"):
        svc.access_set_role(bad, role="Viewer", email="bo@contoso.com")
    with pytest.raises(ValueError, match="workspace id"):
        svc._add_user_by_email(bad, "bo@contoso.com", "Viewer")


@pytest.mark.parametrize("bad", ["bo", "bo@contoso", "bo@contoso.com/memberOf", "../me@contoso.com", "bo@contoso.com?$x=1",
                                 "bo smith@contoso.com", "bo@contoso.com#x", "bo@@contoso.com", "bo@con\ntoso.com"])
def test_find_person_takes_only_an_email_or_an_object_id(svc, monkeypatch, bad):
    from fabric_desktop import estate_service
    monkeypatch.setattr(estate_service.http_session, "session", lambda: pytest.fail("nothing is sent"))
    monkeypatch.setattr(type(svc), "fabric", property(lambda self: pytest.fail("Fabric isn't called")))
    with pytest.raises(ValueError, match="email"):
        svc.access_find_person(bad, workspace_id=WS_ID)


@pytest.mark.parametrize("bad", ["ws1", "../admin", f"{WS_ID}/users", f"{WS_ID}\n"])
def test_find_person_checks_the_workspace_id(svc, monkeypatch, bad):
    monkeypatch.setattr(type(svc), "fabric", property(lambda self: pytest.fail("Fabric isn't called")))
    with pytest.raises(ValueError, match="workspace id"):
        svc.access_find_person("bo@contoso.com", workspace_id=bad)


def test_find_person_escapes_the_email_in_the_directory_url(svc, monkeypatch):
    from fabric_desktop import estate_service
    asked = []

    class Auth:
        def _token(self, scopes):
            return "tok"

    def get(url, params, headers, timeout):
        asked.append(url)
        return types.SimpleNamespace(status_code=200, ok=True, json=lambda: {
            "id": "oid-o", "displayName": "O'Brien", "userPrincipalName": "o'brien+kf@contoso.com", "mail": ""})
    monkeypatch.setattr(estate_service.http_session, "session", lambda: types.SimpleNamespace(get=get))
    svc._auth = Auth()
    assert svc.access_find_person("o'brien+kf@contoso.com")["id"] == "oid-o"
    assert asked == ["https://graph.microsoft.com/v1.0/users/o%27brien%2Bkf@contoso.com"]


def test_find_person_keeps_an_apostrophe_in_the_directory_filter(svc, monkeypatch):
    from fabric_desktop import estate_service
    asked = []

    class Auth:
        def _token(self, scopes):
            return "tok"

    def get(url, params, headers, timeout):
        asked.append((url, params.get("$filter")))
        if len(asked) == 1:
            return types.SimpleNamespace(status_code=404, ok=False, json=lambda: {})
        return types.SimpleNamespace(status_code=200, ok=True, json=lambda: {"value": [
            {"id": "oid-o", "displayName": "O'Brien", "userPrincipalName": "", "mail": "o'brien@contoso.com"}]})
    monkeypatch.setattr(estate_service.http_session, "session", lambda: types.SimpleNamespace(get=get))
    svc._auth = Auth()
    assert svc.access_find_person("o'brien@contoso.com")["id"] == "oid-o"
    assert asked[1][1] == "mail eq 'o''brien@contoso.com'"


@pytest.mark.parametrize("found", [{"value": [{"id": "oid-x", "mail": "obrien@contoso.com", "userPrincipalName": "obrien@contoso.com"}]},
                                   {"id": "oid-x", "mail": "", "userPrincipalName": "someone.else@contoso.com"}])
def test_find_person_refuses_a_directory_answer_for_another_email(svc, monkeypatch, found):
    from fabric_desktop import estate_service
    direct = "value" not in found

    class Auth:
        def _token(self, scopes):
            return "tok"

    def get(url, params, headers, timeout):
        if url.endswith("/users") or direct:
            return types.SimpleNamespace(status_code=200, ok=True, json=lambda: found)
        return types.SimpleNamespace(status_code=404, ok=False, json=lambda: {})
    monkeypatch.setattr(estate_service.http_session, "session", lambda: types.SimpleNamespace(get=get))
    svc._auth = Auth()
    with pytest.raises(LookupError, match="No one with the email"):
        svc.access_find_person("o'brien@contoso.com")


def test_find_person_takes_a_guest_sign_in_name(svc, monkeypatch):
    from fabric_desktop import estate_service
    asked = []

    class Auth:
        def _token(self, scopes):
            return "tok"

    def get(url, params, headers, timeout):
        asked.append(url)
        return types.SimpleNamespace(status_code=200, ok=True, json=lambda: {"id": "oid-g", "displayName": "Guest",
                                                                         "userPrincipalName": "X_contoso.com#EXT#@tenant.onmicrosoft.com"})
    monkeypatch.setattr(estate_service.http_session, "session", lambda: types.SimpleNamespace(get=get))
    svc._auth = Auth()
    assert svc.access_find_person("x_contoso.com#EXT#@tenant.onmicrosoft.com")["id"] == "oid-g"
    assert asked == ["https://graph.microsoft.com/v1.0/users/x_contoso.com%23EXT%23@tenant.onmicrosoft.com"]


@pytest.mark.parametrize("field", ["assignment_id", "principal_id"])
def test_assignment_and_principal_ids_are_guids_too(svc, monkeypatch, field):
    monkeypatch.setattr(type(svc), "fabric", property(lambda self: pytest.fail("Fabric isn't called")))
    with pytest.raises(ValueError, match="id KratosForge can use"):
        svc.access_set_role(WS_ID, role="Viewer", **{field: "x/../y"})


def _adding(svc, monkeypatch, answers):
    """Access > add by email against a fake Power BI that gives ``answers`` in turn (a response or an error)."""
    from fabric_desktop import estate_service, retrying
    sent = []

    def post(url, json, headers, timeout):
        sent.append(url)
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(estate_service.http_session, "session", lambda: types.SimpleNamespace(post=post))
    monkeypatch.setattr(retrying.time, "sleep", lambda s: None)
    svc._auth = types.SimpleNamespace(_token=lambda scopes: "tok")
    return sent


def _answer(status, body=None, headers=None):
    return types.SimpleNamespace(status_code=status, ok=status < 400, headers=headers or {}, json=lambda: body or {})


def test_adding_by_email_waits_out_throttling(svc, monkeypatch):
    sent = _adding(svc, monkeypatch, [_answer(429, headers={"Retry-After": "1"}), _answer(200)])
    svc._add_user_by_email(WS_ID, "bo@contoso.com", "Viewer")
    assert len(sent) == 2


@pytest.mark.parametrize("first", [_answer(503), requests.Timeout("read timed out"), requests.ConnectionError("reset")])
def test_adding_by_email_is_not_sent_twice_when_it_may_have_happened(svc, monkeypatch, first):
    sent = _adding(svc, monkeypatch, [first, _answer(200)])
    with pytest.raises(RuntimeError, match="may or may not have added bo@contoso.com.*access list"):
        svc._add_user_by_email(WS_ID, "bo@contoso.com", "Viewer")
    assert len(sent) == 1


def test_a_refused_add_gives_fabrics_code_not_the_raw_body(svc, monkeypatch):
    body = {"error": {"code": "InvalidRequest", "message": "The role is not valid"}}
    _adding(svc, monkeypatch, [_answer(400, body)])
    with pytest.raises(RuntimeError) as err:
        svc._add_user_by_email(WS_ID, "bo@contoso.com", "Boss")
    assert str(err.value) == "Fabric didn't add bo@contoso.com (400: InvalidRequest: The role is not valid)."


# ---- tests that gate promote

def test_failing_project_test_blocks_promote(svc):
    root = svc._project().root
    result = svc.meta_tests(run=True)
    assert result["passed"] == 1 and result["failed"] == 0 and result["tests"][0]["id"] == "tests/test_common.py::test_round_money"
    svc.meta_set_environment("test", target="local")
    assert svc.meta_promote_check("dev", "test")["errors"] == []

    (root / "tests" / "test_more.py").write_text(
        "def test_math():\n    assert 1 + 1 == 3, 'math is off'\n\n"
        "def test_folder(tmp_path):\n    (tmp_path / 'x').write_text('y')\n\n"
        "def test_fixture(capsys):\n    pass\n", encoding="utf-8")
    check = svc.meta_promote_check("dev", "test")
    assert check["tests"]["failed"] == 2
    assert any("test_more.py::test_math failed: AssertionError: math is off" in e for e in check["errors"])
    assert any("test_fixture errored" in e and "capsys" in e for e in check["errors"])
    with pytest.raises(GateError):
        svc.meta_promote("dev", "test")
    saved = svc.meta_tests()
    assert saved["failed"] == 2 and saved["stale"] is False
    assert {t["name"]: t["status"] for t in saved["tests"]}["test_folder"] == "passed"

    (root / "tests" / "test_more.py").write_text("def test_math():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    assert svc.meta_tests()["stale"] is True
    assert svc.meta_promote("dev", "test")["env"] == "test"


def test_hook_tests_are_linked_to_the_code_that_names_them(svc):
    text = svc.meta_entity("silver.orders")["text"]
    hook = '\n[hooks]\nafter_clean = "transforms.common:noop"\nowner = "me"\ntest = "tests/test_common.py::test_noop"\n'
    root = svc._project().root
    (root / "transforms" / "common.py").write_text(
        (root / "transforms" / "common.py").read_text() + "\n\ndef noop(df, ctx):\n    return df\n")
    (root / "tests" / "test_common.py").write_text(
        (root / "tests" / "test_common.py").read_text() + "\n\ndef test_noop():\n    assert True\n")
    svc.meta_save_entity("silver.orders", text + hook)
    out = svc.meta_tests(run=True)
    noop = next(t for t in out["tests"] if t["name"] == "test_noop")
    assert noop["status"] == "passed" and noop["used_by"] == ["silver.orders hook after_clean transforms.common:noop"]
    assert out["missing"] == []


# ---- VL_Sources

def test_variable_library_definition_has_a_value_set_per_environment(svc):
    project = svc._project()
    sources = project.source_systems
    sources[0].connections["prod"] = {"connection_type": "Files", "file_path": "prod/customers"}
    from fabric_desktop.meta import keys
    keys.write_source_systems(project.root, sources)
    project.reload()
    rows = {r["name"]: r for r in varlib.values(project)}
    assert rows["CRM_file_path"] == {"name": "CRM_file_path", "note": "Customer export: file path", "dev": "landing/customers",
                                     "test": "", "prod": "prod/customers"}
    assert rows["ENVIRONMENT"]["prod"] == "prod"
    parts = varlib.definition(project)
    variables = json.loads(parts["variables.json"])["variables"]
    assert {"name": "CRM_file_path", "note": "Customer export: file path", "type": "String", "value": "landing/customers"} in variables
    prod = json.loads(parts["valueSets/Prod.json"])
    assert {"name": "CRM_file_path", "value": "prod/customers"} in prod["variableOverrides"]
    assert json.loads(parts["settings.json"])["valueSetsOrder"] == ["Test", "Prod"]
    assert varlib.resolve(project, "prod")["CRM_file_path"] == "prod/customers"


def test_publish_variable_library_and_read_it_in_a_notebook(svc, monkeypatch):
    svc.meta_set_environment("test", "ws-test", "Sales Test")
    calls = []

    class Fake:
        def save_variable_library(self, ws, name, parts, description=""):
            calls.append(("save", ws, name, sorted(parts)))
            return {"id": "vl1", "created": True}

        def set_active_value_set(self, ws, lib, value_set):
            calls.append(("active", ws, lib, value_set))
    monkeypatch.setattr(type(svc), "fabric", property(lambda self: Fake()))
    out = svc.meta_publish_varlib("test")
    assert out["created"] and out["value_set"] == "Test" and "variablelibraries/vl1" in out["url"]
    assert calls == [("save", "ws-test", "VL_Sources", ["settings.json", "valueSets/Prod.json", "valueSets/Test.json", "variables.json"]),
                     ("active", "ws-test", "vl1", "Test")]
    assert "test" in svc.meta_varlib()["published"]

    nbu = build(Host(variables=svc._notebook_variables))
    lib = nbu.variableLibrary.getLibrary("VL_Sources")
    assert lib.CRM_file_path == "landing/customers" and lib.ENVIRONMENT == "dev"
    assert nbu.variableLibrary.get("$(/**/VL_Sources/SHOP_file_path)") == "landing/orders"
    with pytest.raises(LookupError):
        nbu.variableLibrary.getLibrary("VL_Other")
    with pytest.raises(AttributeError):
        lib.NOPE


def test_variable_library_rest_calls():
    c, s = client([Resp(200, {"value": [{"id": "v1", "displayName": "VL_Sources"}]}), Resp(200, {}), Resp(200, {})])
    out = c.save_variable_library("ws", "VL_Sources", {"variables.json": "{}"})
    assert out["created"] is False and s.calls[1][1].endswith("/variableLibraries/v1/updateDefinition")
    c.set_active_value_set("ws", "v1", "Prod")
    assert s.calls[2] == ("PATCH", "https://api.fabric.microsoft.com/v1/workspaces/ws/variableLibraries/v1",
                          {"properties": {"activeValueSetName": "Prod"}})


def test_layer_default_retention():
    assert Layer("x").retention_days == 7 and Layer("x").to_dict()["retention_days"] == 7


def _vl_parts():
    return {
        "variables.json": json.dumps({"variables": [
            {"name": "server", "type": "String", "value": "dev-sql"},
            {"name": "batch_size", "type": "Integer", "value": 500},
            {"name": "full_load", "type": "Boolean", "value": False},
            {"name": "ratio", "type": "Number", "value": "0.5"}]}),
        "valueSets/Prod.json": json.dumps({"name": "Prod", "variableOverrides": [
            {"name": "server", "value": "prod-sql"}, {"name": "batch_size", "value": "2000"}]}),
        "settings.json": json.dumps({"valueSetsOrder": ["Prod"]}),
    }


def test_any_variable_library_reads_its_active_value_set():
    assert varlib.read(_vl_parts(), "Default value set") == {"server": "dev-sql", "batch_size": 500, "full_load": False, "ratio": 0.5}
    assert varlib.read(_vl_parts(), "Prod") == {"server": "prod-sql", "batch_size": 2000, "full_load": False, "ratio": 0.5}
    with pytest.raises(LookupError, match="Test"):
        varlib.read(_vl_parts(), "Test")
    bad = _vl_parts() | {"valueSets/Prod.json": json.dumps({"name": "Prod", "variableOverrides": [
        {"name": "batch_size", "value": "secret-ish text"}]})}
    with pytest.raises(ValueError) as err:
        varlib.read(bad, "Prod")
    assert "batch_size is Integer" in str(err.value) and "secret-ish" not in str(err.value)


@pytest.mark.parametrize("name, value", [("batch_size", 1.5), ("batch_size", True), ("full_load", "yes"),
                                         ("full_load", "1")])
def test_a_variable_value_of_the_wrong_kind_isnt_rounded_or_read_as_false(name, value):
    bad = _vl_parts() | {"valueSets/Prod.json": json.dumps({"name": "Prod", "variableOverrides": [
        {"name": name, "value": value}]})}
    with pytest.raises(ValueError, match=f"{name} is"):
        varlib.read(bad, "Prod")
    good = _vl_parts() | {"valueSets/Prod.json": json.dumps({"name": "Prod", "variableOverrides": [
        {"name": "batch_size", "value": 3.0}, {"name": "full_load", "value": " TRUE "}]})}
    assert varlib.read(good, "Prod")["batch_size"] == 3 and varlib.read(good, "Prod")["full_load"] is True


def test_a_broken_library_file_is_named():
    with pytest.raises(ValueError, match="valueSets/Prod.json isn't valid JSON"):
        varlib.read(_vl_parts() | {"valueSets/Prod.json": "{nope"}, "Prod")
    with pytest.raises(ValueError, match="variables.json isn't a JSON object"):
        varlib.read(_vl_parts() | {"variables.json": "[]"}, None)


def test_other_variable_libraries_come_from_the_attached_lakehouse_workspace(svc, monkeypatch, capsys):
    from fabric_desktop import lakehouse
    calls = []

    class Fake:
        def read_variable_library(self, ws, name):
            calls.append((ws, name))
            return (_vl_parts(), "Prod") if name == "VL_App" else None
    monkeypatch.setattr(type(svc), "fabric", property(lambda self: Fake()))
    nbu = build(Host(variables=svc._notebook_variables))
    # Nothing attached: no workspace to read from, and nothing is called.
    with pytest.raises(LookupError, match="Attach a lakehouse"):
        nbu.variableLibrary.getLibrary("VL_App")
    assert calls == []
    monkeypatch.setattr(lakehouse, "_attached", types.SimpleNamespace(workspace_id="ws-1", workspace_name="Sales Landing"))
    assert nbu.variableLibrary.get("$(/**/VL_App/batch_size)") == 2000
    lib = nbu.variableLibrary.getLibrary("VL_App")
    assert lib.server == "prod-sql" and "__fd_from__" not in dir(lib)
    # It says once which workspace it read, since Fabric reads the notebook's own.
    said = capsys.readouterr().out
    assert said.count("Read VL_App from workspace Sales Landing, the attached lakehouse's") == 1
    with pytest.raises(LookupError, match="no variable library named VL_Missing"):
        nbu.variableLibrary.getLibrary("VL_Missing")
    assert calls[0] == ("ws-1", "VL_App")
    # VL_Sources still comes from the metadata project, with no Fabric call.
    n = len(calls)
    assert nbu.variableLibrary.getLibrary("VL_Sources").ENVIRONMENT == "dev"
    assert len(calls) == n


def test_read_variable_library_rest_calls():
    import base64
    enc = lambda t: base64.b64encode(t.encode()).decode()
    c, s = client([
        Resp(200, {"value": [{"id": "v9", "displayName": "VL_App", "properties": {"activeValueSetName": "Prod"}}]}),
        Resp(200, {"definition": {"parts": [{"path": "variables.json", "payload": enc('{"variables": []}')}]}})])
    parts, active = c.read_variable_library("ws", "VL_App")
    assert parts == {"variables.json": '{"variables": []}'} and active == "Prod"
    assert s.calls[1][0] == "POST" and s.calls[1][1].endswith("/items/v9/getDefinition")
    c, s = client([Resp(200, {"value": []})])
    assert c.read_variable_library("ws", "VL_App") is None
