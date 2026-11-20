"""Snowflake sign-in for SQL API calls (SF-001): key-pair tokens as Snowflake documents them, PATs from
references, refused methods by name, and no secret in any error."""

from __future__ import annotations

import base64
import hashlib

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from fabric_desktop.engines import snowflake_auth as sa

NOW = 1_800_000_000.0


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write(tmp_path, key, passphrase=None, name="rsa_key.p8"):
    enc = serialization.BestAvailableEncryption(passphrase.encode()) if passphrase else serialization.NoEncryption()
    path = tmp_path / name
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc))
    return str(path)


def _conn(**cred):
    return {"platform": "snowflake", "account": "myorg-myaccount", "user": "jdoe", "credential": cred}


def test_a_keypair_token_names_the_account_user_and_key_fingerprint(key):
    token, expires = sa.keypair_token("myorg-myaccount", "jdoe", key, NOW)
    claims = jwt.decode(token, key.public_key(), algorithms=["RS256"], options={"verify_exp": False, "verify_iat": False})
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    fp = "SHA256:" + base64.b64encode(hashlib.sha256(der).digest()).decode()
    assert claims == {"iss": f"MYORG-MYACCOUNT.JDOE.{fp}", "sub": "MYORG-MYACCOUNT.JDOE",
                      "iat": int(NOW), "exp": int(NOW) + sa.JWT_LIFETIME}
    assert expires == NOW + sa.JWT_LIFETIME and sa.JWT_LIFETIME < 3600


def test_an_account_locator_with_a_region_uses_the_locator_alone():
    assert sa.jwt_account("xy12345.east-us-2.azure") == "XY12345"
    with pytest.raises(sa.SnowflakeAuthError, match="account identifier"):
        sa.jwt_account("https://x.snowflakecomputing.com")


def test_the_provider_reads_an_encrypted_key_and_sends_keypair_headers(tmp_path, key):
    path = _write(tmp_path, key, "s3cret-pass")
    provider = sa.provider_for(_conn(method="keypair", private_key_file=path, passphrase="env:SF_PASS"),
                               environ={"SF_PASS": "s3cret-pass"}, clock=lambda: NOW)
    headers = provider.headers()
    assert headers["X-Snowflake-Authorization-Token-Type"] == "KEYPAIR_JWT"
    assert jwt.decode(headers["Authorization"][7:], key.public_key(), algorithms=["RS256"],
                      options={"verify_exp": False, "verify_iat": False})["sub"] == "MYORG-MYACCOUNT.JDOE"


def test_a_keypair_token_is_made_again_only_near_expiry(tmp_path, key):
    path, now = _write(tmp_path, key), [NOW]
    provider = sa.provider_for(_conn(method="keypair", private_key_file=path), clock=lambda: now[0])
    first = provider.token()
    now[0] += 60
    assert provider.token() == first
    now[0] = NOW + sa.JWT_LIFETIME - sa.REFRESH_SECONDS + 1
    assert provider.token() != first


@pytest.mark.parametrize("passphrase, env, message", [
    (None, {}, "encrypted and the connection names no passphrase"),
    ("env:SF_PASS", {"SF_PASS": "wrong-one"}, "couldn't be opened"),
    ("env:SF_PASS", {}, "isn't set on this PC"),
])
def test_key_problems_say_what_to_check_without_the_passphrase(tmp_path, key, passphrase, env, message):
    path = _write(tmp_path, key, "right-pass")
    cred = {"method": "keypair", "private_key_file": path} | ({"passphrase": passphrase} if passphrase else {})
    provider = sa.provider_for(_conn(**cred), environ=env, clock=lambda: NOW)
    with pytest.raises(sa.SnowflakeAuthError, match=message) as err:
        provider.token()
    assert "right-pass" not in str(err.value) and "wrong-one" not in str(err.value)


def test_a_key_that_isnt_rsa_or_isnt_a_key_or_isnt_there_is_refused(tmp_path):
    ec_key = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(sa.SnowflakeAuthError, match="RSA key"):
        sa.load_private_key(_write(tmp_path, ec_key, name="ec.p8"), None)
    junk = tmp_path / "junk.p8"
    junk.write_text("not a key")
    with pytest.raises(sa.SnowflakeAuthError, match="PEM private key"):
        sa.load_private_key(str(junk), None)
    with pytest.raises(sa.SnowflakeAuthError, match="can't be read"):
        sa.load_private_key(str(tmp_path / "missing.p8"), None)
    with pytest.raises(sa.SnowflakeAuthError, match="full path"):
        sa.load_private_key("relative.p8", None)


def test_a_huge_file_isnt_read_as_a_key(tmp_path):
    big = tmp_path / "big.p8"
    big.write_bytes(b"x" * (sa.MAX_KEY_BYTES + 1))
    with pytest.raises(sa.SnowflakeAuthError, match="too large"):
        sa.load_private_key(str(big), None)


def test_a_pat_comes_from_its_reference_with_its_own_token_type():
    provider = sa.provider_for(_conn(method="pat", secret="env:SF_PAT"), environ={"SF_PAT": "pat-value-123"})
    assert provider.headers() == {"Authorization": "Bearer pat-value-123",
                                  "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN"}


def test_a_refused_pat_is_never_sent_again_but_a_keypair_token_is_made_anew(tmp_path, key):
    pat = sa.provider_for(_conn(method="pat", secret="env:SF_PAT"), environ={"SF_PAT": "pat-value-123"})
    pat.drop(pat.token())
    with pytest.raises(sa.SnowflakeAuthError, match="refused this programmatic access token") as err:
        pat.token()
    assert "pat-value-123" not in str(err.value)
    now = [NOW]
    kp = sa.provider_for(_conn(method="keypair", private_key_file=_write(tmp_path, key)), clock=lambda: now[0])
    first = kp.token()
    kp.drop(first)
    now[0] += 1
    assert kp.token() != first


@pytest.mark.parametrize("cred, message", [
    ({"method": "oauth-local"}, "Browser sign-in to Snowflake isn't available"),
    ({"method": "cli-connection", "connection": "dev"}, "Snowflake CLI connection isn't available"),
    ({"method": "pat"}, "needs a secret reference"),
    ({}, "no Snowflake sign-in method"),
])
def test_methods_not_offered_yet_or_incomplete_are_refused_by_name(cred, message):
    with pytest.raises(sa.SnowflakeAuthError, match=message):
        sa.provider_for(_conn(**cred))


def test_keypair_needs_a_user():
    with pytest.raises(sa.SnowflakeAuthError, match="user name"):
        sa.keypair_token("myorg-myaccount", "", rsa.generate_private_key(public_exponent=65537, key_size=2048), NOW)
