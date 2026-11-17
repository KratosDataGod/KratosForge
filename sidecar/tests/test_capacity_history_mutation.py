"""Mutation batch 41: capacity_history.py. Pins the read row's record id (an administrator's read is
tagged, a read with no capacity says "-"), the 500-character cap on a reason, and that an override
row with no capacity id isn't treated as a capacity seen before."""

from __future__ import annotations

from fabric_desktop import capacity_history as ch

ME = "me@contoso.example"
AT = "2026-11-16T00:00:00Z"


def test_a_capacity_read_is_untagged_and_an_administrators_read_is_tagged():
    plain = ch.read_row("cap-1", ME, AT, "ok", None)
    admin = ch.read_row("cap-1", ME, AT, "ok", None, source=ch.ADMIN_SOURCE)
    assert plain["record_id"] == f"read:cap-1:{ME}:{AT}" and plain["source"] == ch.SOURCE
    assert admin["record_id"] == f"read:admin:cap-1:{ME}:{AT}" and admin["source"] == ch.ADMIN_SOURCE


def test_a_read_with_no_capacity_is_recorded_under_a_dash():
    row = ch.read_row(None, ME, AT, "failed", "Forbidden")
    assert row["record_id"] == f"read:-:{ME}:{AT}" and row["scope_id"] is None


def test_a_reason_is_cut_to_500_characters_and_an_empty_one_is_none():
    assert len(ch.read_row("c", ME, AT, "failed", "r" * 600)["reason"]) == 500
    assert ch.read_row("c", ME, AT, "failed", "r" * 500)["reason"] == "r" * 500
    assert ch.read_row("c", ME, AT, "ok", "")["reason"] is None


def test_an_override_row_without_a_capacity_id_is_not_a_capacity_seen_before():
    snapshots = [{"scope_id": None, "setting": ch.OVERRIDE + "ExportToExcel", "taken_at": "2026-11-01T00:00:00Z",
                  "value_json": '{"enabled":true}'},
                 {"scope_id": "cap-9", "setting": "displayName", "taken_at": "2026-11-01T00:00:00Z",
                  "value_json": '"Old"'}]
    overrides = [{"id": "CAP-1", "tenantSettings": [{"settingName": "ExportToExcel", "enabled": False}]}]
    snaps, changes = ch.compare_overrides(overrides, snapshots, ME, AT)
    assert [(s["scope_id"], s["setting"]) for s in snaps] == [("cap-1", "override:ExportToExcel")]
    assert [c["scope_id"] for c in changes] == ["cap-1"]
