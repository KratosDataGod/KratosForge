"""engines/snowflake_auth.py held to its exact numbers: a key-pair token lives 59 minutes, it is made
again 120 seconds before it lapses (at that second, not after), and a key file may be 64 KB, not a byte
more. Keys are made here for the test; nothing reaches Snowflake."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from fabric_desktop.engines import snowflake_auth as sa

NOW = 1_800_000_000.0


@pytest.fixture(scope="module")
def pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def test_a_keypair_token_lives_59_minutes(pem):
    key = serialization.load_pem_private_key(pem, None)
    assert sa.JWT_LIFETIME == 3540
    _token, expires = sa.keypair_token("myorg-myaccount", "jdoe", key, NOW)
    assert expires == NOW + 3540


@pytest.mark.parametrize("at, fetches", [(879.0, 1), (879.999, 1), (880.0, 2), (881.0, 2)])
def test_a_token_is_made_again_from_120_seconds_before_it_lapses(at, fetches):
    made: list = []

    def fetch():
        made.append(1)
        return f"tok{len(made)}", 1000.0  # pragma: allowlist secret (a fake token for the test)

    clock = [0.0]
    provider = sa.TokenProvider("myorg-myaccount", "keypair", fetch, clock=lambda: clock[0])
    assert provider.token() == "tok1"
    clock[0] = at
    provider.token()
    assert len(made) == fetches
    assert sa.REFRESH_SECONDS == 120


def test_a_key_file_of_exactly_64_kb_is_read(tmp_path, pem):
    path = tmp_path / "rsa_key.p8"
    path.write_bytes(pem + b"\n" * (64 * 1024 - len(pem)))
    assert path.stat().st_size == 64 * 1024 == sa.MAX_KEY_BYTES
    assert isinstance(sa.load_private_key(str(path), None), rsa.RSAPrivateKey)


def test_a_key_file_one_byte_over_64_kb_is_refused_as_too_large(tmp_path, pem):
    path = tmp_path / "rsa_key.p8"
    path.write_bytes(pem + b"\n" * (64 * 1024 + 1 - len(pem)))
    with pytest.raises(sa.SnowflakeAuthError) as caught:
        sa.load_private_key(str(path), None)
    assert str(caught.value) == "Key-pair sign-in: the private key file is too large to be a key."
