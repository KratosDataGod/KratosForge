"""Mutation tests for appstudio/providers.py: the behaviour its other tests left loose. Each provider's
capabilities and key rule, the Azure address rule, limits of 0, a secret-looking field named in the message,
the store listing only valid connections, the token estimate and prices, the warning at exactly 80 %, how a
request is sent (timeouts, no redirects, streamed, decoded, the reply cap), the status boundaries, reply
token counts that are missing, a long connection error, and what validate answers. Nothing reaches a model."""

from __future__ import annotations

import json

import pytest
import requests

from fabric_desktop import redact
from fabric_desktop.appstudio import providers
from fabric_desktop.appstudio.providers import ConnectionStore, ProviderError
from test_appstudio_providers import KEY, Fake, Resp, conn, reader


def test_each_provider_has_the_capabilities_its_adapter_uses():
    caps = {p: providers.capabilities({"provider": p}) for p in providers.PROVIDERS}
    assert caps == {
        "anthropic": {"structured_output": "tool", "tool_calling": True, "streaming": True},
        "openai": {"structured_output": "json_schema", "tool_calling": True, "streaming": True},
        "azure-openai": {"structured_output": "json_schema", "tool_calling": True, "streaming": True},
        "gemini": {"structured_output": "json_mime", "tool_calling": True, "streaming": True},
        "ollama": {"structured_output": "format_schema", "tool_calling": False, "streaming": True},
        "openai-compatible": {"structured_output": "json_object", "tool_calling": False, "streaming": False},
    }


def test_gemini_needs_a_key_reference_and_a_compatible_server_may_go_without():
    assert "Google Gemini needs a key reference" in " ".join(
        providers.problems({"name": "G", "provider": "gemini", "model": "gemini-x"}))
    assert providers.problems({"name": "C", "provider": "openai-compatible", "model": "m1",
                               "endpoint": "https://llm.contoso.com/v1"}) == []


def test_an_azure_address_elsewhere_is_refused():
    assert providers.endpoint_problem("azure-openai", "https://contoso.example.com") == (
        "An Azure OpenAI address ends in .openai.azure.com or .cognitiveservices.azure.com.")
    assert providers.endpoint_problem("azure-openai", "https://contoso.cognitiveservices.azure.com") == ""


def test_limits_of_zero_and_fractions_are_allowed():
    c = {"name": "C", "provider": "openai-compatible", "model": "m1", "endpoint": "https://llm.contoso.com/v1",
         "limits": {"monthly_limit": 0, "input_price_per_million": 0.5, "output_price_per_million": 0}}
    assert providers.problems(c) == []


def test_a_secret_looking_field_is_named_by_its_first_forty_characters():
    field = "f" * 50
    c = {"name": "C", "provider": "openai-compatible", "model": "m1", "endpoint": "https://llm.contoso.com/v1",
         field: KEY}
    assert f"{'f' * 40} looks like a key or password. Keep keys in Windows Credential Manager." in providers.problems(c)


def test_the_store_lists_only_connections_that_are_still_valid(tmp_path):
    good = conn()
    (tmp_path / "connections.json").write_text(json.dumps({
        "connections": [good, "not a connection", dict(good, id="x", name="Bad", provider="nobody")],
        "default": good["id"]}))
    listed = ConnectionStore(tmp_path).list()
    assert [c["name"] for c in listed["connections"]] == ["My Claude"]


@pytest.mark.parametrize(("text", "tokens"), [("", 1), ("a", 1), ("abcd", 1), ("abcde", 2), ("x" * 400, 100)])
def test_tokens_are_estimated_at_four_characters_each_and_never_none(text, tokens):
    assert providers.estimate_tokens(text) == tokens


def test_cost_is_priced_per_million_tokens():
    c = conn(limits={"input_price_per_million": 2.0, "output_price_per_million": 4.0})
    assert providers.cost(c, 1_000_000, 500_000) == 4.0


def test_the_default_output_cap_is_4096_tokens():
    out = providers.spend_check(conn(), {"cost": 0.0}, 10)
    assert out["max_output_tokens"] == 4096
    store, session = ConnectionStore.__new__(ConnectionStore), Fake(Resp(200, {
        "content": [{"type": "tool_use", "input": {"pages": []}}], "usage": {"input_tokens": 1, "output_tokens": 1}}))
    store.usage = lambda conn_id: {"cost": 0.0}
    store.record = lambda *a: None
    providers.complete(conn(), store, session, "plan", "x", {"type": "object"}, key_reader=reader)
    assert session.calls[0]["json"]["max_tokens"] == 4096


def test_spend_at_exactly_eighty_percent_of_the_limit_warns():
    c = conn(limits={"monthly_limit": 100.0, "input_price_per_million": 1_000_000.0, "output_price_per_million": 0.0})
    out = providers.spend_check(c, {"cost": 79.0}, 1)
    assert out["warning"] == "This month's spend is near the limit (80.00 of 100.00)."


class _Raw:
    def __init__(self, body):
        self.body, self.asked = body, []

    def read(self, n, decode_content=False):
        self.asked.append((n, decode_content))
        return self.body[:n]


class _Resp:
    def __init__(self, status, body):
        self.status_code, self.raw = status, _Raw(body)

    def close(self):
        pass


def test_a_request_is_sent_with_timeouts_no_redirects_streamed_and_decoded():
    reply = _Resp(200, b'{"ok": 1}')
    session = Fake(reply)
    assert providers._send(session, "GET", "https://api.openai.com/v1/models", {"x": "y"}, None, "k") == {"ok": 1}
    [call] = session.calls
    assert call["timeout"] == (10, 120) and call["allow_redirects"] is False and call["stream"] is True
    assert call["headers"] == {"Content-Type": "application/json", "x": "y"}
    assert reply.raw.asked == [(2 * 1024 * 1024 + 1, True)]


def test_a_reply_one_byte_over_two_megabytes_is_too_large():
    with pytest.raises(ProviderError, match="^The model's reply was too large.$"):
        providers._send(Fake(Resp(200, b"x" * (2 * 1024 * 1024 + 1))), "GET", "https://h/x", {}, None, "")


@pytest.mark.parametrize(("status", "said"), [
    (300, "The model service answered with a redirect, which isn't followed."),
    (399, "The model service answered with a redirect, which isn't followed."),
    (400, "The model service said 400: bad"),
])
def test_status_boundaries(status, said):
    with pytest.raises(ProviderError) as err:
        providers._send(Fake(Resp(status, b"bad")), "GET", "https://h/x", {}, None, "")
    assert str(err.value) == said


def test_a_long_connection_error_is_cut_to_two_hundred_characters():
    text = "Max retries exceeded " + "x" * 400
    with pytest.raises(ProviderError) as err:
        providers._send(Fake(requests.ConnectionError(text)), "GET", "https://h/x", {}, None, "")
    assert str(err.value) == "Couldn't reach the model: " + redact.one_line(text, 200)
    assert len(str(err.value)) == len("Couldn't reach the model: ") + 200


def test_missing_token_counts_read_as_zero():
    assert providers._parse("openai", {"choices": [{"message": {"content": "{}"}}]}) == ({}, 0, 0)
    assert providers._parse("ollama", {"message": {"content": "{}"}}) == ({}, 0, 0)
    assert providers._parse("openai", {"choices": [{"message": {"content": "{}"}}],
                                       "usage": {"prompt_tokens": 3}}) == ({}, 3, 0)


def test_validate_says_ok_and_finds_an_azure_deployment_by_id_alone():
    out = providers.validate(conn("azure-openai"), Fake(Resp(200, {"id": "gpt-app"})), key_reader=reader)
    assert out["ok"] is True and out["model_found"] is True and out["models"] == ["gpt-app"]
    out = providers.validate(conn("azure-openai"), Fake(Resp(200, {"model": "gpt-4o"})), key_reader=reader)
    assert out["models"] == ["gpt-app"]
    out = providers.validate(conn("azure-openai"), Fake(Resp(200, {})), key_reader=reader)
    assert out["models"] == [] and out["model_found"] is False
