"""Connection profiles at their exact edges (boundary re-score of batch 13, mutation batch 160,
docs/quality/mutation-testing.md): a key of exactly 60 characters, a hybrid of exactly Fabric and
Databricks, and a remote budget of 0 minutes."""

from __future__ import annotations

from fabric_desktop import connprofiles as cp


def _hybrid():
    return cp.normalize(dict(cp.default_profile(), kind="hybrid", connections=[
        {"id": "f", "platform": "fabric", "workspace_id": "w", "credential": {"method": "app-sign-in"}},
        {"id": "d", "platform": "databricks", "host": "https://adb-1.2.azuredatabricks.net",
         "credential": {"method": "oauth-u2m"}}]))


def test_a_key_of_exactly_60_characters_is_shown_whole():
    key = "ab " * 19 + "abc"
    assert len(key) == 60 and cp._key_text(key) == key
    assert cp._key_text(key + "d") == key[:59] + "…"


def test_a_hybrid_of_exactly_fabric_and_databricks_is_schema_1():
    assert cp.needed_schema(_hybrid()) == 1


def test_a_hybrid_of_two_platforms_is_enough():
    assert not [p for p in cp.problems(_hybrid()) if "two or more platforms" in p]


def test_a_remote_budget_of_0_minutes_is_allowed():
    p = _hybrid()
    p["execution"]["budget"] = {"remote_minutes_per_day": 0}
    assert not [x for x in cp.problems(p) if "remote_minutes_per_day" in x]
    p["execution"]["budget"] = {"remote_minutes_per_day": -1}
    assert [x for x in cp.problems(p) if "remote_minutes_per_day" in x]
