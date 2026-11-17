"""OE-004: an assessment as rows for the shared Optimize lakehouse (opportunities/share.py)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from fabric_desktop import audit, server
from fabric_desktop.opportunities import assess, fixture, share
from fabric_desktop.optimize import setup
from fabric_desktop.optimize.installation import Installation
from fabric_desktop.optimize.lakehouse import LocalLakehouse
from fabric_desktop.optimize.shared import Shared
from test_optimize_service import svc  # noqa: F401 - the fixture is used by name


def _result(**kw):
    """The fixture's data assessed as if it were real (fixture=False), to have every rule fire."""
    d = fixture.load()
    return assess.assess([d["source"]], usage_imports=d["usage_imports"], price=d["price"], now=d["now"], days=30,
                         scope={"kind": "workspace", "workspace_id": "f1f1f1f1-0000-4000-8000-000000000001"}, **kw)


@pytest.fixture
def lake(tmp_path: Path) -> LocalLakehouse:
    lh = LocalLakehouse(tmp_path / "lakehouse")
    setup.initialize(lh, Installation(tmp_path / "setup" / "optimize"), approve=True)
    return lh


def _pc(tmp_path: Path, lake: LocalLakehouse, name: str = "pc1") -> Shared:
    return Shared(lake, Installation(tmp_path / name / "optimize"))


def _record(me: Shared, rows: dict) -> dict:
    return me.record_analysis(rows["run"], rows["artifacts"], rows["findings"], rows["recommendations"])


def test_a_demonstration_fixture_is_never_shared():
    d = fixture.load()
    demo = assess.assess([d["source"]], usage_imports=d["usage_imports"], price=d["price"], now=d["now"], days=30,
                         scope={"kind": "demo"}, fixture=True)
    with pytest.raises(ValueError, match="demonstration fixture"):
        share.analysis_rows(demo)


def test_one_finding_and_one_recommendation_per_opportunity():
    r = _result()
    rows = share.analysis_rows(r)
    assert len(rows["findings"]) == len(r["opportunities"]) == len(rows["recommendations"])
    by_target = {f["target"]: f for f in rows["findings"]}
    for o in r["opportunities"]:
        f = by_target[o["id"]]
        assert f["rule_id"] == share.rule_id(o["rule"]) and f["rule_id"].startswith("oe.")
        assert f["title"] == o["title"]
        assert json.loads(f["evidence"])[0]["kind"] == o["evidence"][0]["kind"]
    rec = next(x for x in rows["recommendations"] if x["target"] == r["opportunities"][0]["id"])
    assert rec["kind"] == r["opportunities"][0]["action"]
    assert json.loads(rec["impact"])["basis"] == r["opportunities"][0]["impact"]["basis"]
    # every workload read is an artifact, once
    keys = [(a["workspace_id"], a["item_id"]) for a in rows["artifacts"]]
    assert len(keys) == len(set(keys)) == len(r["workloads"])


def test_the_kept_item_anchors_a_duplicate_and_uncounted_ones_are_info():
    r = _result()
    rows = share.analysis_rows(r)
    dup = next(o for o in r["opportunities"] if o["rule"] == "OE-DUP-NB")
    keep = next(a for a in dup["artifacts"] if a["role"] == "keep")
    f = next(f for f in rows["findings"] if f["target"] == dup["id"])
    assert (f["workspace_id"], f["item_id"]) == (keep["workspace_id"], keep["item_id"])
    assert f["severity"] == "high"
    for o in r["opportunities"]:
        if o["counted"] is False:
            assert next(f for f in rows["findings"] if f["target"] == o["id"])["severity"] == "info"


def test_free_text_and_evidence_lose_anything_credential_shaped():
    r = _result()
    o = r["opportunities"][0]
    o["title"] = o["title"] + " password=hunter2"
    o["recommendation"] = "Use AccountKey=abc123def456ghi789 to read it"
    o["evidence"][0]["text"] = "sig=deadbeefdeadbeefdeadbeef&x=1 Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc"  # pragma: allowlist secret (a made-up token the test checks is removed)
    rows = share.analysis_rows(r)
    blob = json.dumps(rows)
    for secret in ("hunter2", "abc123def456ghi789", "deadbeefdeadbeefdeadbeef", "eyJhbGciOiJIUzI1NiJ9"):
        assert secret not in blob, secret


def test_the_same_assessment_has_the_same_run_and_a_budget_cut_one_is_partial():
    r = _result()
    assert share.analysis_rows(r)["run"]["run_id"] == share.analysis_rows(copy.deepcopy(r))["run"]["run_id"]
    assert share.analysis_rows(r)["run"]["status"] == "complete"
    r["scope"]["budget"] = {"reached": True}
    assert share.analysis_rows(r)["run"]["status"] == "partial"


def test_recorded_in_the_shared_lakehouse_and_a_dismissal_survives_the_next_assessment(tmp_path, lake):
    me = _pc(tmp_path, lake)
    first = _result()
    out = _record(me, share.analysis_rows(first))
    assert out["findings"] == len(first["opportunities"])
    assert {f["rule_id"] for f in me.findings()} >= {"oe.dup-nb", "oe.dup-copy", "oe.full-refresh"}
    recs = me.recommendations()
    assert len(recs) == len(first["opportunities"])
    dup = next(r for r in recs if r["kind"] == "eliminate")
    me.set_recommendation_state(dup["recommendation_id"], "dismissed", "they must stay isolated")

    # A later assessment on another PC finds the same opportunities: one list, and the dismissed one stays dismissed.
    later = _result()
    later["assessed_at"] = "2026-11-08T00:00:00+00:00"
    other = _pc(tmp_path, lake, "pc2")
    _record(other, share.analysis_rows(later))
    assert len(other.findings()) == len(first["opportunities"])
    open_now = {r["recommendation_id"] for r in other.recommendations()}
    assert dup["recommendation_id"] not in open_now
    assert len(open_now) == len(first["opportunities"]) - 1
    # Recording the same run twice adds nothing.
    again = _record(other, share.analysis_rows(later))
    assert len(other.findings()) == len(first["opportunities"]) and again["findings"] == len(first["opportunities"])


def test_sharing_is_an_audited_call_that_fails_closed():
    assert "optimization_share" in server.METHODS and audit.audited("optimization_share")
    # with an approval and a confirm, its audit line is written first and it's refused if that can't be
    assert audit.fails_closed("optimization_share", {"share_id": "oe-1", "approve": True, "confirm": "abc"})


def test_the_service_shares_only_an_assessment_it_kept_and_only_what_was_shown(svc):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    with pytest.raises(ValueError, match="Assess again"):
        svc.optimization_share("oe-unknown")
    result = _result()
    share_id = svc._keep_assessment(result)
    with pytest.raises(PermissionError) as asked:
        svc.optimization_share(share_id)
    would = asked.value.would
    assert would["findings"] == len(result["opportunities"]) and would["shared"] == 0
    assert "from this assessment" in str(asked.value)
    assert len(svc._optimize_shared().rows("findings")) == 0  # asking wrote nothing
    # The rows come from what this PC kept: changing the copy the app holds changes nothing.
    result["opportunities"][0]["title"] = "something the app made up"
    with pytest.raises(PermissionError, match="changed since you looked"):
        svc.optimization_share(share_id, approve=True, confirm=would["confirm"])
    svc._keep_assessment(_result())
    out = svc.optimization_share(share_id, approve=True, confirm=would["confirm"])
    assert out["shared"] == len(result["opportunities"])
    # (optimize_findings shows only workspaces this person can open; the fixture's isn't one of them)
    assert set(svc._optimize_shared().rows("findings")["rule_id"].to_list()) >= {"oe.dup-nb", "oe.full-refresh"}


def test_a_demonstration_is_not_kept_to_share_and_old_ones_drop_out(svc):  # noqa: F811
    assert "share_id" not in svc.optimization_assess(demo=True)
    ids = []
    for day in range(1, 8):
        r = _result()
        r["assessed_at"] = f"2026-11-0{day}T00:00:00+00:00"
        ids.append(svc._keep_assessment(r))
    with pytest.raises(ValueError, match="Assess again"):
        svc.optimization_share(ids[0])
    assert len(svc._kept_assessments()) == 5
