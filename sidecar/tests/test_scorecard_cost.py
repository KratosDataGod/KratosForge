"""Scorecard and receipt cost carry FinOps classifications (docs/finops/cost-evidence.md)."""

from fabric_desktop import measurement as ms
from fabric_desktop import receipts, scorecard

CFG = {"python_cu": 1, "spark_cu": 8, "price_per_cu_hour": 0.18, "currency": "USD"}
RECORD = {"id": "p1", "started_at": "2026-10-18T01:00:00+00:00", "finished_at": "2026-10-18T01:05:00+00:00",
          "pc": {"seconds": 40}, "fabric": {"job_seconds": 90, "engine": "python", "job_id": "j1", "workspace_id": "w1"}}


def test_estimated_fabric_and_measured_pc():
    cost = scorecard.build(RECORD, CFG)["cost"]
    fab, pc = cost["fabric"], cost["pc"]
    assert fab["kind"] == "estimated" and fab["classification"] == ms.ESTIMATED
    m = fab["measurement"]
    assert ms.problems(m) == [] and m["metric"] == "fabric_job_cu" and m["unit"] == ms.CU_SECONDS
    assert m["value"] == fab["cu_seconds"] == 90.0 and m["run_id"] == "j1" and m["workspace_id"] == "w1"
    assert m["method"] == "run time x configured CUs" and m["assumptions"]
    # The price is a separate assumption of the amount, not of the CU measurement.
    assert not any("per CU hour" in a for a in m["assumptions"])
    assert pc["classification"] == ms.MEASURED and pc["measurement"]["value"] == 0
    # The PC's total capacity isn't a measured 0: OneLake reads and writes aren't counted (QA-008).
    assert pc["kind"] == "not available" and "capacity_cu_seconds" not in pc and "OneLake" in pc["text"]
    assert "OneLake" in pc["measurement"]["method"]
    assert pc["measurement"]["collected_at"] == "2026-10-18T01:05:00+00:00" and ms.problems(pc["measurement"]) == []


def test_unknown_is_none_never_zero():
    rec = dict(RECORD, fabric={"engine": "python", "job_id": "j1"})
    fab = scorecard.build(rec, CFG)["cost"]["fabric"]
    assert fab["classification"] == ms.UNKNOWN and fab["measurement"]["value"] is None
    assert fab["measurement"]["reason"] and "cu_seconds" not in fab
    # No Fabric side at all.
    fab = scorecard.build(dict(RECORD, fabric={}), CFG)["cost"]["fabric"]
    assert fab["classification"] == ms.UNKNOWN and fab["measurement"]["value"] is None


def test_spark_uses_its_cus():
    rec = dict(RECORD, fabric=dict(RECORD["fabric"], engine="spark"))
    assert scorecard.build(rec, CFG)["cost"]["fabric"]["measurement"]["value"] == 720.0


def test_receipt_markdown_names_each_classification():
    card = scorecard.build(RECORD, CFG)
    body = receipts.new_receipt("proof", notebook={"name": "nb"}, cost=card["cost"])
    md = receipts.to_markdown(body, receipts.receipt_id(body))
    assert "- This PC: Fabric job CU 0 CU-seconds (**Measured**)" in md
    assert "- Fabric: Fabric job CU 90 CU-seconds (**Estimated**)" in md
    receipts.canonical(body)  # still hashable: no NaN, text keys
    rec = dict(RECORD, fabric={"engine": "python"})
    body = receipts.new_receipt("proof", notebook={"name": "nb"}, cost=scorecard.build(rec, CFG)["cost"])
    assert "Fabric job CU not known (**Unknown**; Fabric didn't report" in receipts.to_markdown(body, receipts.receipt_id(body))
