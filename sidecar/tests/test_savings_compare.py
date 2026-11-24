"""Compare platforms for one workload (Preview): each platform in its own unit, money only under one
pricing version, and a closing sentence that compares, never recommends (savings/compare.py)."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import compare as cmp
from fabric_desktop.savings import pricing as pr

NOW = dt.datetime(2026, 11, 24, 10, 0, tzinfo=dt.timezone.utc)
WORKLOAD = {"key": "meta:daily_sales", "name": "daily_sales", "definition_version": "v12", "input_scope": "regular load"}
LIST = pr.scenario(scenario="List prices, Nov 2026", rate_per_cu_hour=0.18, currency="USD",
                   effective_from="2026-11-03T00:00:00Z", source="Each platform's public price page",
                   rate_per_dbu={"JOBS_COMPUTE": 0.15}, rate_per_credit=2.0)
CU_ONLY = pr.scenario(scenario="Default price", rate_per_cu_hour=0.18, currency="USD", effective_from="2026-01-01")


def day(n):
    return (NOW - dt.timedelta(days=n)).isoformat()


def pc(n=4):
    return [{"run_id": f"pc{i}", "at": day(i + 1), "seconds": 252, "cpu_seconds": 580, "peak_bytes": 3.1e9,
             "rows": 12.4e6, "bytes": None} for i in range(n)]


def fabric(n=3, version="v12", rows=12.1e6):
    cu = lambda i: ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_SECONDS, 2880, method="run time x configured CUs",  # noqa: E731
                           source="Fabric job API", assumptions=["Run time 6 min times 8 CU (Spark starter pool)"],
                           run_id=f"f{i}")
    return [{"run_id": f"f{i}", "at": day(i + 1), "workload_key": WORKLOAD["key"], "definition_version": version,
             "input_scope": "regular load", "rows": rows, "cu": cu(i)} for i in range(n)]


def dbx(rows=True, connected=True, jobs=True):
    runs = [{"job_id": "8841", "job_run_id": f"r{i}", "sku": "JOBS_COMPUTE", "dbu": 1.42, "started": day(i + 1)}
            for i in range(3)] if rows else []
    return {"connected": connected, "jobs": [{"job_id": "8841", "name": "daily_sales_rollup"}] if jobs else [],
            "read": {"read_at": "2026-11-24T08:55:00Z", "runs": runs + [
                {"job_id": "9999", "job_run_id": "x", "sku": "JOBS_COMPUTE", "dbu": 50, "started": day(1)}]}}


def snow(connected=True):
    q = [{"query_id": f"q{i}", "tag": "kf:daily-sales", "warehouse": "REPORTING_XS", "size": "X-Small",
          "credits_per_hour": 1.0, "seconds": 972, "credits": 0.27, "bytes_scanned": None, "start": day(i + 1)}
         for i in range(3)]
    return {"connected": connected, "tags": ["kf:daily-sales"],
            "read": {"read_at": "2026-11-24T09:12:00Z", "queries": q, "assumptions": ["Cloud services credits aren't counted."]}}


def run(scenario=LIST, **kw):
    args = {"pc_runs": pc(), "fabric_runs": fabric(), "databricks": dbx(), "snowflake": snow()} | kw
    return cmp.compare(WORKLOAD, scenario=scenario, now=NOW, **args)


def card(out, platform):
    return next(c for c in out["cards"] if c["platform"] == platform)


def test_three_platforms_each_in_its_own_unit_with_the_mockups_figures():
    out = run()
    pcc, fab, db, sf = (card(out, p) for p in ("pc", "fabric", "databricks", "snowflake"))
    assert [c["unit"] for c in out["cards"]] == ["seconds", "fabric_cu_seconds", "databricks_dbu", "snowflake_credits"]
    assert pcc["figure"]["classification"] == ms.MEASURED and pcc["figure"]["text"] == "4 min 12 s"
    assert pcc["details"][0]["text"] == "9 min 40 s" and pcc["details"][1]["text"] == "3.1 GB"
    assert pcc["money"]["text"] == "Not priced. This PC's hardware and power aren't counted."
    assert fab["figure"]["classification"] == ms.ESTIMATED and fab["figure"]["text"] == "2,880 CU seconds"
    assert fab["figure"]["method"] == "Run time × configured CUs"
    assert fab["sub"] == "0.80 CU hours" and fab["input"] == "12.1 M rows. Within 25% of this PC's runs."
    assert fab["money"]["text"] == "$0.14" and fab["money"]["price_line"] == "0.80 CU hours × $0.18 per CU hour"
    assert db["figure"]["classification"] == ms.MEASURED and db["figure"]["text"] == "1.42 DBU"
    assert db["figure"]["source"] == "Billed usage, system.billing.usage"
    assert db["matched_by"] == "Job daily_sales_rollup (8841), linked to this workload by you. 3 runs."
    assert db["input"] == "Not recorded, so the volume check didn't run."
    assert db["money"]["price_line"] == "1.42 DBU × $0.15 per DBU" and db["money"]["text"] == "$0.21"
    assert sf["figure"]["classification"] == ms.ESTIMATED and sf["figure"]["text"] == "0.27 credits"
    assert sf["sub"] == "X-Small, 1 credit per hour"
    assert sf["figure"]["method"] == "Warehouse time 16 min 12 s × 1 credit per hour"
    assert sf["money"]["price_line"] == "0.27 credits × $2.00 per credit" and sf["money"]["text"] == "$0.54"
    assert {c["money"]["pricing_version"] for c in (fab, db, sf)} == {LIST["pricing_version"]}
    assert all(c["money"]["classification"] == ms.SCENARIO for c in (fab, db, sf))
    assert out["comparison"]["text"] == ("At these prices, the Fabric estimate ($0.14) is lower than the Databricks "
                                         "figure ($0.21) and the Snowflake estimate ($0.54).")
    assert out["comparison"]["classification"] == ms.SCENARIO


def test_a_platform_not_connected_has_no_figure_and_no_zero_and_is_named_as_left_out():
    out = run(snowflake=snow(connected=False))
    sf = card(out, "snowflake")
    assert sf["status"] == cmp.NOT_CONNECTED and sf["figure"]["value"] is None
    assert "Settings > Connections" in sf["connect"] and "query history" in sf["connect"]
    assert sf["money"]["value"] is None and sf["money"]["text"] == "No figure. Snowflake isn't connected."
    assert out["comparison"]["text"] == ("At these prices, the Fabric estimate ($0.14) is lower than the Databricks "
                                         "figure ($0.21). Snowflake isn't compared because it isn't connected.")


def test_a_figure_not_known_is_none_with_its_reason_and_its_money_is_not_known():
    out = run(databricks=dbx(rows=False))
    db = card(out, "databricks")
    assert db["status"] == cmp.UNKNOWN and db["figure"]["value"] is None
    assert db["figure"]["reason"].startswith("No billing rows for this job's runs yet.")
    assert db["money"]["classification"] == ms.UNKNOWN and db["money"]["value"] is None
    assert out["comparison"]["text"] == ("At these prices, the Fabric estimate ($0.14) is lower than the Snowflake "
                                         "estimate ($0.54). Databricks isn't compared because its figure isn't known.")


def test_no_scenario_shows_native_units_only():
    out = run(scenario=None)
    assert out["scenario"] is None and out["comparison"] is None
    assert all(c["money"] is None for c in out["cards"] if c["platform"] != "pc")
    assert out["note"].startswith("Choose a pricing scenario to see money.")
    assert card(out, "databricks")["figure"]["value"] == pytest.approx(1.42)


def test_no_savings_and_no_recommendation_anywhere():
    text = repr(run()).lower()
    assert "saving" not in text and "recommend" not in text and "cheaper" not in text and "—" not in text


def test_a_scenario_without_a_dbu_or_credit_rate_leaves_those_out_and_compares_nothing():
    out = run(scenario=CU_ONLY)
    assert card(out, "databricks")["money"]["status"] == "no_rate"
    assert "has no rate per DBU for JOBS_COMPUTE" in card(out, "databricks")["money"]["reason"]
    assert card(out, "snowflake")["money"]["status"] == "no_rate"
    assert out["comparison"] is None and out["note"].startswith("Fewer than two platforms")


def test_dbus_are_priced_only_at_their_own_skus_rate():
    d = dbx()
    d["read"]["runs"][0]["sku"] = "PREMIUM_JOBS_SERVERLESS_COMPUTE"
    out = run(databricks=d)
    assert card(out, "databricks")["money"]["status"] == "no_rate"
    both = pr.scenario(scenario="Two", rate_per_cu_hour=0.18, currency="USD", effective_from="2026-01-01",
                       rate_per_dbu={"JOBS_COMPUTE": 0.15, "PREMIUM_JOBS_SERVERLESS_COMPUTE": 0.35})
    m = card(run(scenario=both, databricks=d), "databricks")["money"]
    assert m["value"] == pytest.approx(1.42 / 3 * 0.35 + 2.84 / 3 * 0.15, abs=1e-6)
    assert "(JOBS_COMPUTE)" in m["price_line"] and " + " in m["price_line"]


def test_fabric_runs_of_another_definition_dont_count_and_say_why():
    out = run(fabric_runs=fabric(version="v11"))
    fab = card(out, "fabric")
    assert fab["figure"]["value"] is None and "definition version" in fab["figure"]["reason"]
    assert fab["excluded"] and all("v11" in x["reason"] for x in fab["excluded"])


def test_fabric_runs_with_different_volume_dont_count():
    fab = card(run(fabric_runs=fabric(rows=30e6)), "fabric")
    assert fab["figure"]["value"] is None and "different data" in fab["figure"]["reason"]


def test_a_run_with_no_figure_is_counted_beside_the_average_never_filled_in():
    s = snow()
    s["read"]["queries"][0] |= {"credits": None, "reason": "The warehouse size wasn't recorded."}
    sf = card(run(snowflake=s), "snowflake")
    assert sf["figure"]["runs"] == 2 and sf["figure"]["runs_total"] == 3
    assert sf["figure"]["value"] == pytest.approx(0.27)


def test_unlinked_platforms_say_how_to_link():
    out = run(databricks=dbx(jobs=False))
    db = card(out, "databricks")
    assert db["status"] == cmp.NOT_LINKED and db["figure"]["value"] is None and "Link the job" in db["figure"]["reason"]
    assert "Databricks isn't compared because nothing is linked to this workload." in out["comparison"]["text"]


def test_runs_outside_the_period_dont_count():
    out = cmp.compare(WORKLOAD, pc_runs=pc(), fabric_runs=fabric(), databricks=dbx(), snowflake=snow(), scenario=LIST,
                      days=7, now=NOW + dt.timedelta(days=30))
    assert all(c["figure"]["value"] is None for c in out["cards"])


def test_equal_amounts_are_the_same_not_lower():
    s = snow()
    for q in s["read"]["queries"]:
        q["credits"] = 0.072   # 0.072 x $2.00 = $0.144, shown as $0.14 like Fabric
    out = run(snowflake=s, databricks=dbx(connected=False))
    assert out["comparison"]["text"].startswith("At these prices, the Fabric estimate ($0.14) and the Snowflake "
                                                "estimate ($0.14) are the same.")


def test_the_pc_without_runs_is_unknown_not_zero():
    pcc = card(run(pc_runs=[]), "pc")
    assert pcc["figure"]["value"] is None and pcc["figure"]["reason"]


def test_bad_input_is_refused_in_plain_words():
    with pytest.raises(ValueError, match="Compare the last"):
        cmp.compare(WORKLOAD, pc_runs=[], fabric_runs=[], days=5)
    with pytest.raises(ValueError, match="Pick a workload"):
        cmp.compare({}, pc_runs=[], fabric_runs=[])


def test_money_words():
    assert cmp.money_text(0.144, "USD") == "$0.14" and cmp.money_text(0.004, "EUR") == "€0.0040"
    assert cmp.money_text(2, "SEK") == "2.00 SEK" and cmp.rate_text(0.055, "USD") == "$0.055"
    assert cmp.duration(45) == "45 s" and cmp.duration(3780) == "1 h 3 min"


def test_method_lines_use_the_times_sign_and_leave_other_words_alone():
    assert cmp.method_text("run time x configured CUs") == "Run time × configured CUs"
    assert cmp.method_text("time on this PC x configured CUs of a Python notebook") == \
        "Time on this PC × configured CUs of a Python notebook"
    assert cmp.method_text("max x-axis value") == "Max x-axis value"
