"""Workload actions on the platform's audit (plan item 76): each action and change made from a
workload page goes to the platform as who, what, where, before, after and how it ended. The line
is kept on this PC until the platform has it; a platform that can't take it never fails the action."""

import json

import pytest

from fabric_desktop.central import core
from fabric_desktop.central.client import LocalPlatform
from fabric_desktop.workloads import model
from fabric_desktop.workloads.store import ManagementStore

from test_workloads import USER, signed_in, svc  # noqa: F401 - fixtures


def _platform(svc):
    """A platform on this PC, as Platform > Settings makes one."""
    return svc._catalog()


def _lines(svc):
    rows = _platform(svc).call("audit", limit=100)
    return [dict(r, detail=json.loads(r["detail"]) if r["detail"] else None)
            for r in reversed(rows) if r["action"].startswith("workload ")]


@pytest.fixture
def project(svc, tmp_path):  # noqa: F811
    svc.meta_init(str(tmp_path / "proj"), "3")
    return svc._project()


def test_a_run_goes_to_the_platform_as_submitted_then_how_it_ended(svc, project):  # noqa: F811
    signed_in(svc)
    _platform(svc)
    wid = model.meta_id("proj", sorted(project.entities)[0])
    out = svc.workload_act(wid, "run", "dev")
    lines = _lines(svc)
    assert [x["action"] for x in lines] == ["workload run submitted", f"workload run {out['action']['status']}"]
    first = lines[0]
    assert first["by_user"] == USER and first["target"] == f"workload:{wid}"
    assert first["detail"]["id"] == out["action"]["id"] and first["detail"]["env"] == "dev"
    assert first["detail"]["where"] == "This PC" and USER in first["detail"]["identity"]
    assert svc.workload_audit_state()["waiting"] == 0


def test_a_fabric_run_reaches_the_platform_only_when_fabric_says_how_it_ended(svc, monkeypatch):  # noqa: F811
    from test_workloads import scan
    signed_in(svc)
    _platform(svc)
    scan(svc, monkeypatch)
    wid = model.fabric_id("ws1", "pl1")
    monkeypatch.setattr(svc, "run_fabric_item_now", lambda ws, item, job, name, ws_name="", item_type="": svc._log_fabric_run(
        {"kind": "Scheduled item run", "name": name, "workspace_id": ws, "workspace_name": ws_name,
         "item_id": item, "job_id": "job-1"}))
    svc.workload_act(wid, "run_item")
    svc.workloads_list(view="all", limit=500)
    assert [x["action"] for x in _lines(svc)] == ["workload run_item submitted"]  # never success on submission
    runs = svc._read_fabric_runs()
    runs[-1]["status"] = "Failed"
    svc._write_fabric_runs(runs)
    svc.workloads_list(view="all", limit=500)
    svc.workloads_list(view="all", limit=500)  # settled once, sent once
    assert [x["action"] for x in _lines(svc)] == ["workload run_item submitted", "workload run_item failed"]


def test_an_action_that_throws_is_on_the_platform_as_failed(svc, project, monkeypatch):  # noqa: F811
    signed_in(svc)
    _platform(svc)
    wid = model.meta_id("proj", sorted(project.entities)[0])
    monkeypatch.setattr(svc, "meta_run", lambda names, env: (_ for _ in ()).throw(RuntimeError("engine stopped")))
    with pytest.raises(RuntimeError):
        svc.workload_act(wid, "run", "dev")
    lines = _lines(svc)
    assert [x["action"] for x in lines] == ["workload run submitted", "workload run failed"]
    assert "engine stopped" in lines[1]["detail"]["detail"]


def test_ownership_and_links_go_with_before_and_after(svc):  # noqa: F811
    signed_in(svc)
    _platform(svc)
    svc.notebooks.save("Daily load", [{"type": "code", "source": "x = 1"}])
    wid = model.notebook_id("Daily load")
    svc.workload_assign(wid, {"team": "Data team", "tags": ["b", "a"]})
    svc.workload_assign(wid, {"team": "Data team"})  # no change, no line
    svc.workload_assign(wid, {"team": "Sales data"})
    svc.workload_link("Daily load", "ws1", "nb1", "Sales Dev")
    svc.workload_link("Daily load", "ws1", "nb1", "Sales Dev")  # the same item again: no line
    svc.workload_unlink("Daily load")
    svc.workload_unlink("Daily load")  # nothing linked: no line
    got = [(x["action"], x["detail"]["target"], x["detail"].get("before", ""), x["detail"].get("after", ""))
           for x in _lines(svc)]
    assert got == [
        ("workload assign changed", "team", "", "Data team"),
        ("workload assign changed", "tags", "", "a, b"),
        ("workload assign changed", "team", "Data team", "Sales data"),
        ("workload link changed", "its Fabric item", "", "Sales Dev / nb1 (linked by hand)"),
        ("workload unlink changed", "its Fabric item", "Sales Dev / nb1 (linked by hand)", ""),
    ]
    history = [h["field"] for h in svc.workload_store.history(wid)]
    assert history.count("link") == 2  # kept on this PC too


def test_with_no_platform_lines_wait_and_none_is_made(svc):  # noqa: F811
    signed_in(svc)
    svc.notebooks.save("Daily load", [{"type": "code", "source": "x = 1"}])
    svc.workload_assign(model.notebook_id("Daily load"), {"team": "Data team"})
    assert not (svc.root / "platform").exists()
    state = svc.workload_audit_state()
    assert state["waiting"] == 1 and state["platform"] == "none" and state["error"] == ""
    _platform(svc)  # someone sets up a platform on this PC
    svc.workload_assign(model.notebook_id("Daily load"), {"team": "Sales data"})
    assert [x["detail"]["after"] for x in _lines(svc)] == ["Data team", "Sales data"]  # in order
    assert svc.workload_audit_state()["waiting"] == 0


def test_a_platform_that_refuses_keeps_the_lines_and_never_fails_the_change(svc, monkeypatch):  # noqa: F811
    signed_in(svc)
    platform = _platform(svc)
    real = platform.call

    def refuse(op, **kw):
        if op == "log_workload_action":
            raise LookupError("UDF_Platform wasn't found. Open Platform > Settings, then check the platform's workspace.")
        return real(op, **kw)

    monkeypatch.setattr(platform, "call", refuse)
    svc.notebooks.save("Daily load", [{"type": "code", "source": "x = 1"}])
    wid = model.notebook_id("Daily load")
    svc.workload_assign(wid, {"team": "Data team"})
    svc.workload_assign(wid, {"team": "Sales data"})
    state = svc.workload_audit_state()
    assert state["waiting"] == 2 and "Finish or repair" in state["error"]
    assert svc.workload_detail(wid)["owners"]["team"]["value"] == "Sales data"
    monkeypatch.setattr(platform, "call", real)
    svc._wl_audit_send()
    assert [x["detail"]["after"] for x in _lines(svc)] == ["Data team", "Sales data"]
    assert svc.workload_audit_state() | {"oldest": None, "tried_at": None} == {
        "waiting": 0, "oldest": None, "tried_at": None, "error": "", "platform": "this_pc"}


def test_retry_runs_in_the_background_at_most_every_ten_minutes(svc, monkeypatch):  # noqa: F811
    signed_in(svc)
    started = []
    monkeypatch.setattr("threading.Thread.start", lambda self: started.append(self.name))
    svc.notebooks.save("Daily load", [{"type": "code", "source": "x = 1"}])
    svc.workload_assign(model.notebook_id("Daily load"), {"team": "Data team"})  # waits: no platform
    started.clear()
    svc._wl_audit_retry()
    assert started == []  # nowhere to send it
    (svc.root / "platform").mkdir()
    (svc.root / "platform" / "platform-prod.sqlite").touch()
    svc._wl_audit_retry()
    assert started == ["workload-audit"]  # never tried yet
    svc.workload_store.audit_result(svc.workload_store.audit_waiting()[0]["key"], "down")
    started.clear()
    svc._wl_audit_retry()
    assert started == []  # tried just now


def test_secrets_are_scrubbed_and_values_cut(svc, project, monkeypatch):  # noqa: F811
    signed_in(svc)
    _platform(svc)
    wid = model.meta_id("proj", sorted(project.entities)[0])
    hook = "https://contoso.webhook.office.com/webhookb2/abc?sig=SECRET123"
    monkeypatch.setattr(svc, "meta_run", lambda names, env: (_ for _ in ()).throw(
        RuntimeError(f"post to {hook} failed " + "x" * 2000)))
    with pytest.raises(RuntimeError):
        svc.workload_act(wid, "run", "dev")
    detail = _lines(svc)[-1]["detail"]["detail"]
    assert "SECRET123" not in detail and len(detail) <= 500


def test_the_platform_checks_what_it_is_given(tmp_path):
    p = LocalPlatform(tmp_path / "p.sqlite", lambda: USER)
    with pytest.raises(core.PlatformError, match="action is one of"):
        p.call("log_workload_action", workload="w", action="delete_everything", status="changed")
    with pytest.raises(core.PlatformError, match="status is one of"):
        p.call("log_workload_action", workload="w", action="run", status="great")
    with pytest.raises(core.PlatformError, match="which workload"):
        p.call("log_workload_action", workload=" ", action="run", status="submitted")
    p.call("log_workload_action", workload="w", action="run", status="submitted",
           entry={"env": "dev", "unknown": "dropped", "detail": "y" * 900})
    row = p.call("audit", limit=1)[0]
    detail = json.loads(row["detail"])
    assert row["by_user"] == USER and set(detail) == {"env", "detail"} and len(detail["detail"]) == 500


def test_the_same_line_is_kept_once(tmp_path):
    store = ManagementStore(tmp_path / "w.sqlite")
    store.audit_queue("a:submitted", "w", "run", "submitted", {"id": "a"})
    store.audit_queue("a:submitted", "w", "run", "submitted", {"id": "a"})
    assert len(store.audit_waiting()) == 1
