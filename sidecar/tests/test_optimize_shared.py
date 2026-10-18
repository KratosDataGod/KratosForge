"""Optimize's shared results (PR-OPT-002): ids, batches, concurrent writers, deduplicated and scoped reads."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from fabric_desktop.optimize import schema, setup, shared
from fabric_desktop.optimize.installation import Installation
from fabric_desktop.optimize.lakehouse import LocalLakehouse
from fabric_desktop.optimize.shared import Shared


@pytest.fixture
def lake(tmp_path: Path) -> LocalLakehouse:
    lh = LocalLakehouse(tmp_path / "lakehouse")
    setup.initialize(lh, Installation(tmp_path / "setup" / "optimize"), approve=True)
    return lh


def pc(tmp_path: Path, name: str = "pc1", lake=None, **kw) -> Shared:
    return Shared(lake, Installation(tmp_path / name / "optimize"), **kw)


def finding(ws="w1", item="i1", rule="small_files", target="dbo.sales", **kw):
    return {"workspace_id": ws, "item_id": item, "rule_id": rule, "target": target, "severity": "high",
            "title": "Many small files", **kw}


def analysis(me: Shared, run_id: str, findings, recs=(), **run):
    arts = [{"workspace_id": f["workspace_id"], "item_id": f["item_id"], "item_type": "Lakehouse",
             "display_name": "Sales"} for f in findings]
    return me.record_analysis({"run_id": run_id, "started_at": "2026-10-18T10:00:00Z", **run}, arts, findings, recs)


def test_ids_are_stable_and_use_fabric_ids_not_names():
    key = shared.artifact_key("w1", "i1")
    assert key == "w1/i1"
    assert shared.fingerprint("r", key, "t") == shared.fingerprint("r", key, "t")
    assert shared.fingerprint("r", key, "t") != shared.fingerprint("r", key, "u")
    with pytest.raises(ValueError, match="workspace id and item id"):
        shared.artifact_key("w1", "")
    with pytest.raises(ValueError, match="rule"):
        shared.fingerprint("", key)


def test_same_problem_seen_by_two_installations_is_one_finding(tmp_path, lake):
    a, b = pc(tmp_path, "a", lake), pc(tmp_path, "b", lake)
    analysis(a, "r1", [finding()], [{"workspace_id": "w1", "item_id": "i1", "rule_id": "small_files",
                                     "target": "dbo.sales", "kind": "optimize", "impact": "high"}],
             finished_at="2026-10-18T10:01:00Z")
    analysis(b, "r1", [finding(title="Many small files (again)")],
             [{"workspace_id": "w1", "item_id": "i1", "rule_id": "small_files", "target": "dbo.sales",
               "kind": "optimize"}], finished_at="2026-10-18T11:00:00Z")
    found = a.findings()
    assert len(found) == 1
    f = found[0]
    # Same run id on two PCs is two runs: run ids are scoped to the installation.
    assert f["occurrences"] == 2 and f["installations"] == 2
    assert f["first_seen"] == "2026-10-18T10:01:00.000000+00:00"
    assert f["last_seen"] == "2026-10-18T11:00:00.000000+00:00"
    assert f["title"] == "Many small files (again)"
    recs = b.recommendations()
    assert len(recs) == 1 and recs[0]["state"] == "open" and recs[0]["impact"] == "high"
    assert len(lake.rows("fdopt_recommendations")) == 1  # opened once, whoever saw it first


def test_recording_a_run_again_adds_nothing(tmp_path, lake):
    me = pc(tmp_path, lake=lake)
    for _ in range(2):
        analysis(me, "r1", [finding(), finding(item="i2")], finished_at="2026-10-18T10:01:00Z")
    for name in ("findings", "artifacts", "analysis_runs"):
        assert len(lake.rows(schema.table(name))) == (1 if name == "analysis_runs" else 2)


def test_the_same_problem_twice_in_one_run_is_one_row(tmp_path, lake):
    me = pc(tmp_path, lake=lake)
    analysis(me, "r1", [finding(), finding(detail="seen twice")], finished_at="2026-10-18T10:01:00Z")
    assert len(lake.rows("fdopt_findings")) == 1 and len(lake.rows("fdopt_artifacts")) == 1


def test_failed_run_is_hidden_until_recorded_again(tmp_path, lake, monkeypatch):
    me = pc(tmp_path, lake=lake)
    real = setup.write

    def fail_on_runs(lake_, inst, name, rows, version=None):
        if name == "analysis_runs":
            raise ConnectionError("network down")
        return real(lake_, inst, name, rows, version)

    monkeypatch.setattr(setup, "write", fail_on_runs)
    with pytest.raises(ConnectionError):
        analysis(me, "r1", [finding()], finished_at="2026-10-18T10:01:00Z")
    assert len(lake.rows("fdopt_findings")) == 1
    assert me.findings() == []  # written, but its run isn't, so it doesn't count yet
    monkeypatch.setattr(setup, "write", real)
    analysis(me, "r1", [finding()], finished_at="2026-10-18T10:01:00Z")
    assert len(lake.rows("fdopt_findings")) == 1 and len(me.findings()) == 1


def test_large_writes_go_in_bounded_batches(tmp_path, lake, monkeypatch):
    me = pc(tmp_path, lake=lake, batch=3)
    sizes: list[tuple[str, int]] = []
    real = setup.write
    monkeypatch.setattr(setup, "write", lambda l, i, name, rows, version=None: (sizes.append((name, len(rows))),
                                                                                 real(l, i, name, rows, version))[1])
    analysis(me, "r1", [finding(item=f"i{n}") for n in range(7)], finished_at="2026-10-18T10:01:00Z")
    assert [n for name, n in sizes if name == "findings"] == [3, 3, 1]
    assert len(me.findings()) == 7


def test_concurrent_writers_lose_nothing_and_duplicate_nothing(tmp_path, lake):
    """Four installations write at once: each its own problems plus one problem they all see."""
    errors: list[BaseException] = []

    def work(n: int):
        try:
            me = pc(tmp_path, f"pc{n}", lake)
            analysis(me, "r1", [finding(item=f"own{n}"), finding(item="common")],
                     [{"workspace_id": "w1", "item_id": "common", "rule_id": "small_files", "target": "dbo.sales",
                       "kind": "optimize"}], finished_at=f"2026-10-18T10:0{n}:00Z")
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    reader = pc(tmp_path, "reader", lake)
    found = {f["artifact_key"]: f for f in reader.findings()}
    assert set(found) == {"w1/own0", "w1/own1", "w1/own2", "w1/own3", "w1/common"}
    assert found["w1/common"]["installations"] == 4
    rows = lake.rows("fdopt_findings")
    assert len(rows) == rows["record_id"].n_unique() == 8
    assert len(lake.rows("fdopt_recommendations")) == 1
    assert len(lake.rows("fdopt_analysis_runs")) == 4


def test_recommendation_lifecycle_is_events_and_checks_moves(tmp_path, lake):
    a, b = pc(tmp_path, "a", lake), pc(tmp_path, "b", lake)
    analysis(a, "r1", [finding()], [{"workspace_id": "w1", "item_id": "i1", "rule_id": "small_files",
                                     "target": "dbo.sales", "kind": "optimize", "reason": "1,200 files under 8 MB"}],
             finished_at="2026-10-18T10:01:00Z")
    rid = a.recommendations()[0]["recommendation_id"]
    with pytest.raises(ValueError, match="open can't be marked verified"):
        a.set_recommendation_state(rid, "verified")
    out = b.set_recommendation_state(rid, "dismissed", "Table is retired next month")
    assert out["previous_state"] == "open" and out["changed_by"] == b.installation.id
    assert a.recommendations() == []  # closed ones are left out by default
    closed = a.recommendations(include_closed=True)[0]
    assert closed["state"] == "dismissed" and closed["state_reason"] == "Table is retired next month"
    assert closed["reason"] == "1,200 files under 8 MB" and closed["events"] == 2
    # Seeing the problem again doesn't reopen what someone dismissed.
    analysis(a, "r2", [finding()], [{"workspace_id": "w1", "item_id": "i1", "rule_id": "small_files",
                                     "target": "dbo.sales", "kind": "optimize"}], finished_at="2026-10-18T12:00:00Z")
    assert a.recommendations() == []
    a.set_recommendation_state(rid, "open", "Not retired after all")
    assert a.recommendations()[0]["state"] == "open"
    assert len(lake.rows("fdopt_recommendations")) == 3  # nothing updated in place
    audit = lake.rows("fdopt_audit")
    changes = audit.filter(audit["action"] == "recommendation_state").sort("at")
    assert changes["outcome"].to_list() == ["dismissed", "open"]


def test_a_change_from_a_pc_with_a_slow_clock_still_wins(tmp_path, lake, monkeypatch):
    me = pc(tmp_path, lake=lake)
    analysis(me, "r1", [finding()], [{"workspace_id": "w1", "item_id": "i1", "rule_id": "small_files",
                                      "target": "dbo.sales", "kind": "optimize"}], finished_at="2999-01-01T00:00:00Z")
    rid = me.recommendations()[0]["recommendation_id"]
    me.set_recommendation_state(rid, "accepted")
    me.set_recommendation_state(rid, "applied")  # same moment, and both "before" the opening on this clock
    rec = me.recommendations()[0]
    assert rec["state"] == "applied" and rec["changed_at"] == "2999-01-01T00:00:00.000002+00:00"


def test_reads_leave_out_workspaces_the_person_cant_open(tmp_path, lake):
    me = pc(tmp_path, lake=lake)
    analysis(me, "r1", [finding(ws="w1"), finding(ws="hr", item="secret")],
             [{"workspace_id": "hr", "item_id": "secret", "rule_id": "small_files", "target": "dbo.sales",
               "kind": "optimize"}], finished_at="2026-10-18T10:01:00Z")
    assert [f["artifact_key"] for f in me.findings({"w1"})] == ["w1/i1"]
    assert me.recommendations({"w1"}) == []
    assert len(me.findings({"w1", "hr"})) == 2
    rid = me.recommendations({"hr"})[0]["recommendation_id"]
    with pytest.raises(ValueError, match="can't open"):
        me.set_recommendation_state(rid, "dismissed", workspaces={"w1"})


def test_read_only_access_and_newer_schema_refuse_writes(tmp_path, lake):
    with pytest.raises(PermissionError, match="read-only"):
        analysis(pc(tmp_path, lake=lake, access="read"), "r1", [finding()])
    setup.write(lake, Installation(tmp_path / "future" / "optimize"), "schema_version",
                [{"record_id": "v99", "version": 99, "description": "future", "applied_at": "2027-01-01T00:00:00Z"}])
    with pytest.raises(PermissionError, match="newer Optimize schema"):
        analysis(pc(tmp_path, lake=lake), "r1", [finding()])
    assert len(lake.rows("fdopt_findings")) == 0


def test_bad_input_is_refused_before_anything_is_written(tmp_path, lake):
    me = pc(tmp_path, lake=lake)
    with pytest.raises(ValueError, match="time zone"):
        analysis(me, "r1", [finding()], finished_at="2026-10-18T10:00:00")
    with pytest.raises(ValueError, match="run_id"):
        me.record_analysis({}, [], [finding()])
    with pytest.raises(ValueError, match="Severity"):
        analysis(me, "r1", [finding(severity="catastrophic")])
    with pytest.raises(ValueError, match="same run"):
        analysis(me, "r1", [finding()], [{"workspace_id": "w1", "item_id": "other", "rule_id": "x", "kind": "k"}])
    assert len(lake.rows("fdopt_findings")) == 0


def test_duplicate_rows_in_the_table_are_read_once(tmp_path, lake):
    me = pc(tmp_path, lake=lake)
    analysis(me, "r1", [finding()], finished_at="2026-10-18T10:01:00Z")
    # A writer that bypassed the MERGE (an older client, a manual load) appended the same row again.
    rows = lake.rows("fdopt_findings")
    lake.write("optimization", "dbo", "fdopt_findings", rows, mode="append")
    assert len(lake.rows("fdopt_findings")) == 2
    assert me.findings()[0]["occurrences"] == 1 and len(me.rows("findings")) == 1
