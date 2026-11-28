"""ESI-507: CSV text for the eight savings reports, from the ledger the page reads.

Classifications never mix in one cell, Unknown is an empty cell labelled Unknown (never 0), a price
is a Scenario and never savings, realized savings come only from realized.realize, developer
contribution is per device until D3 and D5, readiness recommends nothing, and every text cell that
starts like a formula is protected."""

from __future__ import annotations

import csv
import io
import random

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import accruals as ac
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import corrections as co
from fabric_desktop.savings import exports as ex
from fabric_desktop.savings import pricing as pr
from fabric_desktop.savings import realized as rz
from fabric_desktop.savings import recurring as rc
from tests.test_savings_realized import AFTER, BEFORE, BILL_F64_TO_F32, CHANGE, action

FIGURES = {"Value", ex.CU_AVOIDED, "Change CU hours", "Layer after CU hours"}
CLASS_LABELS = {"Measured", "Estimated", "Scenario", "Unknown"}


def measured(v: float) -> dict:
    return ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, v, source="Capacity Metrics export",
                   collected_at="2026-11-01T00:00:00Z")


def estimated(v: float) -> dict:
    return ms.make(ms.ESTIMATED, "fabric_job_cu_avoided", ms.CU_HOURS, v, method="calibration",
                   assumptions=["median of 5 Fabric runs"])


def unknown() -> dict:
    return ms.unknown("fabric_job_cu_avoided", ms.CU_HOURS, "not measured yet")


def run(eid: str, when: str, ran: str = at.PC) -> dict:
    return {"execution_id": eid, "workload_key": "nb:sales", "baseline_version": "b1", "at": when, "ran": ran}


def opt(oid: str, after: dict | None, when: str = "2026-10-01T00:00:00Z") -> dict:
    return {"optimization_id": oid, "implemented_at": when, "action": "optimize_in_place", "state": "verified",
            "after": after}


def pc(eid: str, when: str, local: dict | None) -> dict:
    return at.attribute(run(eid, when), measured(10), [], local_avoided=local)


def fabric(eid: str, when: str, after: float = 6) -> dict:
    return at.attribute(run(eid, when, at.FABRIC), measured(10), [opt("o1", measured(after))])


def written(result: dict, *, device: str = "pc-1", created: str = "2026-11-20T00:00:00+00:00", **scope) -> list:
    """The events as a writer puts them in the ledger: provenance filled in."""
    scope = {"workspace_id": "w1", "capacity_id": "c1"} | scope
    return [e | {"installation_id": device, "created_at": created}
            for e in ac.accrue(result, scope=scope)["events"]]


def ledger() -> list:
    """A Fabric run credited to a measured optimization, two PC runs (one estimated, one unknown)
    and a PC run on another device, workspace and month."""
    return (written(fabric("f1", "2026-11-03T10:00:00Z"))
            + written(pc("p1", "2026-11-04T10:00:00Z", estimated(7)))
            + written(pc("p2", "2026-11-05T10:00:00Z", unknown()))
            + written(pc("p3", "2026-10-30T23:30:00-05:00", estimated(2)), device="pc-2", workspace_id="w2"))


def parse(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text, newline="")))


def body(report: dict) -> list[dict[str, str]]:
    """The data rows of the CSV, by header."""
    rows = ex.table(report)
    head, data = rows[0], rows[1:]
    data = data[:data.index([])] if [] in data else data
    return [dict(zip(head, r, strict=True)) for r in data]


def notes(report: dict) -> list[str]:
    rows = ex.table(report)
    return [r[1] for r in rows if r and r[0] == "Note"]


def all_reports(events: list) -> dict[str, dict]:
    priced = pr.value_runs([fabric("f1", "2026-11-03T10:00:00Z")],
                           [pr.scenario(scenario="List price", rate_per_cu_hour=0.18, currency="USD",
                                        effective_from="2026-01-01T00:00:00Z", pricing_model=pr.PAY_AS_YOU_GO,
                                        confidence="confirmed", source="Azure price sheet")], "List price")
    real = [rz.realize(action(), BILL_F64_TO_F32, BEFORE, AFTER, changes=[CHANGE])]
    vals = [{"validation_id": "va-1", "optimization_id": "o1", "result": "verified", "reason": "ok",
             "at": "2026-11-10T00:00:00Z"}]
    return {
        "executive_summary": ex.executive_summary(events, realized=real, capacity_value=priced),
        "workspace": ex.workspace(events),
        "capacity": ex.capacity(events, realized=real),
        "developer_contribution": ex.developer_contribution(events),
        "monthly_statement": ex.monthly_statement(events, realized=real, capacity_value=priced),
        "audit_history": ex.audit_history(events),
        "estimated_vs_verified": ex.estimated_vs_verified(events, validations=vals),
        "capacity_readiness": ex.capacity_readiness(events, validations=vals, realized=real),
    }


# ---------- the shape every report keeps

def test_there_are_eight_reports_each_with_a_header_row_first():
    reports = all_reports(ledger())
    assert set(reports) == set(ex.REPORTS) and len(ex.REPORTS) == 8
    for name, report in reports.items():
        text = ex.to_csv(report)
        rows = parse(text)
        assert rows[0] == report["columns"], name
        assert all(len(r) == len(report["columns"]) for r in rows[1:len(report["rows"]) + 1]), name
        assert "\u2014" not in text and "\u2013" not in text, name  # no long dashes
        assert text.endswith("\r\n") and ex.to_bytes(report) == text.encode("utf-8")


def test_every_figure_column_has_its_classification_next_to_it():
    for name, report in all_reports(ledger()).items():
        cols = report["columns"]
        for i, c in enumerate(cols):
            if c in FIGURES:
                assert "classification" in cols[i + 1].lower(), (name, c)
        for row in body(report):
            for i, c in enumerate(cols):
                if c in FIGURES and row[cols[i + 1]] != "":
                    assert row[cols[i + 1]] in CLASS_LABELS, (name, row)


def test_no_report_has_user_columns():
    for report in all_reports(ledger()).values():
        assert not any(w in c.lower() for c in report["columns"] for w in ("user", "person", "developer", "email"))


# ---------- classifications never mix

def test_measured_and_estimated_are_separate_rows_and_never_added():
    rows = body(ex.executive_summary(ledger()))
    cu = {r["Classification"]: r for r in rows if r["Measure"] == ex.CU_AVOIDED}
    assert cu["Measured"]["Value"] == "4" and cu["Measured"]["Count"] == "1"  # 10 before, 6 after
    assert cu["Estimated"]["Value"] == "9" and cu["Estimated"]["Count"] == "2"  # 7 + 2, both on a PC
    assert not any(r["Value"] in ("13", "11") for r in rows)


def test_unknown_is_an_empty_cell_labelled_unknown_never_zero():
    rows = body(ex.executive_summary(ledger()))
    [u] = [r for r in rows if r["Measure"] == ex.CU_AVOIDED and r["Classification"] == "Unknown"]
    assert u["Value"] == "" and u["Count"] == "1"
    ws = body(ex.workspace(ledger()))
    assert {(r["Workspace"], r["Classification"], r[ex.CU_AVOIDED]) for r in ws} == {
        ("w1", "Measured", "4"), ("w1", "Estimated", "7"), ("w1", "Unknown", ""), ("w2", "Estimated", "2")}


def test_an_empty_ledger_says_so():
    rows = body(ex.executive_summary([]))
    assert rows[0]["Measure"] == ex.CU_AVOIDED and rows[0]["Value"] == "0" and rows[0]["Count"] == "0"
    [r] = [r for r in rows if r["Measure"] == ex.REALIZED]
    assert r["Value"] == "" and r["Classification"] == "Unknown"  # no bill: Unknown, not 0


# ---------- the ledger is read by its chains (rule 4)

def test_a_correction_shows_the_layer_as_its_head_says_and_a_withdrawal_stays_unknown():
    first = written(pc("p1", "2026-11-04T10:00:00Z", estimated(7)))
    fix = co.correct(first, pc("p1", "2026-11-04T10:00:00Z", estimated(5)), reason="Calibration revised")
    rows = body(ex.workspace(first + fix))
    assert [(r["Classification"], r[ex.CU_AVOIDED]) for r in rows] == [("Estimated", "5")]

    gone = co.correct(first + fix, pc("p1", "2026-11-04T10:00:00Z", unknown()))
    rows = body(ex.workspace(first + fix + gone))
    assert [(r["Classification"], r[ex.CU_AVOIDED]) for r in rows] == [("Unknown", "")]  # the chain sums to 0

    audit = body(ex.audit_history(first + fix + gone))
    assert [r["Type"] for r in audit] == ["accrual", "correction", "correction"]
    assert [r["Current"] for r in audit] == ["No", "No", "Yes"]
    assert audit[1]["Change CU hours"] == "-2" and audit[1]["Layer after CU hours"] == "5"  # not protected
    assert audit[2]["Layer after CU hours"] == "" and audit[2]["Layer after classification"] == "Unknown"
    assert audit[1]["Corrects"] == audit[0]["Event"] and audit[2]["Corrects"] == audit[1]["Event"]
    assert audit[1]["Reason"].startswith("Re-attributed")
    assert audit[0]["Workspace"] == audit[2]["Workspace"] == "w1"  # scope from the accrual


def test_a_reversed_run_leaves_the_totals_and_stays_in_the_audit_history():
    first = written(pc("p1", "2026-11-04T10:00:00Z", estimated(7)))
    back = co.reverse(first, reason="Never deployed")
    assert body(ex.workspace(first + back)) == []
    audit = body(ex.audit_history(first + back))
    assert [r["Type"] for r in audit] == ["accrual", "reversal"]
    assert audit[1]["Layer removed"] == "Yes" and audit[1]["Layer after CU hours"] == ""


def test_ledger_problems_become_a_note():
    first = written(pc("p1", "2026-11-04T10:00:00Z", estimated(7)))
    fix = co.correct(first, pc("p1", "2026-11-04T10:00:00Z", estimated(5)))
    orphan = [fix[0] | {"corrects": "nowhere", "ledger_event_id": "x1"}]
    assert any("problem" in n for n in notes(ex.workspace(first + orphan)))
    assert not any("problem" in n for n in notes(ex.workspace(first)))


# ---------- deterministic

def test_row_order_and_text_do_not_depend_on_input_order():
    events = ledger()
    want = {n: ex.to_csv(r) for n, r in all_reports(events).items()}
    for seed in range(5):
        shuffled = events[:]
        random.Random(seed).shuffle(shuffled)
        assert {n: ex.to_csv(r) for n, r in all_reports(shuffled).items()} == want


def test_one_event_from_two_devices_counts_once_for_the_device_that_wrote_it_first():
    r = pc("p1", "2026-11-04T10:00:00Z", estimated(7))
    a = written(r, device="pc-b", created="2026-11-20T00:00:00+00:00")
    b = written(r, device="pc-a", created="2026-11-21T00:00:00+00:00")
    for events in (a + b, b + a):
        rows = body(ex.developer_contribution(events))
        assert [(x["Device"], x[ex.CU_AVOIDED], x["Runs"]) for x in rows] == [("pc-b", "7", "1")]


# ---------- CSV safety

@pytest.mark.parametrize("bad", ["=HYPERLINK(\"http://x\")", "+1+1", "-2+3", "@SUM(A1)", "\tx", "\rx"])
def test_text_that_starts_like_a_formula_gets_an_apostrophe(bad):
    events = written(pc("p1", "2026-11-04T10:00:00Z", estimated(7)), workspace_id=bad)
    [row] = body(ex.workspace(events, names={bad: bad}))
    assert row["Workspace"] == "'" + bad and row["Name"] == "'" + bad


def test_numbers_are_plain_decimals_and_a_regression_stays_negative():
    events = written(at.attribute(run("f1", "2026-11-03T10:00:00Z", at.FABRIC), measured(10),
                                  [opt("o1", measured(6)), opt("o2", measured(7), "2026-10-02T00:00:00Z")]))
    rows = body(ex.estimated_vs_verified(events))
    by = {r["Optimization"]: r for r in rows}
    assert by["o1"][ex.CU_AVOIDED] == "4" and by["o2"][ex.CU_AVOIDED] == "-1"
    assert ex._cell(1 / 3) == "0.333333" and ex._cell(1e-9) == "0" and ex._cell(-0.0) == "0"
    with pytest.raises(ValueError):
        ex._cell(float("nan"))


def test_non_ascii_text_is_utf8():
    events = written(pc("p1", "2026-11-04T10:00:00Z", estimated(7)))
    report = ex.workspace(events, names={"w1": "Ventes été"})
    assert "Ventes été".encode() in ex.to_bytes(report)


# ---------- developer contribution and readiness

def test_developer_contribution_is_per_device_and_says_why():
    report = ex.developer_contribution(ledger(), device_labels={"pc-2": "Build box"})
    assert report["columns"][:2] == ["Device", "Device label"]
    rows = body(report)
    assert {(r["Device"], r["Device label"]) for r in rows} == {("pc-1", ""), ("pc-2", "Build box")}
    assert any("D3" in n and "D5" in n for n in notes(report))


def test_capacity_readiness_reports_evidence_and_leaves_right_sizing_to_the_planner():
    report = all_reports(ledger())["capacity_readiness"]
    rows = body(report)
    assert any("OE-040" in n and "recommends nothing" in n for n in notes(report))
    head = [r for r in rows if r["Evidence"] == ex.HEADROOM]
    assert head and all(r["Value"] == "" and r["Classification"] == "Unknown" for r in head)
    [v] = [r for r in rows if r["Evidence"] == "Optimizations verified"]
    assert v["Capacity"] == "c1" and v["Value"] == "1"
    assert not any("recommend" in c.lower() or "sku" in c.lower() for c in report["columns"])


# ---------- realized, scenario and projection

def test_realized_savings_come_only_from_realize_and_unknown_stays_empty():
    yes = rz.realize(action(), BILL_F64_TO_F32, BEFORE, AFTER, changes=[CHANGE])
    no = rz.realize(None, [], BEFORE, AFTER)
    rows = body(ex.executive_summary([], realized=[yes, no]))
    real = {r["Classification"]: r for r in rows if r["Measure"] == ex.REALIZED}
    assert real["Measured"]["Value"] == f"{yes['realized_savings']['value']:g}" and real["Measured"]["Unit"] == "USD"
    assert real["Unknown"]["Value"] == "" and real["Unknown"]["Count"] == "1"
    cap = body(ex.capacity([], realized=[yes]))
    assert [r["Measure"] for r in cap] == [ex.REALIZED, ex.NOT_CLAIMED]
    assert cap[0]["Period start"] == "2026-11-09T00:00:00+00:00"
    with pytest.raises(ValueError, match="realize"):
        ex.executive_summary([], realized=[{"realized": True, "realized_savings": measured(5)}])
    with pytest.raises(ValueError, match="Measured"):
        ex.executive_summary([], realized=[yes | {"realized_savings": yes["realized_savings"] | {
            "classification": ms.ESTIMATED}}])


def test_capacity_value_is_a_scenario_and_never_called_savings():
    report = all_reports(ledger())["executive_summary"]
    [row] = [r for r in body(report) if r["Measure"] == ex.CAPACITY_VALUE]
    assert row["Classification"] == "Scenario" and row["Value"] == "0.72" and row["Unit"] == "USD"
    assert "savings" not in row["Measure"].lower() and "Not savings" in row["Note"]
    month = [r for r in body(all_reports(ledger())["monthly_statement"]) if r["Measure"] == ex.CAPACITY_VALUE]
    assert [(r["Month"], r["Classification"]) for r in month] == [("2026-11", "Scenario")]
    priced = pr.value_runs([fabric("f1", "2026-11-03T10:00:00Z")], [], "List price")
    bad = priced | {"runs": [x | {"value": measured(1)} for x in priced["runs"]]}
    with pytest.raises(ValueError, match="Scenario"):
        ex.executive_summary([], capacity_value=bad)


def test_a_scenario_with_no_runs_priced_says_scenario_in_the_classification_column():
    priced = pr.value_runs([], [], "List price")
    rows = [r for r in body(ex.executive_summary([], capacity_value=priced)) if r["Measure"] == ex.CAPACITY_VALUE]
    assert [(r["Value"], r["Classification"], r["Unit"], r["Count"]) for r in rows] == [("0", "Scenario", "", "0")]
    assert rows[0]["Note"] == "Scenario List price. No runs priced."


def test_the_projection_is_estimated_and_unknown_projections_are_counted_beside():
    results = [pc(f"p{d}", f"2026-11-{d:02d}T09:00:00Z", estimated(6)) for d in range(20, 26)]
    for r in results:
        r["complete"] = True
    yes = rc.project(results, workload_key="nb:sales", baseline_version="b1", now="2026-11-30T00:00:00Z")
    no = rc.project([], workload_key="nb:other", baseline_version="b1", now="2026-11-30T00:00:00Z")
    rows = [r for r in body(ex.executive_summary([], projections=[yes, no])) if r["Measure"] == ex.PROJECTION]
    assert [(r["Classification"], r["Count"]) for r in rows] == [("Estimated", "1"), ("Unknown", "1")]
    assert rows[1]["Value"] == ""


# ---------- by month and by source

def test_the_monthly_statement_cuts_months_in_utc():
    rows = body(ex.monthly_statement(ledger()))
    cu = [(r["Month"], r["Classification"], r["Value"]) for r in rows if r["Measure"] == ex.CU_AVOIDED]
    # p3 ran at 23:30 on 30 October in UTC-5, which is 31 October in UTC
    assert cu == [("2026-10", "Estimated", "2"), ("2026-11", "Measured", "4"), ("2026-11", "Estimated", "7"),
                  ("2026-11", "Unknown", "")]


def test_estimated_versus_verified_lists_each_source_with_its_latest_validation():
    vals = [{"row": {"validation_id": "va-1", "optimization_id": "o1", "result": "validating", "reason": "few",
                     "at": "2026-11-05T00:00:00Z"}},
            {"validation_id": "va-2", "optimization_id": "o1", "result": "verified", "reason": "ok",
             "at": "2026-11-10T00:00:00Z"},
            {"validation_id": "va-3", "optimization_id": "o9", "result": "failed", "reason": "outputs differ",
             "at": "2026-11-11T00:00:00Z"}]
    rows = body(ex.estimated_vs_verified(ledger(), validations=vals))
    got = [(r["Source"], r["Optimization"], r["Classification"], r[ex.CU_AVOIDED], r["Validation"]) for r in rows]
    assert got == [("Optimization", "o1", "Measured", "4", "Verified"),
                   ("Optimization", "o9", "Unknown", "", "Failed"),
                   ("This PC", "", "Estimated", "9", ""), ("This PC", "", "Unknown", "", "")]
    with pytest.raises(ValueError, match="result"):
        ex.estimated_vs_verified([], validations=[{"optimization_id": "o1", "result": "great"}])


def test_export_names_the_reports():
    assert parse(ex.export("workspace", ledger()))[0][0] == "Workspace"
    with pytest.raises(ValueError, match="no 'leaderboard' report"):
        ex.export("leaderboard", [])
