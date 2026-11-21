"""Mutation batch 53: tenant settings history's value hashing, newest-snapshot pick and read rows. Pure
functions; nothing here reads Fabric."""

from fabric_desktop import tenant_history as th


def test_canonical_sorts_keys_and_keeps_non_ascii_text():
    assert th.canonical({"b": 1, "a": "Zürich"}) == '{"a":"Zürich","b":1}'


def test_security_groups_and_properties_are_put_in_a_fixed_order():
    s = {"settingName": "S", "title": "t", "enabledSecurityGroups": [{"graphId": "g2"}, None, {"graphId": "g1"}],
         "excludedSecurityGroups": [{"graphId": "x2"}, {"graphId": "x1"}],
         "properties": [{"name": "b", "value": "1"}, {"name": "a", "value": "2"}]}
    got = th.value_of(s)
    assert got["enabledSecurityGroups"] == [None, {"graphId": "g1"}, {"graphId": "g2"}]
    assert got["excludedSecurityGroups"] == [{"graphId": "x1"}, {"graphId": "x2"}]
    assert got["properties"] == [{"name": "a", "value": "2"}, {"name": "b", "value": "1"}]
    assert "settingName" not in got and "title" not in got


def test_of_two_snapshots_taken_at_the_same_time_the_first_is_kept():
    rows = [{"scope_id": "t", "setting": "S", "taken_at": "2026-11-01T00:00:00Z", "value_json": "1"},
            {"scope_id": "t", "setting": "S", "taken_at": "2026-11-01T00:00:00Z", "value_json": "2"},
            {"scope_id": "t", "setting": "S", "taken_at": "2026-10-01T00:00:00Z", "value_json": "0"}]
    assert th.latest(rows, "t")["S"]["value_json"] == "1"


def test_a_read_with_no_tenant_is_recorded_with_a_dash_and_its_reason_capped():
    row = th.read_row(None, "oid", "2026-11-01T00:00:00Z", "failed", "x" * 600)
    assert row["record_id"] == "read:-:oid:2026-11-01T00:00:00Z"
    assert row["reason"] == "x" * 500
    assert th.read_row("t-1", "oid", "now", "ok", None)["record_id"] == "read:t-1:oid:now"
