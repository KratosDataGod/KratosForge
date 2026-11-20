"""The data format mark at its boundary (mutation batch 207, a boundary re-score of batch 5,
docs/quality/mutation-testing.md): a format of 0 isn't a format."""

from __future__ import annotations

import json

from fabric_desktop import data_format


def test_a_mark_of_format_zero_is_no_mark_and_one_is(tmp_path):
    (tmp_path / data_format.MARK).write_text(json.dumps({"format": 0}), encoding="utf-8")
    assert data_format.marked(tmp_path) is None
    (tmp_path / data_format.MARK).write_text(json.dumps({"format": 1}), encoding="utf-8")
    assert data_format.marked(tmp_path) == 1
