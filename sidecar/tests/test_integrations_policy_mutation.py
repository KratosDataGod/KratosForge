"""Mutation batch 6: the IT policy for external AI tools (integrations/policy.py) at its edges.

The policy file is replaced by monkeypatching app_settings.read_policy, so no real file is read."""

from __future__ import annotations

import pytest

from fabric_desktop import app_settings
from fabric_desktop.integrations import policy as P
from fabric_desktop.integrations.registry import PERMISSIONS


@pytest.fixture
def file(monkeypatch):
    state: dict = {"data": {}, "error": None}
    monkeypatch.setattr(app_settings, "read_policy", lambda: (dict(state["data"]), state["error"]))
    return state


def test_an_unreadable_file_turns_off_every_part_including_editors(file):
    file["error"] = "policy.json can't be read"
    assert P.current() == {"enabled": False, "max_permissions": [], "http": False, "editors": False,
                           "extensions": [], "problems": ["policy.json can't be read"], "set": list(P.KEYS)}


def test_with_no_keys_the_defaults_allow_http_and_editors(file):
    assert P.current() == {"enabled": None, "max_permissions": list(PERMISSIONS), "http": True, "editors": True,
                           "extensions": None, "problems": [], "set": []}


@pytest.mark.parametrize("value", [True, False])
def test_editors_follows_a_true_or_false_value(file, value):
    file["data"] = {"integrations_editors": value}
    pol = P.current()
    assert pol["editors"] is value and pol["problems"] == []


def test_editors_of_the_wrong_kind_is_off_and_said(file):
    file["data"] = {"integrations_editors": "yes"}
    pol = P.current()
    assert pol["editors"] is False
    assert pol["problems"] == ["integrations_editors should be true or false"]
