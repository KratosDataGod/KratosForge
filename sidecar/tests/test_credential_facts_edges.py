"""A certificate's expiry read from a real self-signed certificate made in tmp_path: PEM and PKCS12, with and
without a password named by an environment variable, a missing file, a file that isn't a certificate, a
PKCS12 that holds no certificate, and the cache that reads a file again only when it changes."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from fabric_desktop import credential_facts

NOT_AFTER = dt.datetime(2031, 3, 4, 5, 6, 7, tzinfo=dt.timezone.utc)
PASSWORD = "made-up-pfx-password"  # pragma: allowlist secret (made up)
PASSWORD_ENV = "KF_TEST_CERT_PASSWORD"  # pragma: allowlist secret (an environment variable name)


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(credential_facts, "_cache", {})


def _cert(not_after: dt.datetime = NOT_AFTER) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "kf-test")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)).not_valid_after(not_after)
            .sign(key, hashes.SHA256()))
    return key, cert


def _pem(path: Path, not_after: dt.datetime = NOT_AFTER) -> Path:
    path.write_bytes(_cert(not_after)[1].public_bytes(serialization.Encoding.PEM))
    return path


def _pfx(path: Path, password: str | None) -> Path:
    key, cert = _cert()
    crypt = (serialization.BestAvailableEncryption(password.encode()) if password
             else serialization.NoEncryption())
    path.write_bytes(pkcs12.serialize_key_and_certificates(b"kf", key, cert, None, crypt))
    return path


def test_a_pem_certificate_gives_its_not_valid_after(tmp_path):
    assert credential_facts.certificate_expiry(str(_pem(tmp_path / "agent.pem"))) == "2031-03-04T05:06:07Z"


@pytest.mark.parametrize("suffix", [".pfx", ".P12"])
def test_a_pkcs12_without_a_password(tmp_path, suffix):
    path = _pfx(tmp_path / f"agent{suffix}", None)
    assert credential_facts.certificate_expiry(str(path)) == "2031-03-04T05:06:07Z"


def test_a_pkcs12_with_the_password_its_environment_variable_holds(tmp_path, monkeypatch):
    monkeypatch.setenv(PASSWORD_ENV, PASSWORD)
    path = _pfx(tmp_path / "agent.pfx", PASSWORD)
    assert credential_facts.certificate_expiry(str(path), PASSWORD_ENV) == "2031-03-04T05:06:07Z"


def test_a_password_protected_pkcs12_without_the_password_is_unknown(tmp_path, monkeypatch):
    monkeypatch.delenv(PASSWORD_ENV, raising=False)
    path = str(_pfx(tmp_path / "agent.pfx", PASSWORD))
    assert credential_facts.certificate_expiry(path) is None
    assert credential_facts.certificate_expiry(path, PASSWORD_ENV) is None  # the variable isn't set


def test_a_pkcs12_with_no_certificate_in_it_is_unknown(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    path = tmp_path / "key-only.pfx"
    path.write_bytes(pkcs12.serialize_key_and_certificates(b"kf", key, None, None, serialization.NoEncryption()))
    assert credential_facts.certificate_expiry(str(path)) is None


def test_a_missing_file_is_unknown_and_not_cached(tmp_path):
    assert credential_facts.certificate_expiry(str(tmp_path / "missing.pem")) is None
    assert credential_facts._cache == {}


def test_a_file_that_is_not_a_certificate_is_unknown_and_logged(tmp_path, caplog):
    path = tmp_path / "garbage.pem"
    path.write_bytes(b"\x00\x01 not a certificate")
    with caplog.at_level("INFO", logger=credential_facts.__name__):
        assert credential_facts.certificate_expiry(str(path)) is None
    assert "Couldn't read the certificate's expiry" in caplog.text


def test_an_unreadable_file_is_unknown(tmp_path, monkeypatch):
    path = _pem(tmp_path / "agent.pem")
    original = Path.read_bytes

    def refuse(self: Path) -> bytes:
        if self == path:
            raise PermissionError(13, "Permission denied", str(self))
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", refuse)
    assert credential_facts.certificate_expiry(str(path)) is None


def test_the_expiry_is_read_again_only_when_the_file_changes(tmp_path, monkeypatch):
    path = _pem(tmp_path / "agent.pem")
    reads: list[Path] = []
    real = credential_facts._read_expiry

    def counting(p: Path, password: str | None) -> str | None:
        reads.append(p)
        return real(p, password)

    monkeypatch.setattr(credential_facts, "_read_expiry", counting)
    first = credential_facts.certificate_expiry(str(path))
    assert credential_facts.certificate_expiry(str(path)) == first == "2031-03-04T05:06:07Z"
    assert len(reads) == 1  # the second ask came from the cache

    _pem(path, dt.datetime(2032, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc))
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 10))  # a new modified time, however coarse the clock
    assert credential_facts.certificate_expiry(str(path)) == "2032-01-02T03:04:05Z"
    assert len(reads) == 2


def test_facts_for_a_certificate_agent_carry_the_expiry_and_not_the_path(tmp_path):
    path = str(_pem(tmp_path / "agent.pem"))
    out = credential_facts.facts({"type": "service_principal", "certificate_path": path}, None)
    assert out == {"kind": "certificate", "expires_at": "2031-03-04T05:06:07Z", "last_token_at": None,
                   "last_token_error": None}
    assert path not in repr(out)
