"""Edge cases of capacity.py (FINOPS-002): error classes, signed-out reads, partial flags and freshness."""

from __future__ import annotations

import datetime as dt

from fabric_desktop import capacity
from fabric_desktop.fabric_api import FabricError

NOW = dt.datetime(2026, 10, 17, 12, tzinfo=dt.UTC)
CAP = {"id": "cap-1", "displayName": "Contoso F64", "sku": "F64", "region": "West Europe", "state": "Active"}


def _ws(**fields):
    return lambda _ws_id: {"id": "w1", "capacityId": "cap-1", **fields}


def test_status_codes_alone_classify_a_failed_call():
    assert capacity.classify_error(FabricError("GET failed with 401", "", 401)) == "workspace_no_access"
    assert capacity.classify_error(FabricError("GET failed with 404", "", 404)) == "workspace_not_found"
    assert capacity.classify_error(FabricError("GET failed with 402", "", 402)) == "error"
    assert capacity.classify_error(FabricError("GET failed with 405", "", 405)) == "error"


def test_error_codes_alone_classify_a_failed_call():
    assert capacity.classify_error(FabricError("GET failed", "WorkspaceNotFound", None)) == "workspace_not_found"
    assert capacity.classify_error(FabricError("GET failed", "Unauthorized", None)) == "workspace_no_access"
    assert capacity.classify_error(FabricError("GET failed", "SomethingElse", None)) == "error"


def test_a_capacity_without_a_state_is_unknown():
    cap = {k: v for k, v in CAP.items() if k != "state"}
    e = capacity.observe_env("dev", "w1", None, _ws(), {"cap-1": cap}, None, NOW)
    assert e["access"] == "ok"
    assert (e["status"], e["status_text"]) == ("unknown", "Its state isn't known.")


def test_without_a_time_given_the_reading_is_stamped_with_the_current_utc_time():
    before = dt.datetime.now(dt.UTC).replace(microsecond=0)
    e = capacity.observe_env("dev", "w1", None, _ws(), {"cap-1": CAP}, None)
    after = dt.datetime.now(dt.UTC)
    stamp = dt.datetime.fromisoformat(e["observed_at"])
    assert stamp.utcoffset() == dt.timedelta(0)
    assert before <= stamp <= after


def test_a_long_error_message_is_cut_to_300_characters():
    def fail(_ws_id):
        raise FabricError("x" * 500, "SomethingElse", None)
    e = capacity.observe_env("dev", "w1", None, fail, {}, None, NOW)
    assert e["access"] == "error" and e["error"] == "x" * 300


def test_the_workspace_name_fabric_returns_wins_and_the_configured_one_is_the_fallback():
    named = capacity.observe_env("dev", "w1", "Old name", _ws(displayName="Sales Dev"), {"cap-1": CAP}, None, NOW)
    assert named["workspace_name"] == "Sales Dev"
    unnamed = capacity.observe_env("dev", "w1", "Old name", _ws(), {"cap-1": CAP}, None, NOW)
    assert unnamed["workspace_name"] == "Old name"


def test_without_a_capacity_list_reader_an_environment_on_a_capacity_reads_as_signed_out():
    doc = capacity.observe({"dev": {"workspace_id": "w1"}}, _ws(), None, NOW)
    [e] = doc["environments"]
    assert e["access"] == "signed_out" and e["capacity_id"] == "cap-1"


def test_partial_is_set_only_when_an_environment_is_neither_read_nor_unassigned():
    caps = lambda: [CAP]  # noqa: E731
    whole = capacity.observe({"dev": {"workspace_id": "w1"}, "test": {}}, _ws(), caps, NOW)
    assert [e["access"] for e in whole["environments"]] == ["ok", "no_workspace"]
    assert whole["partial"] is False
    hidden = capacity.observe({"dev": {"workspace_id": "w1"}}, lambda _w: {"capacityId": "cap-x"}, caps, NOW)
    assert hidden["environments"][0]["access"] == "capacity_not_visible"
    assert hidden["partial"] is True


def test_freshness_counts_whole_seconds_and_is_stale_only_past_an_hour():
    at = {"observed_at": capacity._iso(NOW)}
    assert capacity.freshness(at, NOW) == {"age_seconds": 0, "stale": False}
    assert capacity.freshness(at, NOW + dt.timedelta(seconds=3600)) == {"age_seconds": 3600, "stale": False}
    assert capacity.freshness(at, NOW + dt.timedelta(seconds=3600.5))["stale"] is True
    assert capacity.freshness(at, NOW - dt.timedelta(seconds=30))["age_seconds"] == 0
