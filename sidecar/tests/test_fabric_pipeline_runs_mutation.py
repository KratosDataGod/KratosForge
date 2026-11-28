"""Mutation batch 80: Monitor > Runs' edges. Times parsed to the exact microsecond, durations to the
millisecond, the caps on runs per item, runs in all and activity text, activity rows with and without
times or an error message, the window for a run with no times, the background scan's thread, and how
long ids are cut in what was missed."""

from __future__ import annotations

import datetime as dt

from fabric_desktop import fabric_pipeline_runs_service as fpr
from fabric_desktop.redact import one_line
from fabric_desktop.service import Service
from tests.test_fabric_pipeline_runs import JOB, WS, FakeFabric, svc  # noqa: F401 - the fixture is used by name

UTC = dt.timezone.utc


def test_times_are_parsed_to_the_microsecond():
    assert fpr._parse("2026-10-24T06:00:00.1234567Z") == dt.datetime(2026, 10, 24, 6, 0, 0, 123456, tzinfo=UTC)
    assert fpr._parse("2026-10-24T06:00:00.5Z") == dt.datetime(2026, 10, 24, 6, 0, 0, 500000, tzinfo=UTC)
    assert fpr._parse(" 2026-10-24T06:00:00 ") == dt.datetime(2026, 10, 24, 6, 0, 0, tzinfo=UTC)
    for odd in (123, 1.5, True, [], {}, ""):
        assert fpr._parse(odd) is None


def test_a_duration_is_rounded_to_the_millisecond():
    assert fpr._seconds("2026-10-24T06:00:00Z", "2026-10-24T06:00:01.2346Z") == 1.235
    assert fpr._seconds("2026-10-24T06:00:00Z", None) is None
    assert fpr._seconds(None, "2026-10-24T06:00:01Z") is None


def test_activity_text_is_capped_at_16000_characters():
    assert fpr.TEXT_LIMIT == 16_000
    exact = fpr._text("x" * 16_000)
    assert exact == {"text": "x" * 16_000, "truncated": False}
    over = fpr._text("x" * 16_001)
    assert over == {"text": "x" * 16_000, "truncated": True}
    for empty in (None, "", {}, []):
        assert fpr._text(empty) == {"text": "", "truncated": False}


def test_activity_json_is_indented_by_two_and_keeps_its_letters():
    assert fpr._text({"city": "Zürich"}) == {"text": '{\n  "city": "Zürich"\n}', "truncated": False}


def test_an_activity_row_keeps_its_times_and_an_error_with_only_a_code():
    row = fpr.activity_row({"activityName": "Copy", "status": "Failed", "activityRunStart": "2026-10-24T06:00:00Z",
                            "activityRunEnd": "2026-10-24T06:01:00Z", "error": {"errorCode": "2200"}})
    assert (row["started"], row["ended"]) == ("2026-10-24T06:00:00Z", "2026-10-24T06:01:00Z")
    assert row["error"] == {"code": "2200", "message": "", "failure_type": ""}
    bare = fpr.activity_row({"activityName": "Wait", "status": "Succeeded", "error": {}})
    assert (bare["started"], bare["ended"], bare["error"]) == (None, None, None)


def test_a_run_with_no_times_is_looked_up_over_the_last_day(svc, monkeypatch):  # noqa: F811
    now = dt.datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(fpr, "_now", lambda: now)
    seen = {}

    class Client:
        def query_activity_runs(self, ws, job, after, before):
            seen.update(after=after, before=before)
            return []

    monkeypatch.setattr(Service, "fabric", property(lambda self: Client()))
    out = svc.fabric_pipeline_activity_runs(WS, JOB)
    assert seen == {"after": "2026-10-23T11:55:00.000Z", "before": "2026-10-24T12:05:00.000Z"}
    assert out["read"] == "2026-10-24T12:00:00.000Z"


def _many_jobs(self, ws, item):
    return [{"id": f"{item}-j{i:02d}", "status": "Completed", "startTimeUtc": f"2026-10-{i + 1:02d}T00:00:00Z"}
            for i in range(21)]


def test_at_most_20_runs_per_item_and_300_in_all_newest_first(svc, monkeypatch):  # noqa: F811
    items = [{"id": f"pl{n:02d}", "type": "DataPipeline", "displayName": f"p{n}"} for n in range(16)]
    monkeypatch.setattr(FakeFabric, "list_items", lambda self, ws: items if ws == "ws1" else [])
    monkeypatch.setattr(FakeFabric, "list_job_instances", _many_jobs)
    svc._scan_fabric_pipeline_runs("tok", {"all_workspaces": False, "workspace_ids": ["ws1"]})
    out = svc._fabric_pipeline_runs
    assert out["items"] == 16 and out["missed"] == 0
    assert len(out["runs"]) == 300  # 16 items x 20 runs, cut to 300
    per_item = {}
    for r in out["runs"]:
        per_item.setdefault(r["item_id"], []).append(r["job_id"])
    assert all(f"{item}-j20" not in jobs for item, jobs in per_item.items())  # the 21st run is left out
    assert out["runs"][0]["started"] == "2026-10-20T00:00:00Z"
    assert min(r["started"] for r in out["runs"]) == "2026-10-02T00:00:00Z"


def test_the_scan_runs_on_a_daemon_thread_so_it_never_holds_the_app_open(svc, monkeypatch):  # noqa: F811
    started = []

    class Thread:
        def __init__(self, **kw):
            started.append(kw)

        def start(self):
            pass

    monkeypatch.setattr(fpr.threading, "Thread", Thread)
    assert svc.fabric_pipeline_runs(workspace_ids=["ws1"])["pending"] is True
    assert len(started) == 1 and started[0]["daemon"] is True
    assert started[0]["args"] == ("tok", {"all_workspaces": False, "workspace_ids": ["ws1"]})


def test_long_ids_in_what_was_missed_are_cut_to_100_characters(svc, monkeypatch):  # noqa: F811
    long_ws, long_item = "w" * 150, "i" * 150
    monkeypatch.setattr(svc, "_pipeline_run_workspaces", lambda token, scope: [
        {"id": long_ws, "name": "Long"}, {"id": "ws1", "name": "Sales Dev"}])

    def items(self, ws):
        if ws == long_ws:
            raise RuntimeError("403 Forbidden")
        return [{"id": long_item, "type": "CopyJob", "displayName": "cj"}]

    def jobs(self, ws, item):
        raise RuntimeError("404 Not found")

    monkeypatch.setattr(FakeFabric, "list_items", items)
    monkeypatch.setattr(FakeFabric, "list_job_instances", jobs)
    warned = []
    monkeypatch.setattr(fpr._log, "warning", lambda *a, **k: warned.append(a))
    svc._scan_fabric_pipeline_runs("tok", {"all_workspaces": False, "workspace_ids": []})
    assert svc._fabric_pipeline_runs["missed"] == 2
    assert one_line(long_ws, 100) != one_line(long_ws, 101)
    assert warned[0][2] == f"items in workspace {one_line(long_ws, 100)}: RuntimeError: 403 Forbidden"

    monkeypatch.setattr(svc, "_pipeline_run_workspaces", lambda token, scope: [{"id": "ws1", "name": "Sales Dev"}])
    warned.clear()
    svc._scan_fabric_pipeline_runs("tok", {"all_workspaces": False, "workspace_ids": []})
    assert warned[0][2] == f"runs of copy job {one_line(long_item, 100)}: RuntimeError: 404 Not found"
