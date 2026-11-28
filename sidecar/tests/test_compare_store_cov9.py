"""compare_store: the refusals of link, unlink and save_scenario, and an unlink of a workload with no links."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import compare_store as store_mod
from fabric_desktop.compare_store import CompareStore


@pytest.fixture()
def store(tmp_path):
    return CompareStore(tmp_path / "platform-compare" / "links.json")


@pytest.mark.parametrize("key", ["", "   ", None, 5, "a\x00b", "x" * 301])
def test_a_workload_key_must_be_plain_text(store, key):
    with pytest.raises(ValueError, match="Pick a workload to link to."):
        store.link(key, "databricks", "1")
    assert not store.path.exists()


def test_an_unknown_platform_is_refused(store):
    with pytest.raises(ValueError, match="Link a Databricks job or a Snowflake query tag."):
        store.link("meta:a", "bigquery", "1")


def test_a_jobs_name_must_be_plain_text(store):
    with pytest.raises(ValueError, match="plain text, up to 200 characters"):
        store.link("meta:a", "databricks", "1", "bad\nname")
    with pytest.raises(ValueError, match="plain text, up to 200 characters"):
        store.link("meta:a", "databricks", "1", "n" * 201)
    assert store.link("meta:a", "databricks", "1", "n" * 200)["databricks"] == [{"job_id": "1", "name": "n" * 200}]


def test_linking_again_replaces_the_name_and_keeps_one_entry(store):
    store.link("meta:a", "databricks", "7", "old")
    assert store.link("meta:a", "databricks", " 7 ", "new")["databricks"] == [{"job_id": "7", "name": "new"}]
    store.link("meta:a", "snowflake", "kf:a")
    assert store.link("meta:a", "snowflake", "kf:a")["snowflake"] == ["kf:a"]


@pytest.mark.parametrize("platform, make, limit, word", [
    ("databricks", str, store_mod.databricks_billing.MAX_JOBS, "jobs"),
    ("snowflake", lambda i: f"tag-{i}", store_mod.snowflake_query_credits.MAX_TAGS, "tags")])
def test_a_workload_has_a_limit_of_links(store, platform, make, limit, word):
    for i in range(limit):
        store.link("meta:a", platform, make(i))
    before = store.path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match=f"at most {limit} linked {word}"):
        store.link("meta:a", platform, make(limit))
    assert store.path.read_text(encoding="utf-8") == before  # nothing written


def test_unlinking_from_a_workload_with_no_links_writes_nothing(store):
    assert store.unlink("meta:none", "snowflake", "kf:x") == {"databricks": [], "snowflake": []}
    assert not store.path.exists()


def test_unlinking_one_of_two_keeps_the_workload(store):
    store.link("meta:a", "databricks", "1")
    store.link("meta:a", "snowflake", "kf:a")
    assert store.unlink("meta:a", "snowflake", "kf:a") == {"databricks": [{"job_id": "1", "name": ""}], "snowflake": []}
    assert "meta:a" in json.loads(store.path.read_text(encoding="utf-8"))["links"]
    store.unlink("meta:a", "databricks", "1")
    assert json.loads(store.path.read_text(encoding="utf-8"))["links"] == {}


def scenario(name):
    return {"scenario": name, "rate_per_cu_hour": 0.18, "currency": "usd", "effective_from": "2026-01-01"}


def test_at_most_fifty_scenarios_but_a_name_saved_again_replaces_it(store, monkeypatch):
    monkeypatch.setattr(store_mod, "MAX_SCENARIOS", 3)
    for name in ("a", "b", "c"):
        store.save_scenario(scenario(name))
    with pytest.raises(ValueError, match="Keep at most 3 pricing scenarios on this PC."):
        store.save_scenario(scenario("d"))
    again = store.save_scenario(scenario("b") | {"rate_per_cu_hour": 0.2})
    assert [s["scenario"] for s in store.scenarios()] == ["a", "c", "b"]
    assert store.scenarios()[-1] == again and again["rate_per_cu_hour"] == 0.2


def test_scenarios_without_a_pricing_version_are_left_out(store):
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps({"schema": store_mod.SCHEMA, "links": {},
                                      "scenarios": [{"scenario": "x"}, "junk", {"pricing_version": "p1"}]}),
                          encoding="utf-8")
    assert store.scenarios() == [{"pricing_version": "p1"}]
