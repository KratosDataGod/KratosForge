"""Mutation batch 122: integrations/store.py (docs/quality/mutation-testing.md). The survivors were the
time limits and sizes as numbers, the default HTTP port, what a change to a setting or a tool returns,
the refusals of an unknown setting or change, a new token for a tool that is gone, how a tool's lists are
stored, ids and token lengths, the default list sizes and arguments exactly at the size shown."""

from __future__ import annotations

import pytest

from fabric_desktop.integrations import store as st
from fabric_desktop.integrations.store import IntegrationStore


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return IntegrationStore(tmp_path / "integrations" / "integrations.sqlite", clock=clock)


def test_the_limits_and_the_default_port():
    assert (st.PENDING_SECONDS, st.APPROVED_SECONDS, st.MAX_ARGUMENT_TEXT) == (300, 600, 20_000)
    assert st.USES_SECONDS == 90 * 24 * 3600 == 7_776_000
    assert st.DEFAULT_SETTINGS["http_port"] == 47615


def test_a_setting_is_saved_and_all_settings_are_returned(store):
    assert store.set_setting("http_port", 5000) == {"enabled": False, "http_enabled": False, "http_port": 5000,
                                                   "extensions": {}}
    with pytest.raises(ValueError, match="unknown integration setting 'colour'"):
        store.set_setting("colour", "blue")
    assert "colour" not in store.settings()


def test_a_tool_s_change_is_returned_and_only_some_fields_may_change(store):
    client, token = store.add_client("Probe")
    assert token is None
    changed = store.update_client(client["id"], name="Renamed", grants=["B", "A", "A"])
    assert (changed["name"], changed["grants"]) == ("Renamed", ["A", "B"])
    with pytest.raises(ValueError, match="can't change revoked_at, token_hash"):
        store.update_client(client["id"], token_hash="x", revoked_at=1)
    assert store.client(client["id"])["revoked_at"] is None
    with pytest.raises(LookupError, match="No connected tool cl_missing"):
        store.update_client("cl_missing", name="x")


def test_a_new_token_is_returned_and_works_and_a_gone_tool_gets_none(store):
    client, _ = store.add_client("Probe")
    token = store.new_token(client["id"])
    assert token.startswith("fdt_")
    assert store.client_by_token(token)["id"] == client["id"]
    with pytest.raises(LookupError, match="No connected tool cl_missing"):
        store.new_token("cl_missing")
    store.revoke_client(client["id"])
    with pytest.raises(LookupError, match="No connected tool"):
        store.new_token(client["id"])
    assert store.client_by_token(token) is None


def test_the_same_call_has_the_same_hash_whatever_the_key_order():
    assert st.args_hash("c", {"a": 1, "b": 2}) == st.args_hash("c", {"b": 2, "a": 1})
    assert st.args_hash("c", {"a": 1}) != st.args_hash("d", {"a": 1})


def test_ids_have_ten_hex_digits_and_tokens_43_url_safe_characters(store):
    assert len(st.new_id("ap_")) == 13
    client, token = store.add_client("Web", transport="http")
    assert len(client["id"]) == 13 and len(token) == 4 + 43
    assert len(store.new_token(client["id"])) == 4 + 43


def test_revoking_or_deciding_what_isn_t_there_is_refused(store):
    with pytest.raises(LookupError, match="No connected tool cl_missing"):
        store.revoke_client("cl_missing")
    with pytest.raises(LookupError, match="No request ap_missing"):
        store.decide("ap_missing", True)


def test_the_lists_show_50_requests_and_200_uses_by_default(store, monkeypatch):
    client, _ = store.add_client("Probe")
    for i in range(51):
        store.request_approval(client, "ext_cap", "Title", "READ_METADATA", {}, {"n": i})
    assert len(store.approvals()) == 50
    asked = []
    rows = store._rows
    monkeypatch.setattr(store, "_rows", lambda sql, args=(): asked.append(list(args)) or rows(sql, args))
    assert store.approval_uses(0) == []
    assert asked == [[0, 200]]


def test_a_tool_s_workspaces_are_kept_in_the_order_given(store):
    client, _ = store.add_client("Probe")
    changed = store.update_client(client["id"], workspaces=["ws-b", "ws-a"], auto=["Y", "X"])
    assert changed["workspaces"] == ["ws-b", "ws-a"] and changed["auto"] == ["X", "Y"]


def test_arguments_of_exactly_20_000_characters_are_shown_and_longer_ones_cut(store):
    client, _ = store.add_client("Probe")
    exact = {"a": "x" * 19_991}  # '{"a": "' + 19,991 + '"}' is 20,000 characters
    assert store.request_approval(client, "c", "T", "P", {}, exact)["arguments"] == exact
    longer = {"a": "x" * 19_992}
    shown = store.request_approval(client, "c", "T", "P", {}, longer)["arguments"]
    assert list(shown) == ["(too long to show)"] and len(shown["(too long to show)"]) == 20_000
