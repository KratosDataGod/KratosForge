"""Opt-in usage data (FD-137)."""

import datetime as dt
import json

import pytest

from fabric_desktop import app_settings, server, telemetry
from fabric_desktop.telemetry import Telemetry

KEY = "11111111-2222-3333-4444-555555555555"
CONN = f"InstrumentationKey={KEY};IngestionEndpoint=https://westeurope-5.in.applicationinsights.azure.com/"
ORG = "InstrumentationKey=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee;IngestionEndpoint=https://contoso.in.applicationinsights.azure.com/"


@pytest.fixture
def policy(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("FABRIC_DESKTOP_TELEMETRY_CONNECTION_STRING", CONN)
    path = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    path.parent.mkdir(parents=True)
    return path


class Day:
    def __init__(self, day="2026-10-17"):
        self.day = dt.date.fromisoformat(day)

    def __call__(self):
        return self.day


class Service:
    def __init__(self, usage):
        self.telemetry = usage
        self.audit = None

    def run_sql(self, query):
        return {"rows": []}

    def meta_promote(self, env):
        raise RuntimeError("409 conflict on workspace Sales-Prod for ann@contoso.com")

    def list_workspaces(self):
        return []


def call(service, method, **params):
    return server.handle(service, json.dumps({"id": 1, "method": method, "params": params}))


def test_off_until_turned_on_and_nothing_is_kept_while_off(policy, tmp_path):
    usage = Telemetry(tmp_path / "t", today=Day())
    assert app_settings.value("telemetry") is False
    call(Service(usage), "run_sql", query="SELECT * FROM customers")
    assert not usage.path.exists()
    assert usage.status()["on"] is False and usage.status()["available"] is True


def test_counts_only_action_names_failures_kinds_and_time(policy, tmp_path):
    app_settings.save({"telemetry": True})
    usage = Telemetry(tmp_path / "t", today=Day())
    s = Service(usage)
    call(s, "run_sql", query="SELECT secret FROM customers")
    call(s, "run_sql", query="SELECT 1")
    call(s, "meta_promote", env="prod")
    call(s, "list_workspaces")  # a read: not counted
    [waiting] = usage.status()["waiting"]
    assert waiting["features"]["run_sql"]["count"] == 2 and waiting["features"]["run_sql"]["errors"] == 0
    assert waiting["features"]["meta_promote"] == {"count": 1, "errors": 1, "seconds": waiting["features"]["meta_promote"]["seconds"]}
    assert waiting["errors"] == {"RuntimeError": 1}
    assert "list_workspaces" not in waiting["features"]
    text = usage.path.read_text() + json.dumps(waiting)
    for private in ("customers", "secret", "Sales-Prod", "contoso", "prod\""):
        assert private not in text, private


def test_sends_finished_days_once_and_today_waits(policy, tmp_path):
    app_settings.save({"telemetry": True})
    day = Day("2026-10-16")
    usage = Telemetry(tmp_path / "t", today=day)
    usage.record("run_sql", True, 1.25)
    day.day = dt.date(2026, 10, 17)
    usage.record("run_sql", True, 0.5)
    sent = []
    assert usage.send_due(post=lambda conn, envs: sent.append((conn, envs))) == {"sent": ["2026-10-16"]}
    [(conn, [env])] = sent
    assert conn == CONN and env["iKey"] == KEY and env["data"]["baseData"]["name"] == "daily_usage"
    props = env["data"]["baseData"]["properties"]
    assert props["day"] == "2026-10-16" and json.loads(props["features"]) == {"run_sql": {"count": 1, "errors": 0, "seconds": 1.2}}
    assert all(isinstance(v, str) for v in props.values())  # Application Insights properties are strings
    assert set(props) == {"day", "install_id", "build", "channel", "windows", "python", "features", "errors"}
    status = usage.status()
    assert status["last_sent"] == "2026-10-16" and [w["day"] for w in status["waiting"]] == ["2026-10-17"]
    assert usage.send_due(post=lambda *a: pytest.fail("nothing is due")) == {"sent": []}


def test_a_failed_send_keeps_the_days_for_next_time(policy, tmp_path):
    app_settings.save({"telemetry": True})
    day = Day("2026-10-16")
    usage = Telemetry(tmp_path / "t", today=day)
    usage.record("run_sql", True, 1.0)
    day.day = dt.date(2026, 10, 17)

    def offline(conn, envs):
        raise OSError("no network")
    with pytest.raises(OSError):
        usage.send_due(post=offline)
    assert [w["day"] for w in usage.status()["waiting"]] == ["2026-10-16"]


def test_old_unsent_days_are_dropped(policy, tmp_path):
    app_settings.save({"telemetry": True})
    day = Day("2026-08-01")
    usage = Telemetry(tmp_path / "t", today=day)
    usage.record("run_sql", True, 1.0)
    day.day = dt.date(2026, 10, 17)
    assert usage.send_due(post=lambda *a: pytest.fail("too old to send")) == {"sent": []}
    assert usage.status()["waiting"] == []


def test_turning_it_off_deletes_what_was_waiting(policy, tmp_path):
    from fabric_desktop.service import Service as Real
    app_settings.save({"telemetry": True})
    real = Real(root=tmp_path / "app")
    real.telemetry.record("run_sql", True, 1.0)
    assert real.telemetry_status()["waiting"]
    real.save_app_settings({"telemetry": False})
    assert real.telemetry_status()["waiting"] == [] and real.telemetry_status()["on"] is False


def test_the_organization_decides(policy, tmp_path):
    usage = Telemetry(tmp_path / "t", today=Day())
    policy.write_text(json.dumps({"telemetry": False}))
    with pytest.raises(ValueError, match="organization"):
        app_settings.save({"telemetry": True})
    assert not usage.enabled()
    policy.write_text(json.dumps({"telemetry": True, "telemetry_connection_string": ORG}))
    assert usage.enabled() and telemetry.destination() == (ORG, "organization")
    policy.write_text(json.dumps({"telemetry": True, "telemetry_connection_string": "https://example.com"}))
    assert not usage.enabled()  # a bad one sends nothing, not to KratosBI instead
    assert any("telemetry_connection_string" in p for p in app_settings.policy_problems())


def test_a_build_without_a_destination_never_sends(policy, tmp_path, monkeypatch):
    monkeypatch.delenv("FABRIC_DESKTOP_TELEMETRY_CONNECTION_STRING")
    app_settings.save({"telemetry": True})
    usage = Telemetry(tmp_path / "t", today=Day())
    usage.record("run_sql", True, 1.0)
    assert not usage.path.exists()
    assert usage.status() | {"install_id": ""} == {"on": True, "available": False, "destination": "", "install_id": "",
                                                   "last_sent": "", "today": "2026-10-17", "waiting": []}


def test_a_new_install_id(policy, tmp_path):
    usage = Telemetry(tmp_path / "t", today=Day())
    first = usage.new_install_id()
    assert usage.new_install_id() != first and usage.status()["install_id"] != first


def test_error_kind_never_carries_the_message():
    assert telemetry.error_kind("HTTPError: 403 for https://x/workspaces/123") == "HTTPError"
    assert telemetry.error_kind("bronze.orders: tests failed") == "Error"
    assert telemetry.error_kind("SalesProd: not allowed") == "Error"  # a name, not an exception's kind
    assert telemetry.error_kind("") == "Error"
