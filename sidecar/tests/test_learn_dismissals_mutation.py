"""Mutation-killing tests for learn/dismissals.py (batch 47): the 1000 limit, the id length, rows that
aren't dismissals and the longest place name."""

from __future__ import annotations

import hashlib

from fabric_desktop import jsonfile
from fabric_desktop.learn import dismissals


def test_a_thousand_are_kept_and_the_next_one_pushes_out_the_oldest(tmp_path):
    rows = [{"id": f"{n:016x}", "rule_id": "SQ002", "scope": "file", "where": f"tab {n}", "line": "", "at": ""}
            for n in range(1000)]
    jsonfile.write_json(dismissals.path(tmp_path), rows)
    assert len(dismissals.load(tmp_path)) == 1000
    after = dismissals.add(tmp_path, "SQ002", "file", "tab new")
    assert len(after) == 1000 and after[0]["where"] == "tab 1" and after[-1]["where"] == "tab new"


def test_an_id_is_the_first_sixteen_hex_of_the_rule_scope_place_and_line(tmp_path):
    row = dismissals.add(tmp_path, "SQ002", "file", "WH_Sales/weekly.sql")[0]
    assert row["id"] == hashlib.sha256(b"SQ002|file|WH_Sales/weekly.sql|").hexdigest()[:16]
    assert len(row["id"]) == 16


def test_rows_that_arent_dismissals_are_left_out(tmp_path):
    good = {"id": "a" * 16, "rule_id": "SQ002", "scope": "never", "where": "", "line": "", "at": ""}
    jsonfile.write_json(dismissals.path(tmp_path), ["junk", 5, None, ["scope"], {"scope": "always"}, good])
    assert dismissals.load(tmp_path) == [good]


def test_a_place_of_exactly_512_characters_is_kept(tmp_path):
    rows = dismissals.add(tmp_path, "SQ002", "file", "x" * dismissals.MAX_WHERE)
    assert len(rows) == 1 and len(rows[0]["where"]) == 512
