"""Mutation-testing follow-up for learn/evidence.py (docs/quality/mutation-testing.md, batch 44).

Where each note starts and stops: the row and growth thresholds at their exact edges, the steps
that count as a join's two inputs, and what a step with a missing measure shows."""

from __future__ import annotations

from fabric_desktop.learn import evidence
from fabric_desktop.learn.evidence import _pct

WRAPPED = '("year"(order_date) = 2026)'


def _scan(read, kept, share=0.5, depth=1):
    return {"depth": depth, "name": "SEQ_SCAN", "rows": kept, "rows_scanned": read, "share": share,
            "details": {"Table": "memory.main.sales", "Filters": WRAPPED}}


def _join(made, sides, name="HASH_JOIN", share=0.5, depth=0):
    return [{"depth": depth, "name": name, "rows": made, "share": share, "details": {}},
            *({"depth": depth + 1, "name": "SEQ_SCAN", "rows": n, "details": {}} for n in sides)]


def _notes(*steps):
    return evidence.notes({"steps": list(steps)})


# ---- _pct -------------------------------------------------------------------------------------------

def test_one_percent_is_shown_as_a_percentage():
    assert _pct(0.01) == "1%"
    assert _pct(0.0099) == "under 1%"


# ---- scans ------------------------------------------------------------------------------------------

def test_a_scan_of_exactly_the_minimum_rows_gets_a_note():
    assert evidence.MIN_ROWS == 100_000
    (note,) = _notes(_scan(100_000, 10))
    assert note["measured"]["rows_read"] == 100_000
    assert _notes(_scan(99_999, 10)) == []


def test_a_scan_that_kept_exactly_a_quarter_gets_a_note():
    assert len(_notes(_scan(400_000, 100_000))) == 1
    assert _notes(_scan(400_000, 100_001)) == []


def test_a_scan_without_a_kept_count_or_share_shows_zero():
    step = _scan(200_000, 0)
    del step["rows"], step["share"]
    (note,) = _notes(step)
    assert note["measured"]["rows_kept"] == 0 and note["measured"]["share"] == 0.0
    assert "kept 0 (under 1%), under 1% of the query's time" in note["text"]


# ---- joins ------------------------------------------------------------------------------------------

def test_a_join_of_exactly_the_minimum_rows_gets_a_note():
    assert len(_notes(*_join(100_000, [100, 10_000]))) == 1
    assert _notes(*_join(99_999, [100, 9_999])) == []


def test_a_join_that_grew_exactly_ten_times_gets_a_note():
    assert evidence.JOIN_GROWTH == 10
    (note,) = _notes(*_join(200_000, [5, 20_000]))
    assert note["rule_id"] == "SQ006" and note["measured"]["inputs"] == [5, 20_000]
    assert _notes(*_join(200_000, [5, 20_001])) == []


def test_a_cross_join_that_made_only_its_larger_input_gets_no_note():
    assert _notes(*_join(100_000, [1, 100_000], name="CROSS_PRODUCT")) == []
    assert len(_notes(*_join(100_001, [1, 100_000], name="CROSS_PRODUCT"))) == 1


def test_a_join_with_more_than_two_inputs_gets_no_note():
    assert _notes(*_join(1_000_000, [10, 10, 10])) == []


def test_a_join_without_a_share_or_an_input_count_shows_zero():
    steps = _join(1_000_000, [10, 20])
    del steps[0]["share"], steps[1]["rows"]
    (note,) = _notes(*steps)
    assert note["measured"] == {"rows_made": 1_000_000, "inputs": [0, 20], "share": 0.0}
    assert note["text"].startswith("A join made 1,000,000 rows from 0 and 20, under 1% of the query's time.")


# ---- a join's inputs --------------------------------------------------------------------------------

def test_inputs_stop_at_the_next_step_at_the_joins_level():
    # Two inputs, then a sibling of the join with an input of its own: that one isn't the join's.
    steps = [*_join(1_000_000, [10, 20], depth=1), {"depth": 1, "name": "PROJECTION", "rows": 5},
             {"depth": 2, "name": "SEQ_SCAN", "rows": 30}]
    (note,) = _notes(*steps)
    assert note["measured"]["inputs"] == [10, 20]


def test_a_step_without_a_depth_is_at_the_top():
    # A join with no depth is at depth 0, so steps at depth 1 are its inputs.
    steps = _join(1_000_000, [10, 20])
    del steps[0]["depth"]
    assert len(_notes(*steps)) == 1
    # Steps with no depth after a join at depth 0 are at its level, not under it.
    top = _join(1_000_000, [])
    assert _notes(*top, {"name": "SEQ_SCAN", "rows": 10}, {"name": "SEQ_SCAN", "rows": 20}) == []
