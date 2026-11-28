"""The mobile companion's run feed (MC-001): one shape for every run type, and changes by version."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fabric_desktop.companion import feed

NOW = 1_800_000_000.0


class FakeSql:
    def __init__(self, runs: list[dict[str, Any]]):
        self.runs = runs

    def recent(self) -> list[dict[str, Any]]:
        return self.runs


class FakeService:
    def __init__(self, root: Path):
        self.root = root
        self.sql = FakeSql([])
        self.pipelines: list[dict[str, Any]] = []
        self.proofs: list[dict[str, Any]] = []
        self.comparisons: list[dict[str, Any]] = []
        self.fabric: list[dict[str, Any]] = []
        self.batches: dict[str, dict[str, Any]] = {}
        self.fabric_refresh: list[bool] = []

    @property
    def sql_runs(self) -> FakeSql:
        return self.sql

    def _batch_dir(self) -> Path:
        return self.root / "notebook_batches"

    def notebook_batch(self, id: str) -> dict[str, Any]:  # noqa: A002
        if id not in self.batches:
            raise LookupError(id)
        return self.batches[id]

    def add_batch(self, batch: dict[str, Any]) -> None:
        self._batch_dir().mkdir(exist_ok=True)
        (self._batch_dir() / f"{batch['id']}.json").write_text(json.dumps(batch), encoding="utf-8")
        self.batches[batch["id"]] = batch

    def pipeline_runs(self, limit: int = 50) -> dict[str, Any]:
        return {"runs": self.pipelines}

    def prove_list(self, name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return self.proofs

    def side_by_side_list(self, name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return self.comparisons

    def fabric_runs(self, limit: int = 50, refresh: bool = True, job_ids: list[str] | None = None) -> list[dict[str, Any]]:
        self.fabric_refresh.append(refresh)
        return self.fabric


@pytest.fixture
def svc(tmp_path: Path) -> FakeService:
    return FakeService(tmp_path)


def make(svc: FakeService) -> feed.RunFeed:
    return feed.RunFeed(svc, clock=lambda: NOW)


def test_every_row_has_the_one_shape(svc: FakeService) -> None:
    svc.sql.runs = [{"id": "e1", "state": "running", "lakehouse": "Sales", "created": NOW - 30, "started": NOW - 20,
                     "ended": None, "seconds": 20.0}]
    svc.pipelines = [{"run_id": "p1", "pipeline": "daily", "state": "succeeded", "started": "2027-01-15T08:00:00+00:00",
                      "ended": "2027-01-15T08:05:00+00:00", "seconds": 300.0, "counts": {"succeeded": 4}}]
    out = make(svc).list()
    assert out["sources"] == {k: "ok" for k in feed.SOURCES}
    keys = {"id", "kind", "name", "state", "started", "ended", "seconds", "progress", "engine", "can_cancel", "message"}
    assert all(set(r) == keys for r in out["runs"])
    sql = next(r for r in out["runs"] if r["kind"] == "sql")
    assert sql == {"id": "sql:e1", "kind": "sql", "name": "SQL query on Sales", "state": "running", "started": NOW - 20,
                   "ended": None, "seconds": 20.0, "progress": None, "engine": "this_pc", "can_cancel": True, "message": ""}
    pipe = next(r for r in out["runs"] if r["kind"] == "pipeline")
    assert pipe["state"] == "succeeded" and pipe["can_cancel"] is False
    assert pipe["progress"] == {"done": 4, "total": None, "unit": "activities"}
    assert pipe["started"] == pytest.approx(pipe["ended"] - 300)


@pytest.mark.parametrize(("raw", "state", "cancel"), [
    ("queued", "queued", True), ("running", "running", True), ("cancelling", "cancelling", False),
    ("completed", "succeeded", False), ("failed", "failed", False), ("timed_out", "failed", False),
    ("cancelled", "cancelled", False), ("something_new", "unknown", False),
])
def test_sql_states(svc: FakeService, raw: str, state: str, cancel: bool) -> None:
    svc.sql.runs = [{"id": "e1", "state": raw, "created": NOW}]
    row = make(svc).list()["runs"][0]
    assert (row["state"], row["can_cancel"]) == (state, cancel)


def test_sql_progress_counts_statements_finished(svc: FakeService) -> None:
    svc.sql.runs = [{"id": "e1", "state": "running", "created": NOW, "statements": 5, "statement": 3}]
    assert make(svc).list()["runs"][0]["progress"] == {"done": 2, "total": 5, "unit": "statements"}


def test_messages_lose_credentials_and_control_characters(svc: FakeService) -> None:
    svc.sql.runs = [{"id": "e1", "state": "failed", "created": NOW,
                     "error": "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl\x07 refused"}]
    message = make(svc).list()["runs"][0]["message"]
    assert "eyJ0eXAi" not in message and "\x07" not in message and "refused" in message


def test_names_and_messages_are_capped(svc: FakeService) -> None:
    svc.pipelines = [{"run_id": "p", "pipeline": "x" * 500, "state": "failed", "stop_reason": "y" * 900}]
    row = make(svc).list()["runs"][0]
    assert len(row["name"]) == feed.MAX_NAME and len(row["message"]) == feed.MAX_MESSAGE


def test_notebook_batches(svc: FakeService) -> None:
    svc.add_batch({"id": "a" * 12, "started": "2027-01-15T08:00:00+00:00", "state": "running", "stopping": False,
                   "items": [{"name": "load", "status": "ok"}, {"name": "clean", "status": "running", "cell": 3, "cells": 9},
                             {"name": "report", "status": "waiting"}]})
    svc.add_batch({"id": "b" * 12, "started": "2027-01-15T07:00:00+00:00", "ended": "2027-01-15T07:10:00+00:00",
                   "state": "done", "items": [{"name": "a", "status": "ok"}, {"name": "b", "status": "failed", "message": "boom"}]})
    (svc._batch_dir() / f"{'a' * 12}.0.progress").write_text("{}", encoding="utf-8")  # not a batch
    rows = {r["id"]: r for r in make(svc).list()["runs"]}
    assert set(rows) == {f"notebooks:{'a' * 12}", f"notebooks:{'b' * 12}"}
    running = rows[f"notebooks:{'a' * 12}"]
    assert running["name"] == "Notebooks: load, clean, report"
    assert running["progress"] == {"done": 1, "total": 3, "unit": "notebooks"}
    assert running["message"] == "clean: cell 3 of 9" and running["can_cancel"] is True
    done = rows[f"notebooks:{'b' * 12}"]
    assert done["state"] == "failed" and done["message"] == "b: boom" and done["seconds"] == 600.0


@pytest.mark.parametrize(("batch", "state"), [
    ({"state": "lost", "items": []}, "failed"),
    ({"state": "running", "stopping": True, "items": []}, "cancelling"),
    ({"state": "done", "items": [{"status": "stopped"}, {"status": "ok"}]}, "cancelled"),
    ({"state": "done", "items": [{"status": "ok"}, {"status": "skipped"}]}, "succeeded"),
    ({"state": "done", "items": [{"status": "stopped"}, {"status": "failed"}]}, "failed"),
])
def test_batch_states(batch: dict[str, Any], state: str) -> None:
    assert feed._batch_state(batch) == state


def test_many_notebooks_are_summed_up(svc: FakeService) -> None:
    svc.add_batch({"id": "c" * 12, "state": "running", "items": [{"name": f"n{i}", "status": "waiting"} for i in range(5)]})
    assert make(svc).list()["runs"][0]["name"] == "Notebooks: n0, n1, n2 and 2 more"


def test_proofs_and_side_by_side(svc: FakeService) -> None:
    svc.proofs = [{"id": "pr1", "name": "daily", "state": "pc", "started_at": "2027-01-15T08:00:00+00:00",
                   "pc": {"cell": 2, "cells_total": 6}},
                  {"id": "pr2", "name": "old", "state": "fabric", "started_at": "2027-01-15T07:00:00+00:00"}]
    svc.comparisons = [{"id": "s1", "name": "nb", "state": "done", "started_at": "2027-01-15T06:00:00+00:00",
                        "finished_at": "2027-01-15T06:01:00+00:00"}]
    rows = {r["id"]: r for r in make(svc).list()["runs"]}
    assert rows["proof:pr1"]["progress"] == {"done": 2, "total": 6, "unit": "cells"}
    assert rows["proof:pr1"]["can_cancel"] is True and rows["proof:pr1"]["engine"] == "this_pc"
    assert rows["proof:pr2"]["engine"] == "fabric" and rows["proof:pr2"]["progress"] is None
    assert rows["side_by_side:s1"]["state"] == "succeeded" and rows["side_by_side:s1"]["can_cancel"] is False


def test_fabric_jobs_never_ask_fabric(svc: FakeService) -> None:
    svc.fabric = [{"job_id": "j1", "name": "nb", "status": "InProgress", "workspace_id": "w", "item_id": "i",
                   "started_at": "2027-01-15T08:00:00+00:00"},
                  {"job_id": "j2", "status": "Deduped"},
                  {"name": "no job id yet", "status": "NotStarted"}]
    rows = {r["id"]: r for r in make(svc).list()["runs"]}
    assert svc.fabric_refresh == [False]
    assert set(rows) == {"fabric:j1", "fabric:j2"}
    assert rows["fabric:j1"]["can_cancel"] is True and rows["fabric:j1"]["engine"] == "fabric"
    assert rows["fabric:j2"]["state"] == "cancelled" and rows["fabric:j2"]["can_cancel"] is False
    assert rows["fabric:j2"]["name"] == "Fabric job"


def test_fabric_job_without_ids_cant_be_cancelled(svc: FakeService) -> None:
    svc.fabric = [{"job_id": "j1", "status": "InProgress"}]
    assert make(svc).list()["runs"][0]["can_cancel"] is False


def test_active_runs_come_first_then_newest(svc: FakeService) -> None:
    svc.sql.runs = [{"id": "old", "state": "completed", "started": NOW - 100},
                    {"id": "new", "state": "completed", "started": NOW - 10},
                    {"id": "live", "state": "running", "started": NOW - 500},
                    {"id": "nostart", "state": "queued"}]
    out = make(svc).list()
    assert [r["id"] for r in out["runs"]] == ["sql:live", "sql:nostart", "sql:new", "sql:old"]
    assert [r["id"] for r in make(svc).list(active_only=True)["runs"]] == ["sql:live", "sql:nostart"]
    assert len(make(svc).list(limit=2)["runs"]) == 2


def test_a_failing_source_leaves_the_others(svc: FakeService) -> None:
    svc.sql.runs = [{"id": "e1", "state": "running", "started": NOW}]

    def broken(service: Any, now: float) -> list[dict[str, Any]]:
        raise OSError("disk")

    f = feed.RunFeed(svc, sources={"sql": feed.sql_runs, "pipeline": broken}, clock=lambda: NOW)
    out = f.list()
    assert out["sources"] == {"sql": "ok", "pipeline": "unavailable: OSError"}
    assert [r["id"] for r in out["runs"]] == ["sql:e1"]


def test_changes_by_version(svc: FakeService) -> None:
    f = make(svc)
    svc.sql.runs = [{"id": "e1", "state": "running", "started": NOW, "seconds": 1.0}]
    first = f.list()
    assert first["version"] == 1
    svc.sql.runs = [{"id": "e1", "state": "running", "started": NOW, "seconds": 7.0}]  # only the clock moved
    same = f.changes(first["version"])
    assert same["runs"] == [] and same["version"] == 1 and same["reset"] is False
    svc.sql.runs = [{"id": "e1", "state": "completed", "started": NOW, "ended": NOW + 9, "seconds": 9.0},
                    {"id": "e2", "state": "queued"}]
    moved = f.changes(first["version"])
    assert [r["id"] for r in moved["runs"]] == ["sql:e2", "sql:e1"] and moved["version"] == 3
    svc.sql.runs = [{"id": "e2", "state": "queued"}]
    gone = f.changes(moved["version"])
    assert gone["removed"] == ["sql:e1"] and gone["runs"] == [] and gone["version"] == 4
    assert f.changes(0)["removed"] == ["sql:e1"]


@pytest.mark.parametrize("since", [99, -1, "3", True, None])
def test_a_version_the_feed_never_gave_reads_everything(svc: FakeService, since: Any) -> None:
    svc.sql.runs = [{"id": "e1", "state": "running", "started": NOW}]
    out = make(svc).changes(since)
    assert out["reset"] is True and [r["id"] for r in out["runs"]] == ["sql:e1"]


def test_a_source_that_fails_doesnt_tombstone_its_runs(svc: FakeService) -> None:
    state = {"fail": False}

    def flaky(service: Any, now: float) -> list[dict[str, Any]]:
        if state["fail"]:
            raise RuntimeError("busy")
        return [feed.run("pipeline", "p1", "x", "running", now=now)]

    f = feed.RunFeed(svc, sources={"pipeline": flaky}, clock=lambda: NOW)
    v = f.list()["version"]
    state["fail"] = True
    assert f.changes(v)["removed"] == []


def test_get(svc: FakeService) -> None:
    svc.sql.runs = [{"id": "e1", "state": "running", "started": NOW}]
    f = make(svc)
    assert f.get("sql:e1")["state"] == "running"
    assert f.get("sql:nope") is None
    assert f.get("shell:e1") is None and f.get("e1") is None and f.get(5) is None  # type: ignore[arg-type]


def test_when_and_progress_guards() -> None:
    assert feed._when("2027-01-15T08:00:00Z") == feed._when("2027-01-15T08:00:00+00:00")
    assert feed._when("2027-01-15T08:00:00") == feed._when("2027-01-15T08:00:00+00:00")
    assert feed._when("not a time") is None and feed._when(True) is None and feed._when("") is None
    assert feed._progress(True, 3, "x") is None and feed._progress(-1, 3, "x") is None
    assert feed._progress(4, 3, "x") == {"done": 4, "total": None, "unit": "x"}
    assert feed.run("sql", "a", "", "running", now=NOW)["name"] == "sql"


def test_with_the_real_service_sources_exist() -> None:
    """Every method a source calls exists on the engine's Service, so a rename breaks this test."""
    from fabric_desktop.service import Service

    for name in ("sql_runs", "_batch_dir", "notebook_batch", "pipeline_runs", "prove_list", "side_by_side_list",
                 "fabric_runs"):
        assert hasattr(Service, name), name
