"""opportunities/inventory.py paths that mutation testing (batch 59, docs/quality/mutation-testing.md)
showed no test pinned: the edges of the run window, which runs are timed, the counts and rounding of the
summary, times with and without an offset, and notebook definitions given as plain text or with list
sources."""

from __future__ import annotations

import base64
import datetime as dt
import json

import pytest

from fabric_desktop.opportunities import inventory

NOW = dt.datetime(2026, 10, 17, 12, tzinfo=dt.UTC)


def _iso(t: dt.datetime) -> str:
    return t.isoformat()


def test_runs_exactly_at_the_window_edges_count_and_one_before_does_not():
    since = NOW - dt.timedelta(days=7)
    jobs = [{"startTimeUtc": _iso(since), "status": "Running"},
            {"startTimeUtc": _iso(NOW), "status": "Running"},
            {"startTimeUtc": _iso(since - dt.timedelta(seconds=1)), "status": "Running"},
            {"startTimeUtc": _iso(NOW + dt.timedelta(seconds=1)), "status": "Running"}]
    got = inventory.run_summary(jobs, NOW, 7)
    assert got["runs"] == 2
    assert got["first"] == since.isoformat(timespec="seconds") and got["last"] == NOW.isoformat(timespec="seconds")


def test_a_start_that_isnt_text_is_skipped():
    got = inventory.run_summary([{"startTimeUtc": 12345, "status": "Completed"}], NOW, 7)
    assert got["runs"] == 0


def test_an_offset_is_kept_and_a_time_without_one_is_utc():
    jobs = [{"startTimeUtc": "2026-10-16T14:00:00+02:00", "status": "Running"},
            {"startTimeUtc": "2026-10-16T15:00:00", "status": "Running"}]
    got = inventory.run_summary(jobs, NOW, 7)
    assert got["first"] == "2026-10-16T14:00:00+02:00" and got["last"] == "2026-10-16T15:00:00+00:00"


def test_only_finished_runs_with_an_end_not_before_their_start_are_timed():
    start = NOW - dt.timedelta(hours=1)
    jobs = [{"startTimeUtc": _iso(start), "endTimeUtc": _iso(start), "status": "Completed"},  # 0 s
            {"startTimeUtc": _iso(start), "endTimeUtc": _iso(start + dt.timedelta(seconds=10)), "status": "Failed"},
            {"startTimeUtc": _iso(start), "endTimeUtc": _iso(start + dt.timedelta(seconds=20)), "status": "Running"},
            {"startTimeUtc": _iso(start), "endTimeUtc": _iso(start - dt.timedelta(seconds=5)), "status": "Completed"},
            {"startTimeUtc": _iso(start), "status": "Cancelled"}]
    got = inventory.run_summary(jobs, NOW, 7)
    assert got["runs"] == 5 and got["timed_runs"] == 2
    assert got["seconds_total"] == 10.0 and got["seconds_median"] == 5.0
    assert got["failed"] == 1


def test_failed_counts_only_failed_runs():
    start = NOW - dt.timedelta(hours=1)
    jobs = [{"startTimeUtc": _iso(start), "status": s} for s in ("Failed", "Failed", "Completed", "Cancelled")]
    assert inventory.run_summary(jobs, NOW, 7)["failed"] == 2


def test_seconds_are_rounded_to_three_places():
    start = NOW - dt.timedelta(hours=1)
    end = start + dt.timedelta(seconds=1, microseconds=400)  # 1.0004 s
    got = inventory.run_summary([{"startTimeUtc": _iso(start), "endTimeUtc": _iso(end), "status": "Completed"}], NOW, 7)
    assert got["seconds_total"] == 1.0 and got["seconds_median"] == 1.0


def test_no_runs_is_zero_seconds_and_runs_none_timed_is_unknown():
    assert inventory.run_summary([], NOW, 7)["seconds_total"] == 0.0
    running = [{"startTimeUtc": _iso(NOW - dt.timedelta(hours=1)), "status": "Running"}]
    got = inventory.run_summary(running, NOW, 7)
    assert got["seconds_total"] is None and got["seconds_median"] is None


def _ipynb(cells, payload_type=None, encode=True):
    text = json.dumps({"cells": cells, "metadata": {}})
    part = {"path": "notebook-content.ipynb", "payload": base64.b64encode(text.encode()).decode() if encode else text}
    if payload_type:
        part["payloadType"] = payload_type
    return {"definition": {"parts": [part]}}


def test_a_payload_that_isnt_base64_is_read_as_it_is():
    cells, lh = inventory.notebook_content(_ipynb([{"cell_type": "code", "source": "x = 1"}], "InlinePlainText", encode=False))
    assert cells == ["x = 1"] and lh is None


def test_a_cell_source_given_as_lines_is_joined_and_as_text_kept():
    cells, _ = inventory.notebook_content(_ipynb([{"cell_type": "code", "source": ["a = 1\n", "b = 2"]},
                                                  {"cell_type": "code", "source": "c = 3"},
                                                  {"cell_type": "code", "source": None}]))
    assert cells == ["a = 1\nb = 2", "c = 3", ""]


def test_a_definition_without_notebook_content_is_refused():
    with pytest.raises(ValueError, match="no notebook-content part"):
        inventory.notebook_content({"definition": {"parts": [{"path": ".platform", "payload": ""}]}})


def test_definitions_nested_too_deeply_are_not_read_rather_than_raising():
    """A pipeline from Fabric nested thousands deep is refused by the definition reader (so the copy
    analyzer's signature walk never sees it), and a notebook whose ipynb is nested past what json
    reads is a definition that couldn't be read; it used to raise RecursionError out of workloads()."""
    import base64
    import datetime as dt
    import json

    from fabric_desktop.opportunities import inventory
    deep: object = {"type": "x"}
    for _ in range(2500):
        deep = {"a": [deep]}
    pipe = {"definition": {"parts": [{"path": "pipeline-content.json", "payloadType": "InlineBase64",
                                      "payload": base64.b64encode(json.dumps({"properties": {"activities": [
                                          {"name": "c", "type": "Copy", "typeProperties": {"source": deep}}]}}).encode()).decode()}]}}
    nb_text = '{"cells": ' + "[" * 20000 + "]" * 20000 + "}"
    nb = {"definition": {"parts": [{"path": "notebook-content.ipynb", "payloadType": "InlineBase64",
                                    "payload": base64.b64encode(nb_text.encode()).decode()}]}}
    items = [{"id": "p", "type": "DataPipeline", "displayName": "P"}, {"id": "n", "type": "Notebook", "displayName": "N"}]
    out = inventory.workloads({"id": "w"}, items, {"p": pipe, "n": nb}, {}, now=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                              days=30)
    assert [(w["item_id"], w["definition"]) for w in out] == [("p", "not_read"), ("n", "not_read")]
    assert "nested more than 500 levels deep" in out[0]["problem"] and "nested too deeply" in out[1]["problem"]
