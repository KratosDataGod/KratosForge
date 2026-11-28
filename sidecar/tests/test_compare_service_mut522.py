"""Mutation batch 522: compare_service.py and compare_store.py, the platform comparison's workloads, connections,
scope and the links and pricing scenarios kept on this PC. Each test pins a behavior a surviving mutant changed."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import compare_service as cs
from fabric_desktop import compare_store
from fabric_desktop.compare_store import CompareStore
from tests.test_compare_service import HOST, Svc, ago


def test_a_run_with_a_backfill_is_that_backfill_and_one_without_is_the_regular_load():
    assert cs._scope({"backfill": " 2026-01 "}) == "backfill 2026-01"
    assert cs._scope({"backfill": ""}) == "regular load"
    assert cs._scope({}) == "regular load"


def test_the_store_lives_under_this_pcs_data_folder(tmp_path, monkeypatch):
    from fabric_desktop import service
    monkeypatch.setattr(service, "data_dir", lambda: tmp_path)
    store = cs.CompareMixin()._compare_store()
    assert isinstance(store, CompareStore)
    assert store.path == tmp_path / "platform-compare" / "links.json"


def test_a_run_with_no_entity_is_not_a_workload():
    runs = [{"run_id": "x", "entity": "", "compute": "local"}, {"run_id": "y", "compute": "local"},
            {"run_id": "z", "entity": "orders", "compute": "local"}, {"run_id": "w", "entity": "orders", "compute": "fabric"}]
    assert cs.CompareMixin._compare_workloads(runs) == [{"key": "meta:orders", "name": "orders"}]


def test_the_newest_pc_run_sets_the_definition_and_scope(tmp_path):
    runs = [{"run_id": "n", "entity": "e", "compute": "local", "logic": "v2", "backfill": "2026-02", "seconds": 5},
            {"run_id": "o", "entity": "e", "compute": "local", "logic": "v1", "backfill": "", "seconds": 9}]
    work, pc, fabric = Svc(tmp_path, runs=runs)._compare_inputs("meta:e", runs)
    assert work == {"key": "meta:e", "name": "e", "definition_version": "v2", "input_scope": "backfill 2026-02"}
    assert [r["run_id"] for r in pc] == ["n"] and fabric == []


def test_a_workload_run_only_in_fabric_takes_its_definition_from_that_run(tmp_path):
    runs = [{"run_id": "f", "entity": "e", "compute": "fabric", "logic": "v7", "backfill": "", "seconds": 60,
             "started_at": ago(1), "rows_read": 3}]
    work, pc, _fabric = Svc(tmp_path, runs=runs)._compare_inputs("meta:e", runs)
    assert work["definition_version"] == "v7" and work["input_scope"] == "regular load" and pc == []


def test_a_fabric_run_keeps_its_own_backfill_scope(tmp_path):
    runs = [{"run_id": "f9", "entity": "e", "compute": "fabric", "logic": "v1", "backfill": "2026-03", "seconds": 600,
             "started_at": ago(1), "rows_read": 10}]
    _work, _pc, fabric = Svc(tmp_path, runs=runs)._compare_inputs("meta:e", runs)
    assert [(r["run_id"], r["input_scope"]) for r in fabric] == [("f9", "backfill 2026-03")]


def test_every_run_on_this_pc_is_asked_for_and_what_is_unavailable_is_passed_on(tmp_path):
    asked = {}

    class Counting(Svc):
        def meta_runs_all(self, limit=200):
            asked["limit"] = limit
            return {"runs": list(self.runs), "unavailable": {"fabric": "Sign in to read Fabric runs."}}

    out = Counting(tmp_path).compare_platforms()
    assert asked["limit"] == 100000
    assert out["unavailable"] == {"fabric": "Sign in to read Fabric runs."}


class TwoEach(Svc):
    def engine_connections(self):
        return {"connections": [{"key": "cli:a", "label": "a", "host": HOST, "warehouse_id": "w1"},
                                {"key": "cli:b", "label": "b", "host": "https://adb-2.azuredatabricks.net"},
                                {"key": "cli:nohost", "label": "c"}]}

    def engine_profiles(self):
        return {"profiles": [
            {"id": "p1", "name": "Work", "connections": [
                {"id": "s1", "platform": "snowflake", "label": "Prod", "account": "acct-1", "warehouse_id": "XS"},
                {"id": "d1", "platform": "databricks", "label": "Not Snowflake", "account": "acct-x"},
                {"id": "s0", "platform": "snowflake", "label": "No account"}]},
            {"id": "p2", "connections": [{"id": "s2", "platform": "snowflake", "account": "acct-2"}]}]}


def test_connections_list_only_databricks_hosts_and_snowflake_accounts_with_plain_labels(tmp_path):
    conns = TwoEach(tmp_path)._compare_connections()
    assert [c["key"] for c in conns["databricks"]] == ["cli:a", "cli:b"]
    assert conns["databricks"][1] == {"key": "cli:b", "label": "b", "host": "https://adb-2.azuredatabricks.net",
                                      "warehouse_id": ""}
    assert conns["snowflake"] == [
        {"key": "profile:p1:s1", "label": "Work · Prod", "account": "acct-1", "warehouse": "XS"},
        {"key": "profile:p2:s2", "label": "p2 · s2", "account": "acct-2", "warehouse": ""}]


def test_the_chosen_connection_is_used_and_an_unknown_one_falls_back_to_the_first(tmp_path):
    svc = TwoEach(tmp_path)
    out = svc.compare_platforms(workload="meta:daily_sales", databricks_connection="cli:b",
                                snowflake_connection="profile:p2:s2")
    assert out["databricks_connection"] == "cli:b" and out["snowflake_connection"] == "profile:p2:s2"
    out = svc.compare_platforms(workload="meta:daily_sales", databricks_connection="cli:gone",
                                snowflake_connection="profile:gone")
    assert out["databricks_connection"] == "cli:a" and out["snowflake_connection"] == "profile:p1:s1"



# compare_store.py

def store(tmp_path):
    return CompareStore(tmp_path / "platform-compare" / "links.json")


@pytest.mark.parametrize("workload", ["", "   ", None, 7, "a\nb", "x" * 301])
def test_a_link_needs_a_workload_in_plain_text(tmp_path, workload):
    with pytest.raises(ValueError, match="Pick a workload to link to"):
        store(tmp_path).link(workload, "snowflake", "kf:tag")
    assert not (tmp_path / "platform-compare" / "links.json").exists()


@pytest.mark.parametrize("name", ["a\tb", "x" * 201])
def test_a_jobs_name_is_plain_text_up_to_200_characters(tmp_path, name):
    with pytest.raises(ValueError, match="plain text, up to 200 characters"):
        store(tmp_path).link("meta:w", "databricks", "1", name)
    assert store(tmp_path).links("meta:w") == {"databricks": [], "snowflake": []}


def test_linking_the_same_job_or_tag_to_the_same_workload_again_keeps_one_with_the_new_name(tmp_path):
    s = store(tmp_path)
    s.link("meta:w", "databricks", "1", "first")
    s.link("meta:w", "databricks", "2", "other")
    assert s.link("meta:w", "databricks", "1", "renamed")["databricks"] == [
        {"job_id": "2", "name": "other"}, {"job_id": "1", "name": "renamed"}]
    s.link("meta:w", "snowflake", "kf:a")
    s.link("meta:w", "snowflake", "kf:b")
    assert s.link("meta:w", "snowflake", "kf:a")["snowflake"] == ["kf:b", "kf:a"]


def test_a_job_id_and_a_tag_with_the_same_text_dont_clash_across_platforms(tmp_path):
    s = store(tmp_path)
    s.link("meta:w", "databricks", "123")
    assert s.link("meta:v", "snowflake", "123")["snowflake"] == ["123"]
    s.link("meta:v", "databricks", "9")
    assert s.link("meta:w", "snowflake", "9")["snowflake"] == ["9"]


@pytest.mark.parametrize("platform, refs, word", [
    ("databricks", [str(n) for n in range(1, 22)], "jobs"),
    ("snowflake", [f"kf:t{n}" for n in range(1, 22)], "tags")])
def test_a_workload_takes_up_to_twenty_jobs_and_up_to_twenty_tags(tmp_path, platform, refs, word):
    s = store(tmp_path)
    for ref in refs[:20]:
        out = s.link("meta:w", platform, ref)
    assert len(out[platform]) == 20
    with pytest.raises(ValueError, match=f"at most 20 linked {word}"):
        s.link("meta:w", platform, refs[20])
    assert len(s.links("meta:w")[platform]) == 20


def test_twenty_jobs_dont_use_up_the_tags(tmp_path):
    s = store(tmp_path)
    for n in range(1, 21):
        s.link("meta:w", "databricks", str(n))
    assert s.link("meta:w", "snowflake", "kf:one")["snowflake"] == ["kf:one"]


def test_unlinking_one_keeps_the_others_and_the_last_one_drops_the_workload(tmp_path):
    s = store(tmp_path)
    s.link("meta:w", "databricks", "1")
    s.link("meta:w", "databricks", "2")
    s.link("meta:w", "snowflake", "kf:a")
    s.link("meta:w", "snowflake", "kf:b")
    assert s.unlink("meta:w", "databricks", "1")["databricks"] == [{"job_id": "2", "name": ""}]
    assert s.unlink("meta:w", "snowflake", "kf:a")["snowflake"] == ["kf:b"]
    s.unlink("meta:w", "databricks", "2")
    assert "meta:w" in s.all()["links"], "a workload with a tag left is still linked"
    s.unlink("meta:w", "snowflake", "kf:b")
    assert "meta:w" not in s.all()["links"]


def test_the_store_folder_is_made_with_its_parents(tmp_path):
    s = CompareStore(tmp_path / "a" / "b" / "links.json")
    s.link("meta:w", "snowflake", "kf:a")
    assert s.links("meta:w")["snowflake"] == ["kf:a"]


def test_a_saved_scenario_without_a_pricing_version_isnt_offered(tmp_path):
    path = tmp_path / "platform-compare" / "links.json"
    path.parent.mkdir(parents=True)
    good = {"scenario": "ok", "pricing_version": "pv-1"}
    path.write_text(json.dumps({"schema": compare_store.SCHEMA, "links": {},
                                "scenarios": [{"scenario": "no version"}, good, "not a record"]}), encoding="utf-8")
    assert CompareStore(path).scenarios() == [good]


def _price(name):
    return {"scenario": name, "rate_per_cu_hour": 0.18, "currency": "USD", "effective_from": "2000-01-01T00:00:00+00:00"}


def test_up_to_fifty_scenarios_are_kept_and_a_name_already_kept_can_still_be_saved(tmp_path):
    s = store(tmp_path)
    for n in range(50):
        s.save_scenario(_price(f"s{n}"))
    assert len(s.scenarios()) == 50
    with pytest.raises(ValueError, match="at most 50 pricing scenarios"):
        s.save_scenario(_price("one too many"))
    again = s.save_scenario(_price("s0") | {"rate_per_cu_hour": 0.2})
    names = [x["scenario"] for x in s.scenarios()]
    assert len(names) == 50 and names[-1] == "s0" and s.scenarios()[-1]["pricing_version"] == again["pricing_version"]


def test_a_job_or_tag_linked_elsewhere_is_named_as_a_job_or_a_query_tag(tmp_path):
    s = store(tmp_path)
    s.link("meta:w", "databricks", "8841")
    s.link("meta:w", "snowflake", "kf:t")
    with pytest.raises(ValueError, match=r"^Job 8841 is already linked to another workload \(meta:w\)\. Unlink it there first\.$"):
        s.link("meta:v", "databricks", "8841")
    with pytest.raises(ValueError, match=r"^The query tag kf:t is already linked to another workload \(meta:w\)\."):
        s.link("meta:v", "snowflake", "kf:t")
