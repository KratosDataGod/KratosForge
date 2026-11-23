"""Snowflake sign-in tokens: the paths the other tests leave out (coverage pass 20). A sign-in that
produces nothing is refused by name, and dropping a token that isn't the current one keeps the current."""

from __future__ import annotations

import pytest

from fabric_desktop.engines import snowflake_auth as sa


@pytest.mark.parametrize("method", ["pat", "keypair"])
def test_a_sign_in_that_produces_no_token_is_refused_and_nothing_is_kept(method):
    provider = sa.TokenProvider("myorg-myaccount", method, lambda: ("", None))
    with pytest.raises(sa.SnowflakeAuthError, match=r"^No Snowflake credential was produced\.$"):
        provider.headers()
    with pytest.raises(sa.SnowflakeAuthError):  # asked again, not served from a cache
        provider.token()


def test_dropping_a_token_that_isnt_the_current_one_keeps_the_current_one():
    made = iter([("first", None), ("second", None)])
    provider = sa.TokenProvider("myorg-myaccount", "keypair", lambda: next(made))
    assert provider.token() == "first"
    provider.drop("something-else")
    assert provider.token() == "first"  # not made again
    provider.drop("first")
    assert provider.token() == "second"


def test_a_pat_dropped_while_another_is_current_is_still_never_sent_again():
    made = iter([("current", None), ("old", None)])
    provider = sa.TokenProvider("myorg-myaccount", "pat", lambda: next(made))
    assert provider.token() == "current"
    provider.drop("old")
    assert provider.token() == "current"
    provider.drop("current")
    with pytest.raises(sa.SnowflakeAuthError, match="refused this programmatic access token earlier"):
        provider.token()
