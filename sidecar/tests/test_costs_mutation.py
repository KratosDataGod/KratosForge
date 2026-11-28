"""Cost and capacity figures: pins what mutation testing (batch 33, docs/quality/mutation-testing.md)
showed no test held. Each SKU's size, which settings are taken, time zones, CUs per engine, the
reasons given when a figure isn't known, rounding, and the share of a capacity."""

from __future__ import annotations

import datetime as dt

import pytest
from fabric_desktop import costs
from fabric_desktop import measurement as ms

NOW = dt.datetime(2026, 10, 15, 12, tzinfo=dt.timezone.utc)
AT = "2026-10-15T10:00:00+00:00"
CFG = costs.settings({"python_cu": 2, "spark_cu": 8, "price_per_cu_hour": 0.5})


@pytest.fixture(autouse=True)
def _own_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


# ---- SKUs and settings

@pytest.mark.parametrize("sku, cus", [*((f"F{n}", n) for n in (2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048)),
                                      ("P1", 64), ("P2", 128), ("P3", 256), ("P4", 512), ("P5", 1024),
                                      ("FT1", 64), ("FTL64", 64), ("f8", 8)])
def test_each_sku_has_its_own_size(sku, cus):
    assert costs.sku_cu(sku) == cus


def test_unknown_skus_have_no_size():
    assert costs.sku_cu(None) is None and costs.sku_cu("A4") is None and costs.sku_cu("F3") is None


def test_settings_take_only_known_keys_with_a_value():
    out = costs.settings({"price_per_cu_hour": "", "python_cu": None, "spark_cu": "4", "currency": "EUR",
                          "region": "West Europe"})
    assert out == {"price_per_cu_hour": 0.18, "currency": "EUR", "python_cu": 1.0, "spark_cu": 4.0}


# ---- times

def test_a_time_with_an_offset_keeps_it_and_one_without_is_utc():
    assert costs._seconds("2026-01-01T02:00:00+02:00", "2026-01-01T00:00:10") == 10.0
    assert costs._seconds("2026-01-01T00:00:00Z", "2026-01-01T00:01:00+00:00") == 60.0
    assert costs._seconds("2026-01-01T00:01:00Z", "2026-01-01T00:00:00Z") == 0.0
    assert costs._seconds("yesterday", AT) is None and costs._seconds(None, AT) is None


# ---- runs as rows

def _meta(**kw):
    return {"entity": "orders", "env": "prod", "started_at": AT, "run_id": "r1"} | kw


def test_a_metadata_run_keeps_its_name_and_environment_whatever_it_knows():
    rows = costs.activities([_meta(compute="fabric"), _meta(compute="fabric", seconds=30), _meta(seconds=30)],
                            [], [], CFG, now=NOW)
    assert [(r["name"], r["env"], r["where"]) for r in rows] == [
        ("orders", "prod", "fabric"), ("orders", "prod", "fabric"), ("orders", "prod", "local")]
    assert rows[0]["seconds"] is None and rows[0]["fabric_job_cu"]["classification"] == ms.UNKNOWN
    assert rows[1]["fabric_job_cu"]["value"] == 60.0  # 30 s x 2 CU of a Python notebook


def test_a_run_under_a_second_still_counts_and_a_negative_or_boolean_one_does_not():
    rows = costs.activities([_meta(seconds=0.5), _meta(seconds=-1), _meta(seconds=True)], [],
                            [{"job": "load", "seconds": 0.5, "finished_at": AT}], CFG, now=NOW)
    assert [r["seconds"] for r in rows] == [0.5, None, None, 0.5]
    # The rough scenario is time here x the CUs of a Python notebook (2), not divided by them.
    assert rows[0]["scenario_cu"]["value"] == 1.0 and rows[3]["scenario_cu"]["value"] == 1.0
    assert rows[0]["would_cu_seconds"] == 1.0


def _fabric(**kw):
    return {"kind": "Notebook", "name": "nb", "env": "test", "started_at": AT,
            "finished_at": "2026-10-15T10:00:10+00:00", "workspace_id": "w", "item_id": "i", "job_id": "j"} | kw


def test_a_fabric_runs_workload_needs_both_its_workspace_and_item():
    rows = costs.activities([], [_fabric(), _fabric(item_id=None), _fabric(workspace_id="")], [], CFG, now=NOW)
    assert [r["fabric_job_cu"].get("workload_id") for r in rows] == ["fabric:w:i", None, None]


def test_a_fabric_run_without_a_kind_or_name_is_named_plainly():
    rows = costs.activities([], [_fabric(kind=None, name=None), _fabric(table="sales", name="nb"),
                                 _fabric(name=None, finished_at=None)], [], CFG, now=NOW)
    assert [(r["kind"], r["name"], r["env"]) for r in rows] == [
        ("Fabric job", "", "test"), ("Notebook", "sales", "test"), ("Notebook", "", "test")]


def test_a_fabric_run_whose_time_is_not_known_says_why():
    rows = costs.activities([], [_fabric(finished_at=None), _fabric(finished_at="soon")], [], CFG, now=NOW)
    assert rows[0]["fabric_job_cu"]["reason"] == "Still running, or Fabric hasn't reported its end yet."
    assert rows[1]["fabric_job_cu"]["reason"] == "Its start or end time can't be read."


@pytest.mark.parametrize("kind, engine, cu, what", [
    ("Table maintenance", None, 8, "a Spark job"),
    ("Table maintenance", "python", 2, "a Python notebook"),
    ("Notebook", "python", 2, "a Python notebook"),
    ("Notebook", None, 8, "an item whose engine wasn't recorded, counted as Spark"),
])
def test_cus_follow_the_runs_engine(kind, engine, cu, what):
    row = costs.activities([], [_fabric(kind=kind, engine=engine)], [], CFG, now=NOW)[0]
    assert row["fabric_job_cu"]["value"] == 10 * cu
    assert f"{cu:g} CU while it ran, the configured CUs of {what} (Optimize > Cost and savings > Prices)." \
        in row["fabric_job_cu"]["assumptions"]


# ---- pricing

def test_an_unknown_total_costs_unknown_and_keeps_its_reason():
    plain = costs._money({"classification": ms.UNKNOWN}, 0.5, "USD", "fabric_cost")
    assert plain["reason"] == "CU use isn't known" and plain["value"] is None
    said = costs._money({"classification": ms.UNKNOWN, "reason": "no end time"}, 0.5, "USD", "fabric_cost")
    assert said["reason"] == "no end time"


def test_a_price_note_is_added_only_when_there_is_cu_to_price():
    zero = ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, 0.0, source="log", collected_at=NOW)
    priced = costs._money(zero, 0.5, "USD", "fabric_cost")
    assert priced["classification"] == ms.MEASURED and priced["value"] == 0
    assert priced["assumptions"] == [] and priced.get("methods") == []
    some = ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_HOURS, 2.0, method="m", assumptions=["a"])
    priced = costs._money(some, 0.5, "USD", "fabric_cost")
    assert priced["value"] == 1.0
    assert priced["assumptions"] == sorted(["a", costs._PRICE_NOTE.format(price=0.5, cur="USD")])
    assert priced["methods"] == ["CU hours x price per CU hour"]


def _local_rows(n):
    return costs.activities([_meta(seconds=30, run_id=f"r{i}") for i in range(n)], [], [], CFG, now=NOW)


def test_runs_here_with_nothing_to_compare_say_so_in_good_english():
    one = costs._evidence(_local_rows(1), CFG)["avoided_cu"]["reason"]
    assert one.startswith("1 of 1 run on this PC has no comparable Fabric run") and "what it would" in one
    two = costs._evidence(_local_rows(2), CFG)["avoided_cu"]["reason"]
    assert two.startswith("2 of 2 runs on this PC have no comparable Fabric run") and "what they would" in two
    assert "last 90 days" in one


# ---- the report

def test_report_totals_are_rounded_from_cu_seconds():
    rows = costs.activities([], [_fabric(finished_at="2026-10-15T10:07:30.04+00:00")], [], CFG, now=NOW)
    total = costs.report(rows, CFG, 7, NOW)["total"]
    # 450.04 s x 8 CU = 3600.32 CU-seconds: 1.0 CU hours (3 places), 0.5 at 0.5 per CU hour.
    assert total["seconds"] == 450.0 and total["cu_hours"] == 1.0 and total["cost"] == 0.5
    rows = costs.activities([_meta(seconds=5400)], [], [], CFG, now=NOW)
    total = costs.report(rows, CFG, 7, NOW)["total"]
    assert total["scenario_cu_hours"] == 3.0 and total["scenario_saved"] == 1.5  # 5400 s x 2 CU


def test_report_rounding_keeps_its_places():
    rows = costs.activities([], [_fabric(finished_at="2026-10-15T10:07:31.8+00:00")], [], CFG, now=NOW)
    total = costs.report(rows, CFG, 7, NOW)["total"]
    # 451.8 s x 8 CU = 3614.4 CU-seconds = 1.004 CU hours, 0.502 at 0.5 per CU hour.
    assert total["seconds"] == 451.8 and total["cu_hours"] == 1.004 and total["cost"] == 0.5
    rows = costs.activities([_meta(seconds=180000)], [], [], CFG, now=NOW)
    assert costs.report(rows, CFG, 7, NOW)["total"]["scenario_saved"] == 50.0  # 100 CU hours x 0.5


def test_items_that_cost_the_same_list_the_longest_first():
    rows = costs.activities([_meta(entity="short", seconds=10), _meta(entity="long", seconds=99)], [], [], CFG, now=NOW)
    assert [i["name"] for i in costs.report(rows, CFG, 7, NOW)["by_item"]] == ["long", "short"]


def test_what_an_item_cost_ranks_it_before_how_long_it_ran():
    rows = costs.activities([_meta(entity="long", seconds=1000)], [_fabric(name="dear")], [], CFG, now=NOW)
    assert [i["name"] for i in costs.report(rows, CFG, 7, NOW)["by_item"]] == ["dear", "long"]


def test_what_a_run_here_avoided_ranks_it_before_how_long_it_ran():
    rows = costs.activities([_meta(compute="fabric", seconds=3600, run_id="f1"),
                             _meta(env="dev", seconds=60, run_id="l1"),
                             _meta(entity="other", env="dev", seconds=7200, run_id="l2")], [], [], CFG, now=NOW)
    items = costs.report(rows, CFG, 7, NOW)["by_item"]
    # orders in dev cost nothing here but avoided an estimated 1.0 (from its Fabric run in prod).
    assert [(i["name"], i["env"]) for i in items] == [("orders", "prod"), ("orders", "dev"), ("other", "dev")]
    assert items[1]["evidence"]["avoided_cost"]["value"] > 0 and items[1]["cost"] == 0


def test_items_list_their_kind_name_and_environment_costliest_first():
    rows = costs.activities([], [_fabric(name="small"),
                                 _fabric(name="big", finished_at="2026-10-15T11:00:00+00:00")], [], CFG, now=NOW)
    items = costs.report(rows, CFG, 7, NOW)["by_item"]
    assert [(i["kind"], i["name"], i["env"]) for i in items] == [("Notebook", "big", "test"), ("Notebook", "small", "test")]


def test_capacity_share_is_the_runs_cu_over_the_capacitys_whole_period():
    rows = costs.activities([], [_fabric(finished_at="2026-10-15T11:00:00+00:00")], [], CFG, now=NOW)
    env = costs.report(rows, CFG, 1, NOW, {"test": {"name": "Cap", "sku": "F2", "id": "c"}})["by_env"][0]
    # 3600 s x 8 CU = 8 CU hours of the 48 an F2 has in a day.
    assert env["capacity_share"] == 16.667
    assert env["capacity_share_evidence"]["classification"] == ms.ESTIMATED


def test_capacity_share_says_why_it_is_not_known():
    cu = ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_HOURS, 1.0, method="m", assumptions=["a"])
    since = NOW - dt.timedelta(days=1)
    no_size = costs.capacity_share(cu, {"sku": "A1"}, 1, since, NOW)["capacity_share_evidence"]["reason"]
    assert no_size.startswith("The capacity's size isn't known")
    no_cu = costs.capacity_share(ms.unknown("fabric_job_cu", ms.CU_HOURS, "x"), {"sku": "F2"}, 1, since, NOW)
    assert no_cu["capacity_share"] is None and no_cu["capacity_share_evidence"]["reason"] == "The runs' CU use isn't known."
