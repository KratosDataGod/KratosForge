"""Opt-in usage data (telemetry.py), pinned where mutation testing found gaps: how long unsent days
are kept, the build and install ids, the file's format, what is sent and how."""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest

from fabric_desktop import app_settings, http_session, retrying, telemetry
from fabric_desktop.telemetry import Telemetry
from tests.test_telemetry import CONN, KEY, Day, policy  # noqa: F401  (policy is a fixture)


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "home"))


@pytest.fixture
def on(policy, tmp_path):  # noqa: F811
    app_settings.save({"telemetry": True})
    return tmp_path


def usage_with_days(folder, today, days):
    usage = Telemetry(folder / "t", today=Day(today))
    usage._save({"install_id": "id-1", "days": {d: {"features": {}, "errors": {}} for d in days}})
    return usage


def test_unsent_days_are_kept_for_thirty_days(on):
    # Today 2026-10-17: the cutoff is 2026-09-17, which is still sent; the day before it is dropped.
    usage = usage_with_days(on, "2026-10-17", ["2026-09-16", "2026-09-17", "2026-10-16"])
    sent = []
    assert usage.send_due(post=lambda conn, envs: sent.append([e["data"]["baseData"]["properties"]["day"] for e in envs])) \
        == {"sent": ["2026-09-17", "2026-10-16"]}
    assert sent == [["2026-09-17", "2026-10-16"]]


def test_last_sent_is_the_latest_day_sent(on):
    usage = usage_with_days(on, "2026-10-17", ["2026-10-15", "2026-10-16"])
    usage.send_due(post=lambda conn, envs: None)
    assert usage.status()["last_sent"] == "2026-10-16"


def test_nothing_is_sent_while_off(policy, tmp_path):  # noqa: F811
    usage = usage_with_days(tmp_path, "2026-10-17", ["2026-10-16"])
    assert usage.send_due(post=lambda *a: pytest.fail("off")) == {"sent": []}


@pytest.mark.parametrize("info, build", [({"commit": "0123456789abcdef"}, "0123456789ab"), ({"commit": "abc"}, "abc"),
                                         ({"commit": ""}, "dev"), ({}, "dev"), ({"commit": None}, "dev")])
def test_the_build_is_the_commits_first_twelve_characters_or_dev(monkeypatch, info, build):
    monkeypatch.setattr(app_settings, "_read_engine_info", lambda: info)
    assert telemetry._build() == build


def test_the_install_id_is_made_once_and_returned(tmp_path):
    usage = Telemetry(tmp_path / "t")
    data: dict = {}
    made = usage._install_id(data)
    assert made == data["install_id"] and len(made) == 36
    assert usage._install_id(data) == made
    assert usage._install_id({"install_id": "kept"}) == "kept"


def test_status_shows_the_install_id_on_file(on):
    usage = usage_with_days(on, "2026-10-17", [])
    assert usage.status()["install_id"] == "id-1"


def test_an_unreadable_file_reads_as_empty(tmp_path):
    usage = Telemetry(tmp_path / "t")
    usage.path.mkdir(parents=True)  # a folder where the file should be: reading it is an OSError
    assert usage._load() == {}


def test_the_file_is_sorted_json_with_one_space_indents(on):
    usage = Telemetry(on / "t", today=Day())
    usage.record("sql_start", True, 1.0)
    data = json.loads(usage.path.read_text())
    assert usage.path.read_text() == json.dumps(data, indent=1, sort_keys=True)
    assert list(data) == ["days", "install_id"]


def test_the_windows_version_is_sent_on_windows_and_the_platform_elsewhere(monkeypatch, tmp_path):
    monkeypatch.setattr(telemetry.platform, "version", lambda: "10.0.26100")
    monkeypatch.setattr(telemetry.platform, "platform", lambda: "Linux-6.1")
    usage = Telemetry(tmp_path / "t")
    monkeypatch.setattr(telemetry, "os", SimpleNamespace(name="nt"))
    assert usage.payload("2026-10-16", {}, "id")["windows"] == "10.0.26100"
    monkeypatch.setattr(telemetry, "os", SimpleNamespace(name="posix"))
    assert usage.payload("2026-10-16", {}, "id")["windows"] == "Linux-6.1"


def test_an_envelope_is_one_custom_event_with_string_properties():
    payload = {"name": "daily_usage", "day": "2026-10-16", "install_id": "id", "features": {"b": {"z": 1, "a": 2}}}
    assert telemetry.envelope(CONN, payload) == {
        "name": "Microsoft.ApplicationInsights.Event", "time": "2026-10-16T23:59:59Z", "iKey": KEY,
        "data": {"baseType": "EventData", "baseData": {"ver": 2, "name": "daily_usage", "properties": {
            "day": "2026-10-16", "install_id": "id", "features": '{"b": {"a": 2, "z": 1}}'}}}}


def fake_send(monkeypatch, status):
    seen = {}

    def send_with_retry(send, method, sleep=None, safe=None):
        seen.update(method=method, sleep=sleep, safe=safe)
        return send()

    def post(url, json, timeout):
        seen.update(url=url, json=json, timeout=timeout)
        return SimpleNamespace(status_code=status)
    monkeypatch.setattr(retrying, "send_with_retry", send_with_retry)
    monkeypatch.setattr(http_session, "session", lambda: SimpleNamespace(post=post))
    return seen


def test_a_report_is_posted_once_with_a_fifteen_second_limit(monkeypatch):
    seen = fake_send(monkeypatch, 200)
    nap = lambda s: None  # noqa: E731
    telemetry._post(CONN, [{"name": "x"}], sleep=nap)
    assert seen == {"method": "POST", "sleep": nap, "safe": False, "json": [{"name": "x"}], "timeout": 15,
                    "url": "https://westeurope-5.in.applicationinsights.azure.com/v2/track"}
    import time
    telemetry._post(CONN, [], sleep=None)
    assert seen["sleep"] is time.sleep


@pytest.mark.parametrize("status, refused", [(200, False), (399, False), (400, True), (500, True)])
def test_a_status_of_400_or_more_keeps_the_days_queued(monkeypatch, status, refused):
    fake_send(monkeypatch, status)
    if refused:
        with pytest.raises(RuntimeError, match=f"wasn't sent \\({status}\\)"):
            telemetry._post(CONN, [])
    else:
        telemetry._post(CONN, [])


def test_finished_days_send_on_the_day_after(on):
    usage = usage_with_days(on, "2026-10-17", ["2026-10-17"])
    assert usage.send_due(post=lambda *a: pytest.fail("today waits")) == {"sent": []}
    usage._today = Day("2026-10-18")
    assert usage.send_due(post=lambda conn, envs: None) == {"sent": ["2026-10-17"]}
    assert dt.date.fromisoformat(usage.status()["last_sent"]) == dt.date(2026, 10, 17)
