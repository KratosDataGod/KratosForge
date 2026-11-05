"""App Studio's model connections through the engine (AS-06): the organization's policy (appstudio/policy.py)
is checked on every save, test and use, and fails closed. No key is ever read to list connections, and
testing one spends no tokens."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import app_settings, audit, server
from fabric_desktop.appstudio import policy as as_policy
from fabric_desktop.appstudio import providers as prov
from fabric_desktop.appstudio_service import METHODS, AppStudioMixin


class _Svc(AppStudioMixin):
    def __init__(self, root):
        self.root = root


@pytest.fixture
def svc(tmp_path):
    return _Svc(tmp_path / "data")


@pytest.fixture
def policy(monkeypatch, tmp_path):
    """Writes the IT policy file (a dict as JSON, a str as is)."""
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    path = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    path.parent.mkdir(parents=True)

    def write(data):
        path.write_text(data if isinstance(data, str) else json.dumps(data))
    return write


def compat(endpoint="https://llm.contoso.com/v1", **more):
    return {"name": "Contoso LLM", "provider": "openai-compatible", "model": "llama-3", "endpoint": endpoint,
            "key_ref": "credman:KratosForge/contoso", **more}


def azure(**more):
    return {"name": "Azure", "provider": "azure-openai", "model": "gpt-4o", "deployment": "gpt4o",
            "endpoint": "https://contoso.openai.azure.com", "key_ref": "env:AZURE_OPENAI_API_KEY", **more}


def test_methods_are_served_and_only_the_list_skips_the_audit():
    assert METHODS <= server.METHODS
    assert not audit.audited("appstudio_connections")
    for m in ("appstudio_connection_save", "appstudio_connection_delete", "appstudio_connection_test"):
        assert audit.audited(m)
    assert set(as_policy.KEYS) == set(app_settings.APPSTUDIO_KEYS) and set(as_policy.KEYS) <= set(app_settings.POLICY_KEYS)


def test_with_no_policy_the_person_decides(svc, policy):
    got = svc.appstudio_connections()
    assert got["policy"] == {"enabled": True, "providers": None, "hosts": None, "sample_rows": True, "problems": []}
    assert all(p["allowed"] for p in got["providers"]) and got["connections"] == []
    saved = svc.appstudio_connection_save(compat(consent={"context": True, "sample_rows": True}))
    assert saved["capabilities"]["structured_output"] == "json_object"
    listed = svc.appstudio_connections()
    assert listed["default"] == saved["id"] and listed["connections"][0]["refused"] == []
    assert listed["connections"][0]["usage"]["requests"] == 0


def test_saved_connections_hold_key_references_only(svc, policy, tmp_path):
    svc.appstudio_connection_save(azure())
    text = (tmp_path / "data" / "appstudio" / "connections.json").read_text()
    assert "env:AZURE_OPENAI_API_KEY" in text
    with pytest.raises(prov.ProviderError, match="reference"):
        svc.appstudio_connection_save(azure(name="Raw", key_ref="sk-proj-" + "a" * 40))


def test_policy_can_turn_connections_off_but_projects_still_work(svc, policy):
    saved = svc.appstudio_connection_save(compat())
    policy({"appstudio_models": False})
    got = svc.appstudio_connections()
    assert got["policy"]["enabled"] is False and not any(p["allowed"] for p in got["providers"])
    assert got["connections"][0]["refused"] == [as_policy.OFF]
    with pytest.raises(prov.ProviderError, match="turns off"):
        svc.appstudio_connection_save(compat(name="Another"))
    with pytest.raises(prov.ProviderError, match="turns off"):
        svc.appstudio_connection_test(saved["id"])
    assert svc.appstudio_connection_delete(saved["id"]) == {"deleted": saved["id"]}  # removing one is always allowed
    assert svc.appstudio_create("Still works", "sales")["valid"]


def test_policy_limits_providers_and_chosen_hosts(svc, policy):
    policy({"appstudio_providers": ["azure-openai", "openai-compatible"], "appstudio_hosts": ["contoso.openai.azure.com"]})
    svc.appstudio_connection_save(azure())
    with pytest.raises(prov.ProviderError, match="llm.contoso.com"):
        svc.appstudio_connection_save(compat())
    with pytest.raises(prov.ProviderError, match="doesn't allow OpenAI connections"):
        svc.appstudio_connection_save({"name": "OpenAI", "provider": "openai", "model": "gpt-4o",
                                       "key_ref": "env:OPENAI_API_KEY"})
    allowed = {p["id"] for p in svc.appstudio_connections()["providers"] if p["allowed"]}
    assert allowed == {"azure-openai", "openai-compatible"}


def test_hosts_dont_apply_to_fixed_addresses_or_this_pc(svc, policy):
    policy({"appstudio_hosts": []})
    svc.appstudio_connection_save({"name": "Local", "provider": "ollama", "model": "llama3"})
    svc.appstudio_connection_save({"name": "Claude", "provider": "anthropic", "model": "claude-x", "key_ref": "env:ANTHROPIC_API_KEY"})
    with pytest.raises(prov.ProviderError, match="doesn't allow models at"):
        svc.appstudio_connection_save(azure())


def test_policy_can_refuse_sample_rows(svc, policy):
    saved = svc.appstudio_connection_save(compat(consent={"context": True, "sample_rows": True}))
    policy({"appstudio_sample_rows": False})
    assert "sample rows" in svc.appstudio_connections()["connections"][0]["refused"][0]
    with pytest.raises(prov.ProviderError, match="sample rows"):
        svc._appstudio_model(saved["id"])
    svc.appstudio_connection_save({**saved, "consent": {"context": True, "sample_rows": False}})
    assert svc._appstudio_model(saved["id"])["consent"]["sample_rows"] is False


@pytest.mark.parametrize("text, expect", [
    ("{not json", "unreadable"),
    ({"appstudio_models": "yes"}, "appstudio_models"),
    ({"appstudio_providers": "ollama"}, "appstudio_providers"),
    ({"appstudio_hosts": "contoso.com"}, "appstudio_hosts"),
    ({"appstudio_sample_rows": 1}, "appstudio_sample_rows"),
])
def test_policy_fails_closed(svc, policy, text, expect):
    policy(text)
    pol = as_policy.current()
    assert pol["problems"], pol
    if expect == "unreadable":
        assert pol["enabled"] is False
    elif expect == "appstudio_models":
        assert pol["enabled"] is False
    elif expect == "appstudio_providers":
        assert pol["providers"] == []
    elif expect == "appstudio_hosts":
        assert pol["hosts"] == []
    else:
        assert pol["sample_rows"] is False
    if expect != "unreadable":
        assert any(expect in p for p in app_settings.policy_problems())


def test_policy_reports_unknown_providers_and_bad_hosts(policy):
    policy({"appstudio_providers": ["ollama", "skynet"], "appstudio_hosts": ["https://x.com/", "ok.contoso.com"]})
    pol = as_policy.current()
    assert pol["providers"] == ["ollama"] and pol["hosts"] == ["ok.contoso.com"]
    assert any("skynet" in p for p in pol["problems"]) and any("https://x.com/" in p for p in pol["problems"])


class _Resp:
    def __init__(self, doc, status=200):
        self.status_code, self._raw, self.raw = status, json.dumps(doc).encode(), None

    @property
    def content(self):
        return self._raw

    def close(self):
        pass


def test_test_lists_models_with_the_key_in_a_header_only(svc, policy, monkeypatch):
    saved = svc.appstudio_connection_save(compat())
    sent = []

    class _Session:
        def request(self, method, url, headers=None, **kw):
            sent.append((method, url, headers, kw.get("json")))
            return _Resp({"data": [{"id": "llama-3"}, {"id": "mistral"}]})

    monkeypatch.setattr("fabric_desktop.appstudio_service.http_session.session", lambda: _Session())
    monkeypatch.setattr(prov, "resolve_key", lambda ref, reader=None: "the-key" if ref == "credman:KratosForge/contoso" else "")
    got = svc.appstudio_connection_test(saved["id"])
    assert got["model_found"] and got["models"] == ["llama-3", "mistral"]
    (method, url, headers, body), = sent
    assert method == "GET" and url == "https://llm.contoso.com/v1/models" and body is None
    assert headers["Authorization"] == "Bearer the-key" and "the-key" not in url
    assert "the-key" not in json.dumps(got)


def test_missing_connections_say_so(svc, policy):
    with pytest.raises(prov.ProviderError, match="Add a model connection"):
        svc.appstudio_connection_test()
    with pytest.raises(prov.ProviderError, match="isn't there"):
        svc.appstudio_connection_delete("00000000-0000-0000-0000-000000000000")
