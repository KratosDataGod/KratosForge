"""Mutation-testing follow-up for scorecard.py (docs/quality/mutation-testing.md, batch 40).

Each test pins a behaviour a surviving mutant changed: which side was faster (and the tie), the
estimated Fabric cost and its assumptions, the PC side's measurement, the correctness counts and
the notes a scorecard carries."""

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop import scorecard

CFG = {"python_cu": 1, "spark_cu": 8, "price_per_cu_hour": 0.18, "currency": "USD"}
START, END = "2026-10-18T01:00:00+00:00", "2026-10-18T01:05:00+00:00"


# ---- _faster ----------------------------------------------------------------------------------

@pytest.mark.parametrize("pc, fabric", [(None, 10), (10, None), (0, 10), (10, 0), (None, None)])
def test_no_comparison_without_both_times(pc, fabric):
    assert scorecard._faster(pc, fabric, "end to end") == ""


def test_this_pc_faster():
    assert scorecard._faster(10, 25, "end to end") == "This PC was 2.5x faster end to end"


def test_fabric_faster():
    assert scorecard._faster(30, 10, "on run time") == "Fabric was 3.0x faster on run time"


def test_a_tie_is_reported_for_this_pc():
    # pc == fabric is "This PC ... 1.0x", not "Fabric ... 1.0x" (the <= boundary).
    assert scorecard._faster(12, 12, "end to end") == "This PC was 1.0x faster end to end"


# ---- fabric_cost --------------------------------------------------------------------------------

def test_python_cost_figures_and_assumptions():
    c = scorecard.fabric_cost(100, "python", CFG, at=START, ids={"run_id": "j1", "workspace_id": "", "x": None})
    assert c["kind"] == "estimated" and c["classification"] == ms.ESTIMATED
    assert c["cu_seconds"] == 100.0
    assert c["amount"] == round(100 / 3600 * 0.18, 4) == 0.005
    assert c["currency"] == "USD"
    timing, price, billed = c["assumptions"]
    assert timing == "Job time 100.0 s (from Fabric's job API) x 1 CU for a Python notebook (Monitor > Cost settings)."
    assert price.startswith("Priced at 0.18 USD per CU hour (pay-as-you-go).") and "not an added charge" in price
    assert billed == "Billed numbers are in Fabric's Capacity Metrics app."
    m = c["measurement"]
    assert m["value"] == 100.0 and m["source"] == "Fabric job API" and m["assumptions"] == [timing, billed]
    assert m["period"]["start"] == START
    # Empty and None ids are dropped; the rest are kept.
    assert m["run_id"] == "j1" and m.get("workspace_id") is None and "x" not in m


def test_spark_cost_uses_spark_cus_and_names_spark():
    c = scorecard.fabric_cost(10, "spark", CFG)
    assert c["cu_seconds"] == 80.0 and c["measurement"]["value"] == 80.0
    assert "x 8 CU for a Spark notebook" in c["assumptions"][0]


def test_cu_seconds_round_to_one_place_and_amount_to_four():
    c = scorecard.fabric_cost(1.26, "spark", dict(CFG, price_per_cu_hour=1))
    assert c["cu_seconds"] == 10.1
    assert c["amount"] == round(10.1 / 3600, 4) == 0.0028


def test_the_amount_is_cu_hours_times_the_price():
    # 36,000 CU-seconds are exactly 10 CU hours: 10 x 1.5 = 15.0, not 14.9958 (3,601 seconds an hour).
    assert scorecard.fabric_cost(36000, "python", dict(CFG, price_per_cu_hour=1.5))["amount"] == 15.0


@pytest.mark.parametrize("secs", [None, 0])
def test_no_job_time_means_not_available(secs):
    c = scorecard.fabric_cost(secs, "python", CFG, at=START, ids={"run_id": "j1"})
    assert c["kind"] == "not available" and c["classification"] == ms.UNKNOWN
    assert c["text"] == "Fabric didn't report the job's start and end, so capacity use isn't known."
    assert c["measurement"]["value"] is None and c["measurement"]["run_id"] == "j1"
    assert c["measurement"]["period"]["start"] == START
    assert "cu_seconds" not in c and "amount" not in c


# ---- _pc_cost -----------------------------------------------------------------------------------

def test_pc_cost_is_measured_zero_at_the_finish_time():
    out = scorecard._pc_cost({"id": "p1", "started_at": START, "finished_at": END})
    assert out["kind"] == "not available" and out["text"] == scorecard.PC_CAPACITY
    m = out["measurement"]
    assert m["classification"] == ms.MEASURED and m["value"] == 0.0 and m["run_id"] == "p1"
    assert m["collected_at"] == END and m["period"]["start"] == START
    assert m["source"] == "Fabric Desktop proof record"


def test_pc_cost_falls_back_to_the_start_time():
    m = scorecard._pc_cost({"started_at": START})["measurement"]
    assert m["classification"] == ms.MEASURED and m["collected_at"] == START and m.get("run_id") is None


def test_pc_cost_without_a_time_is_unknown():
    out = scorecard._pc_cost({"id": "p1"})
    m = out["measurement"]
    assert m["classification"] == ms.UNKNOWN and m["value"] is None and m["reason"] == "The proof record has no run time."
    assert m["run_id"] == "p1" and out["kind"] == "not available"


# ---- build ----------------------------------------------------------------------------------------

def _record(**over):
    rec = {"id": "p1", "started_at": START, "finished_at": END,
           "pc": {"seconds": 20, "run": 1},
           "fabric": {"job_seconds": 90, "total_seconds": 50, "run_seconds": 10, "start_seconds": 30,
                      "engine": "python", "job_id": "j1", "workspace_id": "w1"},
           "tables": [{"level": "complete", "result": "match"}, {"level": "fingerprint", "result": "match"},
                      {"level": "complete", "result": "mismatch"}, {"result": "match"}],
           "verdict": {"verdict": "pass", "text": "All tables match."},
           "machine": {"cores": 8, "memory_gb": 32}}
    rec.update(over)
    return rec


def test_correctness_counts_tables_matches_and_levels():
    c = scorecard.build(_record(), CFG)["correctness"]
    assert c == {"verdict": "pass", "text": "All tables match.", "tables": 4, "matched": 3,
                 "levels": {"complete": 2, "fingerprint": 1}}


def test_time_and_resources():
    card = scorecard.build(_record(), CFG)
    t = card["time"]
    assert t["pc"] == {"seconds": 20, "kind": "measured"}
    assert t["fabric"] == {"total_seconds": 50, "job_seconds": 90, "run_seconds": 10, "startup_seconds": 30,
                           "kind": "measured"}
    assert t["faster"] == "This PC was 2.5x faster end to end"
    assert t["faster_run"] == "Fabric was 2.0x faster on run time alone, leaving out Fabric's session start"
    r = card["resources"]
    assert r["pc"]["cores"] == 8 and r["pc"]["memory_gb"] == 32 and r["pc"]["kind"] == "measured"
    assert r["fabric"]["kind"] == "not available"


def test_cost_text_for_an_estimate():
    cost = scorecard.build(_record(), CFG)["cost"]
    assert cost["text"] == "Fabric: about 90 CU-seconds, about 0.0045 USD at pay-as-you-go prices (estimate)."
    assert cost["fabric"]["measurement"]["run_id"] == "j1" and cost["fabric"]["measurement"]["workspace_id"] == "w1"
    assert cost["fabric"]["measurement"]["period"]["start"] == START


def test_cost_engine_defaults_to_python():
    fab = dict(_record()["fabric"])
    del fab["engine"]
    assert scorecard.build(_record(fabric=fab), CFG)["cost"]["fabric"]["cu_seconds"] == 90.0


def test_cost_text_and_side_when_not_run_in_fabric():
    card = scorecard.build(_record(fabric=None), CFG)
    cost = card["cost"]
    assert cost["text"] == "Fabric capacity use isn't known for this run."
    assert cost["fabric"]["kind"] == "not available" and cost["fabric"]["classification"] == ms.UNKNOWN
    assert cost["fabric"]["measurement"]["reason"] == "The notebook didn't run in Fabric."
    assert card["time"]["faster"] == "" and card["notes"] == []


def test_notes_for_fabric_and_a_kernel_process():
    pc = {"seconds": 20, "cpu_seconds": 3.0, "measured": "the notebook's own kernel process"}
    notes = scorecard.build(_record(pc=pc), CFG)["notes"]
    assert len(notes) == 3
    assert notes[0].startswith("Fabric's total includes publishing the notebook and queueing")
    assert notes[1].startswith("This PC's CPU and memory are the notebook's own kernel process's")
    assert notes[2].startswith("Fabric doesn't report a notebook run's CPU or memory")


def test_notes_for_the_engine_process_and_zero_cpu():
    # cpu_seconds of 0 is still a measurement (is not None), and without "kernel process" it's the engine's.
    pc = {"seconds": 20, "cpu_seconds": 0}
    notes = scorecard.build(_record(pc=pc), CFG)["notes"]
    assert any(n.startswith("This PC's CPU and memory are the engine process's") for n in notes)
    assert not any("kernel process" in n for n in notes)


def test_no_total_note_without_both_fabric_times():
    fab = dict(_record()["fabric"], total_seconds=None)
    notes = scorecard.build(_record(fabric=fab), CFG)["notes"]
    assert not any(n.startswith("Fabric's total") for n in notes)
    fab = dict(_record()["fabric"], job_seconds=None)
    notes = scorecard.build(_record(fabric=fab), CFG)["notes"]
    assert not any(n.startswith("Fabric's total") for n in notes)


def test_an_empty_record():
    card = scorecard.build({}, CFG)
    assert card["correctness"] == {"verdict": None, "text": None, "tables": 0, "matched": 0, "levels": {}}
    assert card["notes"] == [] and card["time"]["faster"] == ""
