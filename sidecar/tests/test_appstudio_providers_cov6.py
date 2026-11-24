"""App Studio's model connections (appstudio/providers.py), the paths the other tests leave: an Ollama address that
names another machine, connections that aren't objects or are missing what their provider needs, picking and
deleting one of several connections, a usage file that isn't an object, spend well under the limit, context of
the wrong kind or too large, an error from a server with no key, and a reply that is JSON but not an object.
Nothing reaches a model: the HTTP session is a stand-in."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.appstudio import providers
from fabric_desktop.appstudio.providers import ConnectionStore, ProviderError
from test_appstudio_providers import Fake, Resp, conn


@pytest.mark.parametrize(("endpoint", "said"), [
    ("http://localhost:11434", ""),
    ("http://127.0.0.1:11434", ""),
    ("http://gpu-box:11434", "Ollama connections only reach this PC (http://localhost:11434)."),
    ("http://10.0.0.5:11434", "Ollama connections only reach this PC (http://localhost:11434)."),
])
def test_ollama_only_reaches_this_pc(endpoint, said):
    assert providers.endpoint_problem("ollama", endpoint) == said


def test_a_connection_that_isnt_an_object_is_refused():
    assert providers.problems(["anthropic"]) == ["A connection must be an object."]


def _raw(provider, **extra):
    base = {"name": "Mine", "provider": provider, "model": "m1"}
    return base | extra


def test_what_each_provider_needs_is_named():
    assert "Ollama on this PC doesn't use a key." in providers.problems(
        _raw("ollama", endpoint="http://localhost:11434", key_ref="env:KRATOSFORGE_OLLAMA"))
    assert "Azure OpenAI needs the deployment name." in providers.problems(
        _raw("azure-openai", endpoint="https://contoso.openai.azure.com", key_ref="env:AZURE_OPENAI_API_KEY"))
    assert "Azure OpenAI needs the deployment name." in providers.problems(
        _raw("azure-openai", endpoint="https://contoso.openai.azure.com", key_ref="env:AZURE_OPENAI_API_KEY",
             deployment="-bad"))
    assert providers.problems(_raw("ollama", endpoint="http://localhost:11434", limits=[100])) == [
        "limits must be an object."]


def test_one_of_several_connections_is_found_and_deleting_another_keeps_the_default(tmp_path):
    store = ConnectionStore(tmp_path)
    first = store.save(conn("anthropic"))
    second = store.save(conn("openai", name="My GPT"))
    assert store.get(second["id"])["name"] == "My GPT"
    assert store.get()["id"] == first["id"], "the first saved is the default"
    store.delete(second["id"])
    listed = store.list()
    assert [c["id"] for c in listed["connections"]] == [first["id"]] and listed["default"] == first["id"]
    with pytest.raises(ProviderError, match="^That model connection isn't there any more.$"):
        store.get(second["id"])


def test_a_usage_file_that_isnt_an_object_starts_again(tmp_path):
    (tmp_path / "usage.json").write_text(json.dumps(["not", "a", "month"]))
    store = ConnectionStore(tmp_path)
    store.record("c1", 100, 20, 0.5)
    got = store.usage("c1")
    assert (got["input_tokens"], got["output_tokens"], got["cost"], got["requests"]) == (100, 20, 0.5, 1)


def test_spend_well_under_the_limit_has_no_warning():
    c = conn(limits={"monthly_limit": 100.0, "input_price_per_million": 1.0, "output_price_per_million": 1.0})
    out = providers.spend_check(c, {"cost": 1.0}, 1000)
    assert out["warning"] == "" and out["estimated_cost"] == round((1000 + providers.DEFAULT_MAX_OUTPUT_TOKENS) / 1e6, 4)


def test_context_must_be_metadata_or_rows_and_fit(monkeypatch):
    c = conn()
    with pytest.raises(ProviderError, match="^Context must be metadata or rows.$"):
        providers.build_messages(c, "plan", "x", {"kind": "files", "data": {}})
    monkeypatch.setattr(providers, "MAX_CONTEXT_CHARS", 20)
    with pytest.raises(ProviderError, match="^The context is too large to send; choose fewer tables.$"):
        providers.build_messages(c, "plan", "x", {"kind": "metadata", "data": {"tables": ["a" * 30]}})
    system, user = providers.build_messages(c, "plan", "x", {"kind": "metadata", "data": {"t": 1}})
    assert system.endswith("plan") and '<DATA kind="metadata">' in user


def test_an_error_from_a_server_with_no_key_is_shown_as_it_came():
    with pytest.raises(ProviderError, match="^The model service said 404: model llama9 not found$"):
        providers._send(Fake(Resp(404, b"model llama9 not found")), "POST", "http://localhost:11434/api/chat", {},
                        {}, "")


def test_a_reply_that_is_json_but_not_an_object_is_refused():
    with pytest.raises(ProviderError, match="^The model service's reply wasn't a JSON object.$"):
        providers._send(Fake(Resp(200, [1, 2])), "GET", "https://api.anthropic.com/v1/models", {}, None, "k")
