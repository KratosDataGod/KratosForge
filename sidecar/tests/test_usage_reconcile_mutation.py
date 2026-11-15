"""usage/reconcile.py paths that mutation testing (batch 35, docs/quality/mutation-testing.md) showed no
test pinned: the edges of the coverage window, which runs and rows count, how the results read, and
the per-capacity bill. Rows are built by hand with only the fields reconcile reads."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.usage import reconcile as rc

NOW = dt.datetime(2026, 10, 17, 12, tzinfo=dt.UTC)
SINCE = NOW - dt.timedelta(days=30)


def at(text):
    return f"2026-10-16T{text}+00:00"


def cu(value, start, end):
    return {"value": value, "unit": ms.CU_SECONDS, "source": "export.csv", "uncertainty": "sampled",
            "classification": ms.MEASURED, "metric": "fabric_operation_cu", "period": {"start": start, "end": end}}


def op(item, start, end, value=100.0, ws="Sales Dev"):
    return {"level": "operation", "workspace": ws, "item": item, "start": start, "end": end, "cu": cu(value, start, end),
            "cost": None}


def imp(*rows, id="u-1", imported_at=None):
    return {"id": id, "imported_at": imported_at or NOW.isoformat(), "rows": list(rows)}


def run(job, name, start, end, kind="Notebook run", ws="Sales Dev", **extra):
    return {"kind": kind, "name": name, "workspace_name": ws, "workspace_id": "w1", "job_id": job,
            "started_at": start, "finished_at": end} | extra


def bill(cap, day, meter="CU", value=10.0, slot=None, cost=1.0):
    period = {"start": f"2026-10-{day}T00:00:00+00:00", "end": f"2026-10-{day + 1}T00:00:00+00:00"}
    return {"level": "capacity", "workspace": None, "item": None, "key": f"{cap}:{day}:{meter}:{value}",
            "slot": slot or f"{cap}:{day}:{meter}", "capacity_id": cap.lower(), "capacity": cap, "meter": meter,
            "start": period["start"], "end": period["end"],
            "cu": {"value": value, "unit": ms.CU_HOURS, "classification": ms.MEASURED, "metric": "billed_capacity_cu",
                   "period": period, "source": "cost.csv"},
            "cost": {"value": cost, "unit": "currency", "currency": "EUR", "classification": ms.MEASURED,
                     "metric": "billed_cost", "period": period, "source": "cost.csv"}}


def recon(imports, runs, estimates=None, ws_names=None, since=SINCE, until=NOW, **kw):
    return rc.reconcile(imports, runs, estimates or {}, since, until, ws_names, now=NOW, **kw)


def states(r):
    return {b["job_id"]: b["state"] for b in r["by_run"]}


# ---- the coverage window: an import covers its workspace from its first row's start to its last row's end,
# give or take TOLERANCE_S

def test_a_run_up_to_the_tolerance_outside_an_imports_rows_is_covered_and_one_second_more_isnt():
    rows = imp(op("nb_a", at("14:00:00"), at("15:00:00")))
    r = recon([rows], [run("in_before", "nb_x", at("13:55:00"), at("13:56:00")),
                       run("out_before", "nb_x", at("13:54:59"), at("13:56:00")),
                       run("in_after", "nb_x", at("15:05:00"), at("15:06:00")),
                       run("out_after", "nb_x", at("15:05:01"), at("15:06:00"))])
    assert rc.TOLERANCE_S == 300 and r["tolerance_s"] == 300
    assert states(r) == {"in_before": "no_row", "out_before": "not_covered", "in_after": "no_row", "out_after": "not_covered"}


def test_an_imports_span_ends_at_its_last_rows_end_not_its_start():
    r = recon([imp(op("nb_a", at("14:00:00"), at("15:00:00")))], [run("j", "nb_x", at("14:30:00"), at("14:31:00"))])
    assert states(r) == {"j": "no_row"}


def test_a_capacity_bill_covers_no_run():
    # A cost row has no workspace; a run that recorded no workspace name mustn't count as covered by it.
    r = recon([imp(bill("F64", 16))], [run("j", "nb_x", at("10:00:00"), at("10:05:00"), ws="")])
    assert states(r) == {"j": "not_covered"}


# ---- which runs count

def test_a_run_needs_a_start_inside_the_period_and_a_job_id():
    r = recon([], [run("before", "nb", (SINCE - dt.timedelta(hours=1)).isoformat(), (SINCE + dt.timedelta(hours=1)).isoformat()),
                   run("no_start", "nb", None, at("10:00:00")),
                   run("", "nb", at("10:00:00"), at("10:05:00")),
                   run("ok", "nb", at("10:00:00"), at("10:05:00"))])
    assert [b["job_id"] for b in r["by_run"]] == ["ok"] and r["runs"]["total"] == 1


def test_a_run_matches_only_rows_that_start_before_it_finished():
    r = recon([imp(op("nb_a", at("14:10:00"), at("14:12:00")))], [run("j", "nb_a", at("14:00:00"), at("14:05:00"))])
    assert r["matched"]["runs"] == 0 and r["unmatched"]["by_reason"] == {rc.NO_RUN: 1}


def test_a_run_still_going_is_measured_up_to_now():
    r = recon([imp(op("nb_a", at("14:10:00"), at("14:12:00")))], [run("j", "nb_a", at("14:00:00"), None)])
    measured = r["by_run"][0]["measured"]
    assert r["matched"]["runs"] == 1 and measured["period"] == {"start": at("14:00:00"), "end": NOW.isoformat()}


def test_by_run_lists_the_newest_first_and_at_most_200():
    many = [run(f"j{i:03}", "nb", (SINCE + dt.timedelta(minutes=i)).isoformat(), None) for i in range(201)]
    r = recon([], many)
    assert len(r["by_run"]) == 200 and r["runs"]["total"] == 201
    assert r["by_run"][0]["job_id"] == "j200" and r["by_run"][-1]["job_id"] == "j001"


# ---- which rows count

def test_a_timed_row_outside_the_period_isnt_counted():
    early = op("nb_a", "2026-09-01T10:00:00+00:00", "2026-09-01T10:05:00+00:00")
    r = recon([imp(early)], [])
    assert r["rows"] == 0 and r["imported"] is None and r["rows_outside_period"] == 0


def test_an_item_total_wholly_outside_the_period_isnt_counted_as_partly_inside():
    total = {"level": "item", "workspace": "Sales Dev", "item": "nb_a", "start": None,
             "cu": cu(500, "2026-08-01T00:00:00+00:00", "2026-08-31T00:00:00+00:00")}
    r = recon([imp(total)], [])
    assert r["rows"] == 0 and r["rows_outside_period"] == 0


# ---- how a match and a run read

def test_one_matched_row_is_described_in_the_singular_and_carries_the_runs_workspace():
    r = recon([imp(op("nb_a", at("14:01:00"), at("14:02:00")))], [run("j", "nb_a", at("14:00:00"), at("14:05:00"))])
    measured = r["by_run"][0]["measured"]
    assert measured["method"].startswith("Sum of 1 imported row matched")
    assert measured["workspace_id"] == "w1"
    two = recon([imp(op("nb_a", at("14:01:00"), at("14:02:00")), op("nb_a", at("14:03:00"), at("14:04:00")))],
                [run("j", "nb_a", at("14:00:00"), at("14:05:00"), workspace_id="")])
    assert two["by_run"][0]["measured"]["method"].startswith("Sum of 2 imported rows matched")
    assert two["by_run"][0]["measured"]["workspace_id"] is None


def test_every_count_starts_at_zero_and_counts_one_per_run():
    r = recon([imp(op("nb_a", at("14:01:00"), at("14:02:00")))],
              [run("m", "nb_a", at("14:00:00"), at("14:05:00")),
               run("n", "?", at("14:10:00"), at("14:11:00"), kind="Something new"),
               run("r", "nb_b", at("14:04:00"), at("14:06:00")),
               run("c", "nb_c", at("09:00:00"), at("09:01:00"))])
    assert r["runs"] == {"total": 4, "matched": 1, "no_row": 1, "not_covered": 1, "no_item_name": 1}


def test_a_run_without_an_estimate_says_why_by_its_kind():
    r = recon([], [run("meta", "all 3 entities", at("10:00:00"), at("10:01:00"), kind="Metadata run"),
                   run("nb", "nb_a", at("11:00:00"), at("11:01:00"))])
    reasons = {b["job_id"]: b["estimated"]["reason"] for b in r["by_run"]}
    assert reasons == {"meta": "Metadata runs are estimated per table from the run log, not per Fabric job.",
                       "nb": "No estimate for this run."}


def test_by_run_names_the_workspace_the_run_recorded_or_its_id_maps_to():
    r = recon([], [run("named", "nb", at("10:00:00"), at("10:01:00")),
                   run("mapped", "nb", at("11:00:00"), at("11:01:00"), ws="")], ws_names={"w1": "Mapped WS"})
    assert {b["job_id"]: b["workspace"] for b in r["by_run"]} == {"named": "Sales Dev", "mapped": "Mapped WS"}


def test_the_measured_to_estimate_ratio_keeps_three_places():
    est = ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_SECONDS, 1200, method="run time x CU", assumptions=["1 CU"], run_id="j")
    r = recon([imp(op("nb_a", at("14:01:00"), at("14:02:00"), value=2000.75))],
              [run("j", "nb_a", at("14:00:00"), at("14:05:00"))], {"j": est})
    assert r["matched"]["measured_to_estimate"] == 1.667


# ---- item names

def test_an_item_name_the_run_recorded_wins():
    assert rc.item_name({"kind": "Metadata run", "item_name": "nb_own"}) == "nb_own"


def test_table_maintenance_without_its_table_has_no_item_name():
    assert rc.item_name({"kind": "Table maintenance", "name": "LH_Silver", "table": ""}) is None
    assert rc.item_name({"kind": "Table maintenance", "name": "LH_Silver other", "table": "sales"}) is None


# ---- the bill per capacity

def test_capacities_are_listed_by_name_even_with_one_row():
    r = recon([imp(bill("Zeta", 10), bill("alpha", 11), bill("alpha", 12))], [])
    assert [(c["capacity"], c["rows"]) for c in r["billed"]] == [("alpha", 2), ("Zeta", 1)]


def test_the_busiest_eight_meters_are_named():
    rows = [bill("F64", 10, meter=f"m{i}") for i in range(9)] + [bill("F64", 11, meter="m0")]
    [cap] = recon([imp(*rows)], [])["billed"]
    assert len(cap["meters"]) == 8 and next(iter(cap["meters"])) == "m0" and cap["meters"]["m0"] == 2


def test_of_two_imports_reaching_as_far_the_one_imported_last_wins():
    older = imp(bill("F64", 10, value=40.0, slot="s"), id="a", imported_at="2026-10-16T09:00:00+00:00")
    newer = imp(bill("F64", 10, value=96.0, slot="s"), id="b", imported_at="2026-10-16T10:00:00+00:00")
    for order in ([older, newer], [newer, older]):
        [cap] = recon(order, [])["billed"]
        assert cap["cu"]["value"] == pytest.approx(96.0)
