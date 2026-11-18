"""Optimize's shared tables (optimize/shared.py): what mutation testing (batch 87,
docs/quality/mutation-testing.md) showed no test pinned. The batch size, an item key's workspace,
which values are cleaned, the overhead counters starting at nought, what the record calls return,
and which of this installation's runs are already in the lakehouse. A local lakehouse folder only:
nothing reaches Fabric or OneLake."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import polars as pl
import pytest

from fabric_desktop.optimize import setup, shared
from fabric_desktop.optimize.installation import Installation
from fabric_desktop.optimize.lakehouse import LocalLakehouse
from fabric_desktop.optimize.shared import Shared


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata-env"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def lake(tmp_path):
    lh = LocalLakehouse(tmp_path / "lakehouse")
    setup.initialize(lh, Installation(tmp_path / "setup" / "optimize"), approve=True)
    return lh


def pc(tmp_path, lake, name="pc1"):
    return Shared(lake, Installation(tmp_path / name / "optimize"))


def test_writes_go_in_batches_of_5000_by_default(tmp_path, lake):
    assert shared.BATCH == 5000
    assert pc(tmp_path, lake).batch == 5000


def test_an_item_keys_workspace_is_the_part_before_the_first_slash():
    assert shared.workspace_of("w1/i1") == "w1"
    assert shared.workspace_of("w1/i1/extra") == "w1"


@pytest.mark.parametrize("value", [None, 5, "", 0.5])
def test_only_non_empty_text_is_cleaned(value):
    assert shared._clean(value) == value


def test_text_with_a_secret_is_cleaned():
    cleaned = shared._clean("password=hunter2-not-real")  # pragma: allowlist secret (a fake value)
    assert "hunter2-not-real" not in cleaned


def test_the_overhead_counters_start_at_nought(tmp_path, lake):
    me = pc(tmp_path, lake)
    assert (me.rows_sent, me.commits) == (0, 0)
    me.record_local_executions([{"execution_id": "e1"}])
    assert (me.rows_sent, me.commits) == (1, 1)


def test_recording_local_runs_returns_how_many_were_sent(tmp_path, lake):
    me = pc(tmp_path, lake)
    assert me.record_local_executions([{"execution_id": "e1"}, {"execution_id": "e2"}, {"execution_id": "e1"}]) == 2
    assert me.record_local_executions([]) == 0


def test_this_installations_runs_already_in_the_lakehouse(tmp_path, lake):
    me, other = pc(tmp_path, lake), pc(tmp_path, lake, "pc2")
    assert me.local_execution_ids() == set()
    me.record_local_executions([{"execution_id": "e1"}, {"execution_id": "e2"}])
    other.record_local_executions([{"execution_id": "e3"}])
    assert me.local_execution_ids() == {"e1", "e2"}
    assert other.local_execution_ids() == {"e3"}


def test_a_record_id_is_32_characters():
    assert len(shared.record_id("run", "pc", "r1")) == 32


def test_a_batch_is_at_least_one_row(tmp_path, lake):
    assert Shared(lake, Installation(tmp_path / "pc" / "optimize"), batch=0).batch == 1


def test_recording_no_measurements_sends_nothing(tmp_path, lake):
    assert pc(tmp_path, lake).record_measurements([]) == 0


def test_a_measurement_keeps_its_period_method_assumptions_and_reason(tmp_path, lake):
    me = pc(tmp_path, lake)
    sent = me.record_measurements([
        {"id": "m1", "classification": "Measured", "metric": "duration", "unit": "s", "value": 1.5,
         "period": {"start": "2026-10-01T00:00:00+00:00", "end": "2026-10-02T00:00:00+00:00"},
         "assumptions": ["one PC"], "reason": "timed here", "method": "stopwatch"},
        {"id": "m2", "classification": "Measured", "metric": "duration", "unit": "s", "value": 2.0, "method": ""}])
    assert sent == 2
    rows = {r["measurement_id"]: r for r in me.rows("measurements").to_dicts()}
    assert rows["m1"]["period_start"] == "2026-10-01T00:00:00+00:00"
    assert rows["m1"]["period_end"] == "2026-10-02T00:00:00+00:00"
    assert rows["m1"]["assumptions"] == '["one PC"]' and rows["m1"]["reason"] == "timed here"
    assert rows["m1"]["method"] == "stopwatch" and rows["m2"]["method"] is None
    assert rows["m2"]["period_start"] is None and rows["m2"]["assumptions"] is None and rows["m2"]["reason"] is None


def _run(me, run_id, findings, recs=(), finished="2026-10-18T10:00:00+00:00"):
    arts = [{"workspace_id": f["workspace_id"], "item_id": f["item_id"]} for f in findings]
    return me.record_analysis({"run_id": run_id, "finished_at": finished}, arts, findings, recs)


def _finding(item="i1", target="dbo.sales", severity="high"):
    return {"workspace_id": "w1", "item_id": item, "rule_id": "small_files", "target": target, "severity": severity}


def test_a_findings_target_is_kept(tmp_path, lake):
    me = pc(tmp_path, lake)
    _run(me, "r1", [_finding()])
    [f] = me.findings()
    assert f["target"] == "dbo.sales"


def test_a_recommendation_needs_a_kind(tmp_path, lake):
    me = pc(tmp_path, lake)
    with pytest.raises(ValueError, match="needs a kind"):
        _run(me, "r1", [_finding()], [{"workspace_id": "w1", "item_id": "i1", "rule_id": "small_files",
                                       "target": "dbo.sales", "kind": "  "}])


def test_findings_of_the_same_severity_are_most_recent_first(tmp_path, lake):
    me = pc(tmp_path, lake)
    _run(me, "r1", [_finding("old")], finished="2026-10-01T10:00:00+00:00")
    _run(me, "r2", [_finding("new")], finished="2026-10-05T10:00:00+00:00")
    assert [f["artifact_key"] for f in me.findings()] == ["w1/new", "w1/old"]


def test_no_recommendations_is_an_empty_list(tmp_path, lake):
    assert pc(tmp_path, lake).recommendations() == []


# ---- reading a table, with a fake lakehouse


class Lake:
    def __init__(self, answer):
        self.answer = answer

    def rows(self, name):
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


class TableNotFoundError(Exception):
    pass


@pytest.mark.parametrize("answer", [None, OSError("Table not found: fdopt_findings"), TableNotFoundError("gone")])
def test_a_missing_table_reads_as_no_rows_with_its_columns(tmp_path, answer):
    df = Shared(Lake(answer), Installation(tmp_path / "pc" / "optimize")).rows("findings")
    assert len(df) == 0 and "fingerprint" in df.columns


def test_another_read_error_is_raised(tmp_path):
    with pytest.raises(OSError, match="disk"):
        Shared(Lake(OSError("the disk is busy")), Installation(tmp_path / "pc" / "optimize")).rows("findings")


def test_rows_keep_the_first_of_each_record_in_order(tmp_path):
    df = pl.DataFrame({"record_id": ["b", "a", "b", "c"], "n": [1, 2, 3, 4]})
    out = Shared(Lake(df), Installation(tmp_path / "pc" / "optimize")).rows("findings")
    assert out["record_id"].to_list() == ["b", "a", "c"] and out["n"].to_list() == [1, 2, 4]


# ---- runs, artifacts and recommendations


def test_a_run_without_a_status_is_complete_and_an_artifact_keeps_its_item_id(tmp_path, lake):
    me = pc(tmp_path, lake)
    _run(me, "r1", [_finding()])
    me.record_analysis({"run_id": "r2", "status": "failed"}, [], [])
    assert sorted(me.rows("analysis_runs")["status"].to_list()) == ["complete", "failed"]
    [art] = me.rows("artifacts").to_dicts()
    assert (art["workspace_id"], art["item_id"], art["artifact_key"]) == ("w1", "i1", "w1/i1")


def _rec(item="i1", kind="compact", reason="many small files"):
    return {"workspace_id": "w1", "item_id": item, "rule_id": "small_files", "target": "dbo.sales",
            "kind": kind, "reason": reason}


def test_a_new_recommendation_has_no_state_reason_until_it_changes(tmp_path, lake):
    me = pc(tmp_path, lake)
    _run(me, "r1", [_finding()], [_rec()])
    [rec] = me.recommendations()
    assert rec["reason"] == "many small files" and rec["state_reason"] is None and rec["events"] == 1
    me.set_recommendation_state(rec["recommendation_id"], "accepted", "planned for Friday")
    [rec] = me.recommendations()
    assert rec["state"] == "accepted" and rec["state_reason"] == "planned for Friday" and rec["events"] == 2


def test_a_state_reason_is_cut_to_500_characters(tmp_path, lake):
    me = pc(tmp_path, lake)
    _run(me, "r1", [_finding()], [_rec()])
    [rec] = me.recommendations()
    out = me.set_recommendation_state(rec["recommendation_id"], "accepted", "x" * 600)
    assert out["state_reason"] == "x" * 500


def test_recommendations_are_latest_change_first_then_by_id(tmp_path, lake):
    me = pc(tmp_path, lake)
    _run(me, "r1", [_finding("i1"), _finding("i2"), _finding("i3")], [_rec("i1"), _rec("i2"), _rec("i3")])
    ids = sorted(r["recommendation_id"] for r in me.recommendations())
    assert [r["recommendation_id"] for r in me.recommendations()] == ids  # all opened at once: by id
    me.set_recommendation_state(ids[1], "accepted")
    assert [r["recommendation_id"] for r in me.recommendations()] == [ids[1], ids[0], ids[2]]


def test_findings_seen_together_keep_their_record_order(tmp_path, lake):
    me = pc(tmp_path, lake)
    items = [f"i{n}" for n in range(8)]
    _run(me, "r1", [_finding(i) for i in items])
    obs = me.rows("findings").sort("record_id")["artifact_key"].to_list()
    assert [f["artifact_key"] for f in me.findings()] == obs


def test_a_change_at_the_same_instant_still_sorts_after(monkeypatch):
    fixed = dt.datetime(2026, 10, 18, 10, 0, tzinfo=dt.timezone.utc)

    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(shared, "dt", SimpleNamespace(datetime=Clock, timezone=dt.timezone, timedelta=dt.timedelta))
    assert shared._after(fixed.isoformat()) == "2026-10-18T10:00:00.000001+00:00"
    assert shared._after("2026-10-18T09:00:00+00:00") == "2026-10-18T10:00:00.000000+00:00"
