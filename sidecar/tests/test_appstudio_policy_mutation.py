"""Mutation batch 6: App Studio's model-connection policy (appstudio/policy.py) at its edges.

The policy file is replaced by monkeypatching app_settings.read_policy, so no real file is read."""

from __future__ import annotations

import pytest

from fabric_desktop import app_settings
from fabric_desktop.appstudio import policy as P


@pytest.fixture
def file(monkeypatch):
    state: dict = {"data": {}, "error": None}
    monkeypatch.setattr(app_settings, "read_policy", lambda: (dict(state["data"]), state["error"]))
    return state


def test_an_unreadable_file_turns_everything_off_including_sample_rows(file):
    file["error"] = "policy.json can't be read"
    pol = P.current()
    assert pol == {"enabled": False, "providers": [], "hosts": [], "sample_rows": False,
                   "problems": ["policy.json can't be read"], "set": list(P.KEYS)}


def test_a_provider_list_given_as_one_string_allows_no_provider(file):
    file["data"] = {"appstudio_providers": "openai"}  # a string, not a list: not split into letters
    pol = P.current()
    assert pol["providers"] == []
    assert pol["problems"] == ["appstudio_providers should be a list of provider ids, so no provider is allowed"]


def test_a_host_list_given_as_one_string_allows_no_host(file):
    file["data"] = {"appstudio_hosts": "contoso.openai.azure.com"}
    pol = P.current()
    assert pol["hosts"] == []
    assert pol["problems"] == ["appstudio_hosts should be a list of host names, so no chosen address is allowed"]


def test_unknown_providers_are_named_cut_to_40_characters_and_at_most_five(file):
    names = [f"{c * 41}" for c in "abcdef"]
    file["data"] = {"appstudio_providers": names}
    problem = P.current()["problems"][0]
    shown = problem.split("doesn't have: ")[1].split(" (it has")[0].split(", ")
    assert shown == [c * 40 for c in "abcde"]


def test_bad_hosts_are_named_cut_to_60_characters_and_at_most_five(file):
    names = [f"{c * 61}!" for c in "abcdef"]
    file["data"] = {"appstudio_hosts": names}
    problem = P.current()["problems"][0]
    shown = problem.split("skipped: ")[1].split(", ")
    assert shown == [c * 60 for c in "abcde"]


def test_a_refused_provider_names_the_allowed_ones_or_none():
    base = {"enabled": True, "hosts": None, "sample_rows": True}
    some = P.refusals({"provider": "openai"}, dict(base, providers=["anthropic", "gemini"]))
    assert some == ["Your organization's policy doesn't allow OpenAI connections (allowed: anthropic, gemini)."]
    none = P.refusals({"provider": "openai"}, dict(base, providers=[]))
    assert none == ["Your organization's policy doesn't allow OpenAI connections."]


def test_an_unknown_providers_label_is_cut_to_40_characters():
    pol = {"enabled": True, "providers": [], "hosts": None, "sample_rows": True}
    out = P.refusals({"provider": "x" * 50}, pol)
    assert out == [f"Your organization's policy doesn't allow {'x' * 40} connections."]


def test_a_refused_host_names_the_allowed_ones_or_none():
    base = {"enabled": True, "providers": None, "sample_rows": True}
    conn = {"provider": "azure-openai", "endpoint": "https://evil.example.com/openai"}
    some = P.refusals(conn, dict(base, hosts=["contoso.openai.azure.com"]))
    assert some == [("Your organization's policy doesn't allow models at evil.example.com "
                     "(allowed: contoso.openai.azure.com).")]
    none = P.refusals(conn, dict(base, hosts=[]))
    assert none == ["Your organization's policy doesn't allow models at evil.example.com."]
    allowed = P.refusals(dict(conn, endpoint="https://CONTOSO.openai.azure.com/x"),
                         dict(base, hosts=["contoso.openai.azure.com"]))
    assert allowed == []
