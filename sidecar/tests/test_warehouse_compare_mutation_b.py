"""Mutation batch 510: the side-by-side view of a long script with many repeated lines."""

from __future__ import annotations

from fabric_desktop.warehouse_compare import side_by_side


def test_a_long_script_of_repeated_lines_still_lines_up_line_by_line():
    # difflib's autojunk would treat lines seen in more than 1% of a 200+ line script as junk,
    # and then the whole script would show as one changed block.
    body = "\n".join(["SELECT 1", "GO"] * 150)
    left = body + "\nSELECT 'warehouse'"
    right = "-- added\n" + body + "\nSELECT 'project'"
    rows = side_by_side(left, right)["rows"]
    assert rows[0] == {"op": "added", "left": None, "right": "-- added"}
    assert [r["op"] for r in rows[1:301]] == ["same"] * 300
    assert rows[301] == {"op": "changed", "left": "SELECT 'warehouse'", "right": "SELECT 'project'"}
    assert len(rows) == 302
