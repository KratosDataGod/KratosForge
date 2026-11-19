"""Remote compute approval edges the main tests don't pin down (mutation batch 130,
docs/quality/mutation-testing.md): a policy approval's expiry, the exact moment an approval runs
out, that a checked approval is handed back, and that an approval can't be changed."""

import dataclasses
import datetime as dt

import pytest

from fabric_desktop.engines import authorization as auth
from fabric_desktop.engines import registry

NOW = dt.datetime(2026, 11, 18, 12, tzinfo=dt.timezone.utc)


def _remote():
    return registry.get("snowflake-sql")


def test_a_policy_approval_lasts_the_confirmation_time_from_now():
    got = auth.from_policy({"remote_compute": "policy", "allowed_remote_engines": ["snowflake-sql"]},
                           "work", "snowflake-sql", "cluster-1", now=NOW)
    assert got.expires_at == NOW + dt.timedelta(minutes=auth.CONFIRM_MINUTES)
    assert got.by == "policy:work" and got.reason == "policy"


def test_an_approval_runs_out_at_exactly_its_expiry_and_is_good_just_before():
    approval = auth.confirmed("snowflake-sql", "cluster-1", "me", now=NOW)
    end = approval.expires_at
    assert auth.require(_remote(), "cluster-1", approval, now=end - dt.timedelta(microseconds=1)) is approval
    with pytest.raises(auth.RemoteNotAuthorized, match="expired"):
        auth.require(_remote(), "cluster-1", approval, now=end)


def test_an_approval_cant_be_changed_after_it_is_given():
    approval = auth.confirmed("snowflake-sql", "cluster-1", "me", now=NOW)
    with pytest.raises(dataclasses.FrozenInstanceError):
        approval.target = "cluster-2"  # type: ignore[misc]
