"""App Studio model connections (appstudio/providers): the remaining edges. Validation of each odd field, the
store's default and usage files, contexts that can't be sent, and replies that aren't usable. A fake HTTP
session stands in for every model; nothing leaves this PC."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.appstudio import providers
from fabric_desktop.appstudio.providers import ConnectionStore, ProviderError


class Resp:
    def __init__(self, status, body):
        self.status_code, self.raw, self.content = status, None, body if isinstance(body, bytes) else json.dumps(body).encode()

    def close(self):
        pass


class Session:
    def __init__(self, resp):
        self.resp = resp

    def request(self, *a, **k):
        return self.resp


def conn(**over):
    return {"name": "Local", "provider": "openai-compatible", "model": "m1", "endpoint": "https://llm.example.com/v1",
            "consent": {"context": True, "sample_rows": False}} | over


def test_ollama_reaches_only_this_pc_by_name_or_loopback_address():
    assert providers.endpoint_problem("ollama", "http://127.0.0.1:11434") == ""
    assert providers.endpoint_problem("ollama", "http://gpu-box:11434") == \
        "Ollama connections only reach this PC (http://localhost:11434)."


def test_each_odd_field_is_named_when_saving():
    assert providers.problems(["not", "a", "connection"]) == ["A connection must be an object."]
    assert providers.problems({"name": "O", "provider": "ollama", "model": "llama3", "endpoint": "http://localhost:11434",
                               "key_ref": "env:KRATOSFORGE_X"}) == ["Ollama on this PC doesn't use a key."]
    azure = {"name": "Az", "provider": "azure-openai", "model": "gpt", "endpoint": "https://x.openai.azure.com",
             "key_ref": "env:AZURE_OPENAI_API_KEY"}
    assert providers.problems(azure) == ["Azure OpenAI needs the deployment name."]
    assert providers.problems(conn(limits=[1])) == ["limits must be an object."]


def test_the_store_finds_a_connection_past_others_and_keeps_the_default_when_another_goes(tmp_path):
    store = ConnectionStore(tmp_path)
    first = store.save(conn(name="First"))
    second = store.save(conn(name="Second"))
    assert store.get(second["id"])["name"] == "Second"
    store.delete(second["id"])
    assert store.list()["default"] == first["id"] and [c["name"] for c in store.list()["connections"]] == ["First"]


def test_usage_kept_in_a_file_that_isnt_an_object_starts_again(tmp_path):
    (tmp_path / "usage.json").write_text("[1, 2]", encoding="utf-8")
    store = ConnectionStore(tmp_path)
    store.record("c1", 10, 5, 0.25)
    month = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert list(month.values()) == [{"c1": {"input_tokens": 10, "output_tokens": 5, "cost": 0.25, "requests": 1}}]


def test_spend_well_under_the_limit_carries_no_warning():
    c = conn(limits={"monthly_limit": 100, "input_price_per_million": 1, "output_price_per_million": 1})
    got = providers.spend_check(c, {"cost": 1.0}, 1000)
    assert got["warning"] == "" and got["estimated_cost"] == round((1000 + 4096) / 1_000_000, 4)


def test_a_context_of_another_kind_or_too_large_isnt_sent(monkeypatch):
    with pytest.raises(ProviderError, match="^Context must be metadata or rows.$"):
        providers.build_messages(conn(), "i", "r", {"kind": "files", "data": {}})
    monkeypatch.setattr(providers, "MAX_CONTEXT_CHARS", 10)
    with pytest.raises(ProviderError, match="^The context is too large to send; choose fewer tables.$"):
        providers.build_messages(conn(), "i", "r", {"kind": "metadata", "data": {"tables": ["a" * 20]}})


def test_an_error_from_a_server_without_a_key_is_shown_as_it_said():
    with pytest.raises(ProviderError, match="^The model service said 500: model m1 is loading$"):
        providers._send(Session(Resp(500, b"model m1 is loading")), "GET", "https://llm.example.com/v1/models", {}, None, "")


def test_a_reply_that_is_json_but_not_an_object_is_refused():
    with pytest.raises(ProviderError, match="^The model service's reply wasn't a JSON object.$"):
        providers._send(Session(Resp(200, [1, 2])), "GET", "https://llm.example.com/v1/models", {}, None, "")
