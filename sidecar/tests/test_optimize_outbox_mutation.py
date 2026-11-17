"""The Optimize outbox's limits, waits and error text, pinned where mutation testing
(docs/quality/mutation-testing.md, batch 84) found nothing that would notice a change."""

from __future__ import annotations

import pytest

from fabric_desktop.optimize import outbox as ob


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def payload(n=1):
    return {"run": {"run_id": f"r{n}"}, "artifacts": [], "findings": [], "recommendations": []}


def test_the_default_limits_are_two_hundred_items_and_thirty_days(tmp_path):
    box = ob.Outbox(tmp_path / "o.sqlite")
    assert ob.MAX_ITEMS == 200 and ob.MAX_AGE_DAYS == 30
    assert box.max_items == 200
    assert box.max_age == 30 * 86400


def test_an_age_in_days_is_counted_in_whole_days_of_seconds(tmp_path):
    box = ob.Outbox(tmp_path / "o.sqlite", max_age_days=2)
    assert box.max_age == 172800


def test_a_cap_below_one_still_keeps_the_newest_item(tmp_path):
    clock = Clock()
    box = ob.Outbox(tmp_path / "o.sqlite", clock=clock, max_items=0)
    assert box.max_items == 1
    box.add("lh1", ob.ANALYSIS, payload(1), "down")
    clock.now += 1
    newest = box.add("lh1", ob.ANALYSIS, payload(2), "down")
    assert [i["id"] for i in box.due("lh1", include_waiting=True)] == [newest]
    assert box.status("lh1")["dropped"] == 1


def test_one_trim_that_drops_an_old_item_and_one_past_the_cap_counts_both(tmp_path):
    clock = Clock()
    box = ob.Outbox(tmp_path / "o.sqlite", clock=clock, max_items=3, max_age_days=1)
    box.add("lh1", ob.ANALYSIS, payload(1), "down")
    clock.now += 100
    box.add("lh1", ob.ANALYSIS, payload(2), "down")
    clock.now += 100
    newest = box.add("lh1", ob.ANALYSIS, payload(3), "down")
    clock.now += 86400 - 150  # the first is now too old, the others aren't
    box.max_items = 1
    assert [i["id"] for i in box.due("lh1", include_waiting=True)] == [newest]
    assert box.status("lh1")["dropped"] == 2  # one too old and one past the cap, in the same trim


def test_a_failure_inside_a_write_undoes_it_and_is_raised(tmp_path, monkeypatch):
    box = ob.Outbox(tmp_path / "o.sqlite")

    def broken(kind, payload):
        raise RuntimeError("could not count rows")

    monkeypatch.setattr(ob, "rows_in", broken)
    with pytest.raises(RuntimeError, match="could not count rows"):
        box.add("lh1", ob.ANALYSIS, payload(), "down")
    monkeypatch.undo()
    assert box.status("lh1") == {"pending": 0, "pending_rows": 0, "oldest": None, "last_sync": None,
                                 "last_error": None, "last_error_at": None, "dropped": 0}


def test_each_failure_doubles_the_wait_to_the_second(tmp_path):
    clock = Clock()
    box = ob.Outbox(tmp_path / "o.sqlite", clock=clock)
    item = box.add("lh1", ob.ANALYSIS, payload(), "down")  # attempt 1
    box.failed(item, "still down")  # waits 60 * 2 ** 1
    clock.now += 119
    assert box.due("lh1") == []
    clock.now += 1
    [due] = box.due("lh1")
    assert due["attempts"] == 2
    box.failed(item, "still down")  # waits 60 * 2 ** 2
    clock.now += 239
    assert box.due("lh1") == []
    clock.now += 1
    assert len(box.due("lh1")) == 1


def test_the_wait_stops_growing_at_an_hour(tmp_path):
    clock = Clock()
    box = ob.Outbox(tmp_path / "o.sqlite", clock=clock)
    item = box.add("lh1", ob.ANALYSIS, payload(), "down")
    for _ in range(8):
        box.failed(item, "still down")
    clock.now += ob.MAX_BACKOFF_SECONDS - 1
    assert box.due("lh1") == []
    clock.now += 1
    assert len(box.due("lh1")) == 1


def test_a_lakehouse_never_seen_has_dropped_nothing(tmp_path):
    box = ob.Outbox(tmp_path / "o.sqlite")
    assert box.status("never-seen")["dropped"] == 0
    assert box.status("never-seen")["last_error"] is None


@pytest.mark.parametrize("record", ["add", "failed", "dropped"])
def test_an_error_is_kept_to_its_first_five_hundred_characters(tmp_path, record):
    box = ob.Outbox(tmp_path / "o.sqlite")
    long = "x" * 499 + "yz" + "w" * 100
    item = box.add("lh1", ob.ANALYSIS, payload(), long if record == "add" else "down")
    if record == "failed":
        box.failed(item, long)
    elif record == "dropped":
        box.dropped(item, long)
    assert box.status("lh1")["last_error"] == "x" * 499 + "y"
