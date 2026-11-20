"""Left-over files at their boundary (mutation batch 210, a boundary re-score of batch 1,
docs/quality/mutation-testing.md): a spill name needs three dash-separated parts to name its process."""

from __future__ import annotations

from fabric_desktop import local_writes_service as lw


def test_a_spill_name_of_two_parts_names_no_process(tmp_path):
    two = tmp_path / "write-4242"
    two.write_text("x")
    assert lw._owner(two) is None
    three = tmp_path / "write-4242-a.arrow"
    three.write_text("x")
    assert lw._owner(three) == 4242
