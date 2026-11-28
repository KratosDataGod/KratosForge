"""Mutation-testing gaps in credential_facts.py (docs/quality/mutation-testing.md, batch 49)."""

import datetime as dt
import types

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from fabric_desktop import credential_facts
from tests.test_run_as import certificate

UTC = dt.timezone.utc


def test_a_status_record_reads_a_pfx_with_the_password_its_auth_names(tmp_path, monkeypatch):
    monkeypatch.setattr(credential_facts, "_cache", {})
    key, cert = certificate(dt.datetime(2027, 4, 1, tzinfo=UTC))
    pfx = tmp_path / "agent.pfx"
    pfx.write_bytes(pkcs12.serialize_key_and_certificates(b"a", key, cert, None,
                                                          serialization.BestAvailableEncryption(b"pw")))
    monkeypatch.setenv("AGENT_PFX_PW", "pw")
    auth = {"type": "service_principal", "certificate_path": str(pfx), "certificate_password_env": "AGENT_PFX_PW"}
    assert credential_facts.facts(auth, None)["expires_at"] == "2027-04-01T00:00:00Z"


def test_a_token_error_is_kept_to_200_characters():
    text = "The sign-in was refused because " + "the request timed out " * 20
    cred = types.SimpleNamespace(last_token_at=None, last_token_error=(0.0, text))
    error = credential_facts.facts({"type": "user"}, cred)["last_token_error"]
    assert error["at"] == "1970-01-01T00:00:00Z"
    assert error["error"] == text[:200]
