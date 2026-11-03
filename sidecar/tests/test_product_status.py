"""How each product is doing now (data mesh P5): its team's incident check reports Red, Amber or Green to
the platform's catalog, and every reader sees it with the products."""

import types

import pytest

from fabric_desktop.central import core
from fabric_desktop.central.client import LocalPlatform
from fabric_desktop.meta import mesh
from fabric_desktop.service import Service

PORT = [{"port": "customers", "entity": "silver.customers", "table": "customers", "columns": {"id": "int64"}}]
OWNER = "chris@example.com"


def _product(name="customers", project="crm"):
    return {"project": project, "name": name, "access": "public", "status": "published", "version": "1", "owner": OWNER}


@pytest.fixture
def platform(tmp_path):
    p = LocalPlatform(tmp_path / "platform.sqlite", lambda: OWNER)
    p.call("register_product", product=_product(), ports=PORT, env="prod")
    return p


# --------------------------------------------------------------------------- the catalog


def test_a_team_reports_and_everyone_reads(platform, tmp_path):
    rows = platform.call("report_product_status", project="crm", env="prod",
                         statuses=[{"product": "customers", "status": "red", "why": "Load failed: silver.customers",
                                    "latest": "Acknowledged by dana@example.com", "latest_at": "2026-11-03T07:41:00Z"}])
    assert [r["status"] for r in rows] == ["red"]
    reader = LocalPlatform(tmp_path / "platform.sqlite", lambda: "rae@example.com")
    [row] = reader.call("list_product_status")
    assert row["product_id"] == "crm/customers" and row["env"] == "prod" and row["why"] == "Load failed: silver.customers"
    assert row["checked_by"] == OWNER and row["latest_at"] == "2026-11-03T07:41:00Z"


def test_a_new_report_replaces_the_last_and_a_product_left_out_shows_nothing(platform):
    platform.call("report_product_status", project="crm", env="prod", statuses=[{"product": "customers", "status": "red"}])
    platform.call("report_product_status", project="crm", env="test", statuses=[{"product": "customers", "status": "green"}])
    platform.call("report_product_status", project="crm", env="prod", statuses=[])
    assert [(r["env"], r["status"]) for r in platform.call("list_product_status")] == [("test", "green")]


def test_only_the_projects_own_members_report(platform, tmp_path):
    other = LocalPlatform(tmp_path / "platform.sqlite", lambda: "mallory@example.com")
    with pytest.raises(core.PlatformError, match="members"):
        other.call("report_product_status", project="crm", env="prod", statuses=[{"product": "customers", "status": "green"}])
    assert platform.call("list_product_status") == []


def test_a_project_with_nothing_published_reports_nothing_and_gets_no_first_owner(platform, tmp_path):
    other = LocalPlatform(tmp_path / "platform.sqlite", lambda: "mallory@example.com")
    assert other.call("report_product_status", project="hr", env="prod", statuses=[{"product": "x", "status": "red"}]) == []
    with platform._db() as db:
        assert core._stored_members(db, "hr") == {}


def test_a_product_the_catalog_doesnt_have_yet_is_skipped(platform):
    rows = platform.call("report_product_status", project="crm", env="prod",
                         statuses=[{"product": "customers", "status": "amber"}, {"product": "orders", "status": "red"}])
    assert [r["product_id"] for r in rows] == ["crm/customers"]


@pytest.mark.parametrize("kw, said", [
    ({"env": "qa"}, "env is one of"),
    ({"statuses": "red"}, "statuses is a list"),
    ({"statuses": [{"product": "customers", "status": "purple"}]}, "status is one of"),
    ({"statuses": [{"product": "customers", "status": "red"}, {"product": "customers", "status": "green"}]}, "once"),
    ({"statuses": [{"status": "red"}]}, "once"),
])
def test_a_report_nobody_can_read_is_refused(platform, kw, said):
    with pytest.raises(core.PlatformError, match=said):
        platform.call("report_product_status", **({"project": "crm", "env": "prod", "statuses": []} | kw))


def test_long_text_is_cut(platform):
    [row] = platform.call("report_product_status", project="crm", env="prod",
                          statuses=[{"product": "customers", "status": "amber", "why": "x" * 1000}])
    assert len(row["why"]) == 300


def test_reading_creates_nothing_on_a_platform_made_before_the_status_view(platform):
    with platform._db() as db:  # as a platform made before the Status view left it
        db.run(f"DROP TABLE {db.t('catalog', 'product_status')}")
        db.commit()
    assert platform.call("list_product_status") == []
    with platform._db() as db:
        assert not db.exists("catalog", "product_status")
    platform.call("report_product_status", project="crm", env="prod", statuses=[{"product": "customers", "status": "green"}])
    assert len(platform.call("list_product_status")) == 1  # the first report makes it


# --------------------------------------------------------------------------- what the team's check works out


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s._auth = types.SimpleNamespace(signed_in_user=lambda: OWNER, silent_token=lambda s: None,
                                    fabric_token=lambda: None, storage_token=lambda: None)
    s.meta_init(str(tmp_path / "crm"), "3")
    s.mesh_save_product({"name": "customers", "access": "public", "status": "published", "version": "1",
                         "ports": [{"name": "customers", "entity": "silver.customers"}]})
    s.mesh_save_product({"name": "draft_one", "access": "public", "status": "draft", "version": "1",
                         "ports": [{"name": "customers", "entity": "silver.customers"}]})
    s._project().reload()
    return s


def _inc(kind, name="silver.customers", state="open", **kw):
    return {"kind": kind, "name": name, "state": state, "events": [], "evidence": {}} | kw


@pytest.mark.parametrize("incidents, status, why", [
    ([], "green", ""),
    ([_inc("run_failed")], "red", "Load failed: silver.customers"),
    ([_inc("agent_offline", name="pc-1")], "red", "Agent offline"),
    ([_inc("late")], "amber", "Late: silver.customers"),
    ([_inc("promise", evidence={"products": ["customers"]})], "amber", "Promise broken: silver.customers"),
    ([_inc("promise", evidence={"products": ["someone_else"]})], "green", ""),
    ([_inc("late", name="silver.orders")], "green", ""),
    ([_inc("run_failed", state="resolved")], "green", ""),
    ([_inc("needs_decision"), _inc("cant_evaluate", name="prod")], "green", ""),
    ([_inc("late"), _inc("run_failed", state="acknowledged")], "red", "Load failed: silver.customers; Late: silver.customers"),
])
def test_red_stops_the_load_amber_is_late_or_a_broken_promise(svc, incidents, status, why):
    [s] = mesh.product_statuses(svc._project(), incidents)
    assert (s["product"], s["status"], s["why"]) == ("customers", status, why)


def test_the_latest_update_says_what_the_team_did_and_never_who_or_the_note(svc):
    inc = _inc("run_failed", events=[
        {"at": "2026-11-03T07:00:00Z", "by": "KratosForge", "what": "opened", "text": "x"},
        {"at": "2026-11-03T07:41:00Z", "by": "dana@example.com", "what": "acknowledged", "text": "secret detail"},
        {"at": "2026-11-03T07:50:00Z", "by": "KratosForge", "what": "seen", "text": ""}])
    [s] = mesh.product_statuses(svc._project(), [inc])
    assert s["latest"] == "Acknowledged by its team" and s["latest_at"] == "2026-11-03T07:41:00Z"
    assert "secret" not in str(s) and "dana" not in str(s), "notes and people's names stay with the team"


# --------------------------------------------------------------------------- end to end


def _run(entity, status, run_id):
    import datetime as dt
    return {"entity": entity, "status": status, "run_id": run_id, "rows_read": 10, "quarantined": 0,
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "seconds": 0}


def test_the_incident_check_reports_and_readers_see_it_with_the_products(svc, monkeypatch):
    name = mesh.project_name(svc._project())
    svc._catalog().call("register_product", product=_product(project=name), ports=PORT, env="prod")
    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: [_run("silver.customers", "failed", "r1")])
    svc.incidents_evaluate("dev")
    [p] = svc.platform_products()["products"]
    assert p["health"]["dev"]["status"] == "red" and p["health"]["dev"]["why"] == "Load failed: silver.customers"
    assert svc.platform_products()["can_status"] is True

    calls = []
    real = svc._catalog().call
    monkeypatch.setattr(type(svc._catalog()), "call", lambda self, op, **kw: (calls.append(op), real(op, **kw))[1])
    svc.incidents_evaluate("dev")
    assert "report_product_status" not in calls, "an unchanged status isn't sent again"

    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: [_run("silver.customers", "ok", "r2")])
    svc.incidents_evaluate("dev")
    assert svc.platform_products()["products"][0]["health"]["dev"]["status"] == "green"


def test_a_platform_that_cant_take_it_doesnt_stop_the_check(svc, monkeypatch):
    def refuse(self, op, **kw):
        if op == "report_product_status":
            raise LookupError("no such function")
        return real(self, op, **kw)

    real = type(svc._catalog()).call
    monkeypatch.setattr(type(svc._catalog()), "call", refuse)
    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: [_run("silver.customers", "failed", "r1")])
    out = svc.incidents_evaluate("dev")
    assert [c["change"] for c in out["changes"]] == ["opened"]


def test_one_agent_offline_while_another_is_live_is_amber_not_red(svc):
    offline = [_inc("agent_offline", name="pc-1")]
    assert mesh.product_statuses(svc._project(), offline, agents_live=False)[0]["status"] == "red"
    [s] = mesh.product_statuses(svc._project(), offline, agents_live=True)
    assert (s["status"], s["why"]) == ("amber", "Agent offline")
    assert mesh.product_statuses(svc._project(), offline + [_inc("run_failed")], agents_live=True)[0]["status"] == "red"


def test_two_agents_one_offline_reports_amber(svc, monkeypatch):
    name = mesh.project_name(svc._project())
    svc._catalog().call("register_product", product=_product(project=name), ports=PORT, env="prod")
    monkeypatch.setattr(svc, "_incident_agents", lambda env: {"readable": True, "agents": [
        {"agent": "pc-1", "state": "offline"}, {"agent": "pc-2", "state": "live"}]})
    svc._report_product_status("dev", [_inc("agent_offline", name="pc-1")])
    [row] = svc._catalog().call("list_product_status")
    assert row["status"] == "amber"


@pytest.mark.parametrize("refusal", [LookupError("no such function"),
                                     core.PlatformError("Only crm's members or a platform admin can report"),
                                     PermissionError("refused"), ValueError("the platform has no operation 'report_product_status'")])
def test_a_refusal_isnt_asked_again_this_session_but_a_network_error_is(svc, monkeypatch, refusal):
    calls = []

    def answer(self, op, **kw):
        if op == "report_product_status":
            calls.append(op)
            raise refusal
        return real(self, op, **kw)

    real = type(svc._catalog()).call
    monkeypatch.setattr(type(svc._catalog()), "call", answer)
    svc._report_product_status("dev", [_inc("run_failed")])
    svc._report_product_status("dev", [_inc("late")])
    assert len(calls) == 1

    refusal_free = []

    def offline(self, op, **kw):
        if op == "report_product_status":
            refusal_free.append(op)
            raise ConnectionError("network is unreachable")
        return real(self, op, **kw)

    monkeypatch.setattr(type(svc._catalog()), "call", offline)
    svc._product_status_refused.clear()
    svc._report_product_status("dev", [_inc("run_failed")])
    svc._report_product_status("dev", [_inc("run_failed")])
    assert len(refusal_free) == 2, "a network error is tried again on the next check"


@pytest.mark.parametrize("glitch", [core.PlatformError("The platform couldn't run it: UDF_Platform answered 400 with something that isn't JSON"),
                                    ValueError("Expecting value: line 1 column 1")])
def test_a_one_off_platform_error_is_tried_again(svc, monkeypatch, glitch):
    calls = []

    def answer(self, op, **kw):
        if op == "report_product_status":
            calls.append(op)
            raise glitch
        return real(self, op, **kw)

    real = type(svc._catalog()).call
    monkeypatch.setattr(type(svc._catalog()), "call", answer)
    svc._report_product_status("dev", [_inc("run_failed")])
    svc._report_product_status("dev", [_inc("run_failed")])
    assert len(calls) == 2
