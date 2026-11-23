"""The live test harness (livetest.py), held to exact values: each check's detail on a full run, the seconds it
records, the ledger's layout, who the result names, the second lease writer not waiting, a notebook run that
polls until it finishes, the pipeline it publishes, the ids a lost write names, and the summary's every line.
Fabric and OneLake are the fakes of the main tests; Delta is on local disk; the clock is a fake."""

from __future__ import annotations

import json
import re

from fabric_desktop import leases, livetest
from tests.test_livetest import ItemFabric, by_id, extra_harness, local_writer

DETAILS = {
    "auth.fabric": "Token received (not recorded).",
    "auth.onelake": "Token received (not recorded).",
    "fabric.workspace": "Live tests (capacity none)",
    "fabric.create_lakehouse": "Lakehouse fdlt_run1 (lh-1)",
    "fabric.get_lakehouse": "Properties include the OneLake Tables and Files paths.",
    "onelake.files": "30 bytes written, listed, read back identically and deleted.",
    "delta.write": "3 rows written as version 0.",
    "delta.read": "The 3 rows read back unchanged.",
    "delta.append": "Version 1 has 4 rows.",
    "delta.merge": "One row updated and one inserted.",
    "delta.time_travel": "Version 0 still has the original 3 rows.",
    "errors.not_found": "Reported as ItemNotFound.",
    "errors.bad_token": "Refused with 401.",
    "delta.concurrent": "Both commits are there (version 2, 101 rows = 1 + 50 + 50, none lost). Neither writer had "
                        "to retry; Delta resolved any commit race itself.",
    "notebook.publish": "Notebook fdlt_run1_nb (item-0) published; its 2 cells read back unchanged.",
    "notebook.run": "Job job-1 completed in Fabric.",
    "pipeline.definition": "Pipeline fdlt_run1_pl (item-1) created; its definition parses (item_definition, "
                           "1 activity) and round-trips through export.",
    "lease.onelake": "While writer A held the lease, writer B's create was refused (If-None-Match), a stale replace "
                     "was refused (If-Match) and B's wait ended busy; after A released it, B took it and released it.",
    "delta.merge_profile": "MERGE updated 1 and inserted 1 row(s) as version 1; the table stays in the profile "
                           "(reader 1, writer 2, no deletion vectors).",
    "cleanup.notebook": "Deleted, and no longer listed.",
    "cleanup.pipeline": "Deleted, and no longer listed.",
    "cleanup.lakehouse": "Deleted, and no longer found.",
}


class _Clock:
    """Each read moves on 0.12345 s."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 0.12345
        return self.now


class _Recording(ItemFabric):
    def __init__(self, statuses=None, **kw):
        super().__init__(**kw)
        self.saved, self.statuses = [], list(statuses or [])

    def save_item(self, ws, item_type, name, parts=None, description=""):
        self.saved.append((item_type, dict(parts or {})))
        return super().save_item(ws, item_type, name, parts, description)

    def job_instance(self, ws, item_id, job_id):
        if self.statuses:
            return {"status": self.statuses.pop(0)}
        return super().job_instance(ws, item_id, job_id)


def test_a_full_run_says_exactly_what_each_check_found(tmp_path):
    fabric = _Recording()
    result = extra_harness(tmp_path, fabric, run_in_fabric=True, clock=_Clock()).run()
    checks = by_id(result)
    assert {k: checks[k]["detail"] for k in DETAILS} == DETAILS
    assert set(checks) == set(DETAILS) | {"pipeline.mailbox"}
    assert re.fullmatch(r"Request taken once by agent A, left alone by agent B, cancelled; a made-up pipeline run "
                        r"was reported as not found \(.+\)\.", checks["pipeline.mailbox"]["detail"])
    seconds = {c["id"]: c["seconds"] for c in result["checks"]}
    assert seconds.pop("notebook.run") == 0.247, "it reads the clock once more, for its deadline"
    assert set(seconds.values()) == {0.123}, "the clock is read before and after each check"
    assert result["identity"] == {"name": "test user"}
    [(kind, parts)] = fabric.saved
    content = json.loads(next(iter(parts.values())))
    assert kind == "DataPipeline" and content["properties"]["activities"][0]["typeProperties"] == {"waitTimeInSeconds": 1}


def test_the_ledger_is_saved_as_indented_json(tmp_path):
    test = extra_harness(tmp_path, extra=())
    test.ledger.add(kind="lakehouse", workspace_id="ws", id="lh-1", name="fdlt_run1")
    text = (tmp_path / "out" / "run1" / "ledger.json").read_text(encoding="utf-8")
    assert text == json.dumps({"run_id": "run1", "workspace_id": test.workspace_id,
                               "created": [{"kind": "lakehouse", "workspace_id": "ws", "id": "lh-1",
                                            "name": "fdlt_run1"}]}, indent=2)


def test_a_credential_without_a_name_is_the_signed_in_user(tmp_path):
    test = extra_harness(tmp_path, extra=())
    test.credential = type("Anon", (), {"fabric_token": lambda self: "t", "storage_token": lambda self: "t"})()
    assert test.run()["identity"] == {"name": "signed-in user"}


def test_neither_lease_writer_waits(tmp_path, monkeypatch):
    waits = []

    class _Leases(leases.Leases):
        def __init__(self, *a, **kw):
            waits.append(kw.get("wait"))
            super().__init__(*a, **kw)
    monkeypatch.setattr(livetest.leases, "Leases", _Leases)
    result = extra_harness(tmp_path, extra={"lease.onelake"}).run()
    assert by_id(result)["lease.onelake"]["status"] == "passed"
    assert waits == [0, 0]


def test_a_notebook_run_polls_until_the_job_finishes(tmp_path):
    fabric = _Recording(statuses=["NotStarted", "InProgress", "Completed"])
    slept = []
    result = extra_harness(tmp_path, fabric, extra=(), run_in_fabric=True, clock=lambda: 0.0, sleep=slept.append).run()
    assert by_id(result)["notebook.run"]["detail"] == "Job job-1 completed in Fabric."
    assert slept == [10, 10] and not [c for c in fabric.calls if c[0] == "cancel_job"]


def test_a_lost_write_names_the_first_ten_ids_it_lost(tmp_path):
    result = extra_harness(tmp_path, extra={"delta.concurrent"}, writer=local_writer(tmp_path, drop="A")).run()
    assert by_id(result)["delta.concurrent"]["detail"] == (
        "RuntimeError: Expected 101 rows, found 51; lost ids [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]")


def _result(**kw):
    return {"run_id": "r1", "outcome": "failed", "workspace": {"id": "ws-1", "name": "W" * 250},
            "started_at": "2026-11-23T10:00:00+00:00", "opt_in": {"checks": ["delta.concurrent", "lease.onelake"]},
            "refused": [], "left_behind": [{"kind": "lakehouse", "name": "N" * 250, "id": "I" * 150}],
            "checks": [{"title": "T" * 250, "status": "failed", "seconds": 1.5, "detail": "boom"}]} | kw


def test_the_summary_writes_every_part_cut_to_its_limits():
    assert livetest.summary(_result()) == "\n".join([
        "# KratosForge live test r1", "",
        f"Outcome: **failed**. Workspace {'W' * 199}…, started 2026-11-23T10:00:00+00:00.", "",
        "Opt-in checks: delta.concurrent, lease.onelake.", "",
        "| Check | Result | Seconds | Detail |", "| --- | --- | --- | --- |",
        f"| {'T' * 199}… | **Failed** | 1.5 | boom |", "",
        "**Left behind** (cleanup failed; run `fabric-desktop live-test --cleanup` with this run's ledger.json):", "",
        f"- lakehouse {'N' * 199}… ({'I' * 99}…)"]) + "\n"


def test_a_summary_without_opt_in_checks_or_a_workspace_name_says_neither():
    text = livetest.summary(_result(opt_in={"checks": []}, workspace={"id": "ws-1", "name": ""}, left_behind=[],
                                    refused=["not a | test workspace"], checks=[]))
    assert text == ("# KratosForge live test r1\n\nOutcome: **failed**. Workspace ws-1, started 2026-11-23T10:00:00+00:00."
                    "\n\nNothing was written, because:\n\n- not a \\| test workspace\n\n"
                    "| Check | Result | Seconds | Detail |\n| --- | --- | --- | --- |\n")
