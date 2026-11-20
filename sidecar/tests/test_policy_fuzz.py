"""Junk policy values, from the policy file and from Group Policy, never break reading settings and never
loosen anything: a restriction given a value of the wrong kind takes its most restrictive value, an
out-of-range number never applies, and each problem is said. Seeded, so a failure repeats."""

from __future__ import annotations

import json
import random

import pytest

from fabric_desktop import app_settings, registry_policy

from test_policy_loosening import gp, policy  # noqa: F401 - fixtures

SEEDS = range(40)
JUNK = [None, True, False, 0, 1, -1, 2, 7, 2**31, 2**63, -2**63, 1.5, float("1e308"), "", "0", "1", "true",
        "yes", "x" * 5000, "\x00", "퟿", [], [1], ["a", 2], {}, {"a": 1}, [[]], "https://evil.example"]
REG_KINDS = [registry_policy.REG_SZ, registry_policy.REG_EXPAND_SZ, registry_policy.REG_DWORD, 3, 7, 11, 0]
BOOL_RESTRICTIONS = sorted(k for k, v in app_settings.RESTRICTED.items() if isinstance(v, bool))


def _junk(rng):
    return rng.choice(JUNK)


@pytest.mark.parametrize("seed", SEEDS)
def test_junk_in_the_policy_file_never_raises_or_loosens_a_restriction(seed, policy, gp):  # noqa: F811
    rng = random.Random(seed)
    data = {k: _junk(rng) for k in rng.sample(app_settings.POLICY_KEYS, 25)}
    try:
        policy(json.dumps(data))
    except (ValueError, UnicodeEncodeError):  # a value JSON can't hold
        pytest.skip("not writable as JSON")
    settings = app_settings.load()
    app_settings.policy_problems()
    for key in BOOL_RESTRICTIONS:
        if key in data and not isinstance(data[key], bool):  # IT meant to set it, and got its kind wrong
            assert settings[key]["value"] == app_settings.RESTRICTED[key], (key, data[key])
    for key, (low, high) in app_settings.SQL_RANGES.items():
        assert low <= settings[key]["value"] <= high, (key, settings[key])


@pytest.mark.parametrize("seed", SEEDS)
def test_junk_from_group_policy_never_raises_or_loosens_a_restriction(seed, policy, gp):  # noqa: F811
    rng = random.Random(seed)
    values = {}
    for key in rng.sample(app_settings.POLICY_KEYS, 25):
        kind = rng.choice(REG_KINDS)
        data = _junk(rng)
        values[key] = (data, kind)
    lists = {k: [_junk(rng) for _ in range(rng.randint(0, 3))] for k in rng.sample(app_settings.POLICY_KEYS, 5)}
    gp(values, lists)
    out, problems = registry_policy.read()
    settings = app_settings.load()
    app_settings.policy_problems()
    for key in BOOL_RESTRICTIONS:
        if key in values:
            data, kind = values[key]
            good = kind == registry_policy.REG_DWORD and type(data) is int and data in (0, 1)
            if not good:
                assert out[key] is app_settings.RESTRICTED[key], (key, values[key])
                assert settings[key]["value"] == app_settings.RESTRICTED[key]
                assert any(p.startswith(f"Group Policy value {key} ") and "most restrictive" in p for p in problems)
    for key, (low, high) in app_settings.SQL_RANGES.items():
        assert low <= settings[key]["value"] <= high, (key, settings[key])


@pytest.mark.parametrize("key", BOOL_RESTRICTIONS)
@pytest.mark.parametrize("data, kind", [("0", registry_policy.REG_SZ), (2, registry_policy.REG_DWORD),
                                        (-1, registry_policy.REG_DWORD), (b"\x00", 3)])
def test_a_restriction_group_policy_sets_with_a_bad_value_takes_its_most_restrictive_value(key, data, kind, policy, gp):  # noqa: F811
    gp({key: (data, kind)})
    out, problems = registry_policy.read()
    assert out == {key: app_settings.RESTRICTED[key]}
    assert problems == [problems[0]] and "so its most restrictive value applies" in problems[0]
