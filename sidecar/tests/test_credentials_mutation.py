"""Agent credentials (credentials.py): what the mutation run (batch 128, docs/quality/mutation-testing.md)
showed no test pinned. Whether the service principal's key file is open to every account answers True or
False, never None (only "others may read" counts), a refusal says how long the agent waits, and a PFX without its certificate is refused in words."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from fabric_desktop import credentials


def test_no_key_file_or_not_a_service_principal_is_false(tmp_path):
    assert credentials.key_file_open({"type": "service_principal"}) is False
    assert credentials.key_file_open({"type": "managed_identity", "certificate_path": str(tmp_path)}) is False


def test_a_key_file_that_cant_be_read_is_false(tmp_path):
    missing = tmp_path / "missing.pem"
    assert credentials.key_file_open({"type": "service_principal", "certificate_path": str(missing)}) is False


def test_windows_is_false(tmp_path, monkeypatch):
    key = tmp_path / "agent.pem"
    key.write_bytes(b"x")
    monkeypatch.setattr(credentials, "os", SimpleNamespace(name="nt", environ=os.environ))
    assert credentials.key_file_open({"type": "service_principal", "certificate_path": str(key)}) is False


@pytest.mark.skipif(sys.platform == "win32", reason="file modes are POSIX")
@pytest.mark.parametrize("mode, open_", [(0o644, True), (0o640, False), (0o641, False)])
def test_a_key_file_every_account_can_read_is_true(tmp_path, mode, open_):
    key = tmp_path / "agent.pem"
    key.write_bytes(b"x")
    key.chmod(mode)
    assert credentials.key_file_open({"type": "service_principal", "certificate_path": str(key)}) is open_


def test_a_pfx_without_its_certificate_is_refused(tmp_path):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import NoEncryption, pkcs12

    key = ec.generate_private_key(ec.SECP256R1())
    pfx = tmp_path / "agent.pfx"
    pfx.write_bytes(pkcs12.serialize_key_and_certificates(b"agent", key, None, None, NoEncryption()))
    with pytest.raises(ValueError, match="has no private key and certificate"):
        credentials._certificate(pfx, None)


def test_a_refusal_says_the_agent_waits_five_minutes():
    text = credentials.refusal("service principal c", {"error_description": "AADSTS7000222: The secret expired."})
    assert "(AADSTS7000222)" in text and "doesn't ask Entra again for 5 minutes at a time" in text
    assert credentials.refusal("x", {"error_description": "AADSTS50196: loop"}) == ""
