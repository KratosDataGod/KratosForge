"""Snowflake sign-in (engines/snowflake_auth.py), mutation batch 350: the token's lifetime and refresh
window as numbers, the refresh at exactly its edge, the key file's size limit at exactly its edge, and
an empty credential refused."""

from __future__ import annotations

import pytest
from fabric_desktop.engines import snowflake_auth as sa

NOW = 1_800_000_000.0


def test_a_keypair_token_lives_59_minutes():
    assert sa.JWT_LIFETIME == 3540


def test_the_token_is_made_again_from_exactly_two_minutes_before_it_lapses():
    assert sa.REFRESH_SECONDS == 120
    now, made = [NOW], []

    def fetch():
        made.append(now[0])
        return f"tok-{len(made)}", NOW + 3540

    provider = sa.TokenProvider("myorg-myaccount", "keypair", fetch, clock=lambda: now[0])
    assert provider.token() == "tok-1"
    now[0] = NOW + 3540 - 121  # still more than two minutes left: kept
    assert provider.token() == "tok-1"
    now[0] = NOW + 3540 - 120  # exactly two minutes left: made again
    assert provider.token() == "tok-2"


def test_a_fetch_that_gives_no_token_is_refused_not_sent():
    provider = sa.TokenProvider("myorg-myaccount", "pat", lambda: ("", None))
    with pytest.raises(sa.SnowflakeAuthError, match="No Snowflake credential was produced"):
        provider.headers()


def test_a_key_file_of_exactly_64_kib_is_read_and_one_byte_more_is_too_large(tmp_path):
    assert sa.MAX_KEY_BYTES == 64 * 1024
    at_limit = tmp_path / "at_limit.p8"
    at_limit.write_bytes(b"x" * (64 * 1024))
    with pytest.raises(sa.SnowflakeAuthError, match="couldn't be opened"):  # read, then not a PEM key
        sa.load_private_key(str(at_limit), None)
    over = tmp_path / "over.p8"
    over.write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(sa.SnowflakeAuthError, match="too large to be a key"):
        sa.load_private_key(str(over), None)
