"""Mutation-testing pins for data_format (docs/quality/mutation-testing.md, batch 5)."""

from __future__ import annotations

import json

from fabric_desktop import data_format


def test_a_folder_already_at_this_builds_format_is_left_as_it_was(tmp_path):
    mark = tmp_path / data_format.MARK
    saved = json.dumps({"format": data_format.DATA_FORMAT, "written_by": "an earlier build"}) + "\n"
    mark.write_text(saved, encoding="utf-8")
    data_format.check(tmp_path)
    assert mark.read_text(encoding="utf-8") == saved  # not rewritten: who marked it stays on record
