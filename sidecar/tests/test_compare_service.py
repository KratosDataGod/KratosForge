"""Compare platforms for one workload (Preview): links kept on this PC, pricing scenarios with DBU and
credit rates, and one read-only RPC that builds the comparison from what is already on this PC.
Nothing here reads Databricks or Snowflake; their reads are faked as last reads saved on disk."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop import audit, costs, server
from fabric_desktop import compare_service as cs
from fabric_desktop.compare_store import CompareStore
from fabric_desktop.engines import databricks_billing as bill
from fabric_desktop.engines import snowflake_query_credits as sqc

NOW = dt.datetime.now(dt.timezone.utc)
HOST = "https://adb-1.azuredatabricks.net"


def ago(days):
    return (NOW - dt.timedelta(days=days)).isoformat(timespec="seconds")


RUNS = [  # newest first, as meta_runs_all gives them
    {"run_id": "l1", "entity": "daily_sales", "compute": "local", "started_at": ago(1), "seconds": 252, "rows_read": 12_400_000,
     "logic": "v12", "backfill": ""},
    {"run_id": "f1", "entity": "daily_sales", "compute": "fabric", "started_at": ago(2), "seconds": 2880, "rows_read": 12_100_000,
     "logic": "v12", "backfill": ""},
    {"run_id": "l0", "entity": "daily_sales", "compute": "local", "started_at": ago(3), "seconds": 999, "rows_read": 12_400_000,
     "logic": "v11", "backfill": ""},
    {"run_id": "b1", "entity": "customers", "compute": "local", "started_at": ago(1), "seconds": 10, "rows_read": 5,
     "logic": "c1", "backfill": "2026-01"},
]


class Svc(cs.CompareMixin):
    def __init__(self, tmp_path, runs=RUNS, dbx=True, sf=True):
        self.tmp, self.runs, self.dbx, self.sf = tmp_path, runs, dbx, sf

    def _compare_store(self):
        return CompareStore(self.tmp / "platform-compare" / "links.json")

    def _cost_settings(self):
        return costs.settings({})

    def _billing_root(self):
        return self.tmp / "billing"

    def _snowflake_credits_root(self):
        return self.tmp / "sf"

    def meta_runs_all(self, limit=200):
        return {"runs": list(self.runs), "unavailable": {}}

    def engine_connections(self):
        return {"connections": [{"key": "cli:dev", "label": "dev (Databricks CLI)", "host": HOST, "warehouse_id": "wh"}]
                if self.dbx else []}

    def engine_profiles(self):
        conns = [{"id": "sf", "platform": "snowflake", "label": "Prod", "account": "myorg-acct", "warehouse_id": "REPORTING_XS"}]
        return {"profiles": [{"id": "p1", "name": "Work", "connections": conns if self.sf else []}]}


def test_the_rpcs_are_registered_and_only_the_comparison_skips_the_audit():
    assert cs.METHODS <= server.METHODS
    assert "compare_platforms" in audit.READ_ONLY
    assert not {"compare_link", "compare_unlink", "compare_pricing_save"} & audit.READ_ONLY


def test_links_are_kept_per_workload_and_a_job_or_tag_means_one_workload(tmp_path):
    svc = Svc(tmp_path)
    assert svc.compare_link("meta:daily_sales", "databricks", "8841", "daily_sales_rollup")["links"]["databricks"] == [
        {"job_id": "8841", "name": "daily_sales_rollup"}]
    assert svc.compare_link("meta:daily_sales", "snowflake", "kf:daily-sales")["links"]["snowflake"] == ["kf:daily-sales"]
    with pytest.raises(ValueError, match="already linked to another workload"):
        svc.compare_link("meta:customers", "databricks", "8841")
    with pytest.raises(ValueError, match="already linked to another workload"):
        svc.compare_link("meta:customers", "snowflake", "kf:daily-sales")
    assert svc.compare_unlink("meta:daily_sales", "databricks", "8841")["links"]["databricks"] == []
    assert svc.compare_link("meta:customers", "databricks", "8841")["links"]["databricks"][0]["job_id"] == "8841"


@pytest.mark.parametrize("platform, ref, words", [("databricks", "8841; DROP", "job id"), ("snowflake", "a'b", "query tag"),
                                                  ("fabric", "x", "Databricks job or a Snowflake query tag")])
def test_odd_links_are_refused_in_plain_words(tmp_path, platform, ref, words):
    with pytest.raises(ValueError, match=words):
        Svc(tmp_path).compare_link("meta:daily_sales", platform, ref)


def test_a_scenario_with_dbu_and_credit_rates_is_saved_and_offered_after_todays_price(tmp_path):
    svc = Svc(tmp_path)
    row = svc.compare_pricing_save("List prices, Nov 2026", 0.18, rate_per_dbu={"JOBS_COMPUTE": 0.15}, rate_per_credit=2,
                                   source="Public price pages")
    assert row["rate_per_dbu"] == {"JOBS_COMPUTE": 0.15} and row["pricing_version"].startswith("pv-")
    again = svc.compare_pricing_save("List prices, Nov 2026", 0.18, rate_per_credit=3)
    scen = svc.compare_platforms()["scenarios"]
    assert [s["scenario"] for s in scen] == ["Default price", "List prices, Nov 2026"]
    assert scen[1]["pricing_version"] == again["pricing_version"] != row["pricing_version"]
    with pytest.raises(ValueError, match="above 0"):
        svc.compare_pricing_save("Bad", 0.18, rate_per_credit=-1)


def test_the_comparison_reads_only_what_is_on_this_pc(tmp_path):
    svc = Svc(tmp_path)
    svc.compare_link("meta:daily_sales", "databricks", "8841", "daily_sales_rollup")
    svc.compare_link("meta:daily_sales", "snowflake", "kf:daily-sales")
    bill.save(tmp_path / "billing", HOST, {"schema": bill.SCHEMA, "read_at": ago(0), "days": 30, "rows": [], "runs": [
        {"job_id": "8841", "job_run_id": "r1", "sku": "JOBS_COMPUTE", "dbu": 1.42, "started": ago(1)}], "run_jobs": ["8841"]})
    sqc.save(tmp_path / "sf", "myorg-acct", {"schema": sqc.SCHEMA, "read_at": ago(0), "days": 30, "assumptions": ["x"],
                                            "tags": ["kf:daily-sales"],
                                            "queries": [{"query_id": "q", "tag": "kf:daily-sales", "warehouse": "REPORTING_XS",
                                                         "size": "X-Small", "credits_per_hour": 1.0, "seconds": 972,
                                                         "credits": 0.27, "start": ago(1)}]})
    version = svc.compare_pricing_save("List", 0.18, rate_per_dbu={"JOBS_COMPUTE": 0.15}, rate_per_credit=2)["pricing_version"]
    out = svc.compare_platforms(pricing_version=version)
    assert [w["key"] for w in out["workloads"]] == ["meta:customers", "meta:daily_sales"]
    assert out["workload"] == "meta:customers"   # the first in the list when none is named
    out = svc.compare_platforms(workload="meta:daily_sales", pricing_version=version)
    cards = {c["platform"]: c for c in out["comparison"]["cards"]}
    # This PC: only the runs of the current definition (v12), not the older one.
    assert cards["pc"]["figure"]["value"] == 252 and cards["pc"]["figure"]["runs"] == 1
    assert cards["pc"]["details"][0]["text"] == "Not recorded"
    assert cards["fabric"]["figure"]["value"] == pytest.approx(2880) and cards["fabric"]["figure"]["classification"] == "estimated"
    assert cards["databricks"]["figure"]["value"] == pytest.approx(1.42)
    assert cards["snowflake"]["figure"]["value"] == pytest.approx(0.27)
    assert out["comparison"]["comparison"]["text"].startswith("At these prices, the Fabric estimate ($0.14) is lower than")
    assert out["databricks_connection"] == "cli:dev" and out["snowflake_connection"] == "profile:p1:sf"


def test_no_connections_and_no_scenario(tmp_path):
    out = Svc(tmp_path, dbx=False, sf=False).compare_platforms(workload="meta:daily_sales")
    cards = {c["platform"]: c for c in out["comparison"]["cards"]}
    assert cards["databricks"]["status"] == "not_connected" and cards["snowflake"]["status"] == "not_connected"
    assert out["comparison"]["scenario"] is None and out["comparison"]["comparison"] is None


def test_no_workloads_and_unknown_choices(tmp_path):
    assert Svc(tmp_path, runs=[]).compare_platforms()["workload"] is None
    with pytest.raises(ValueError, match="Pick one from the list"):
        Svc(tmp_path).compare_platforms(workload="meta:nope")
    with pytest.raises(ValueError, match="pricing scenario"):
        Svc(tmp_path).compare_platforms(pricing_version="pv-gone")


def test_a_damaged_store_starts_empty(tmp_path):
    path = tmp_path / "platform-compare" / "links.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"schema": "other"}', encoding="utf-8")
    assert CompareStore(path).links("meta:x") == {"databricks": [], "snowflake": []}


@pytest.mark.parametrize("body", [
    {"links": []}, {"links": {"meta:x": "8841"}}, {"links": {"meta:x": {"databricks": "8841"}}},
    {"links": {"meta:x": {"databricks": ["8841"]}}}, {"links": {"meta:x": {"snowflake": [1]}}},
    {"scenarios": {}},
])
def test_a_wrong_typed_store_is_damaged_and_starts_empty(tmp_path, body):
    import json
    from fabric_desktop import compare_store
    path = tmp_path / "platform-compare" / "links.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema": compare_store.SCHEMA} | body), encoding="utf-8")
    store = CompareStore(path)
    assert store.links("meta:x") == {"databricks": [], "snowflake": []} and store.scenarios() == []
    assert store.link("meta:x", "snowflake", "kf:tag")["snowflake"] == ["kf:tag"]
    assert store.unlink("meta:x", "snowflake", "kf:tag")["snowflake"] == []
