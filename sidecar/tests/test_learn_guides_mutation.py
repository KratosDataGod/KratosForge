"""Kill tests for learn/guides.py (docs/quality/mutation-testing.md, batch 116).

Text before the first rule heading isn't a guide, a title line with few tags, a later bold line,
an example's note on its Before or After mark, building from every page in rule order, and the
shipped guides only when none are given.
"""

from __future__ import annotations

from fabric_desktop.learn import guides


def test_text_before_the_first_heading_is_not_a_guide():
    got = guides.parse("NB001\nWhy: not a guide.\n\n### SQ999\n\nWhy: this one.\n")
    assert list(got) == ["SQ999"]


def test_a_title_line_with_few_tags_leaves_area_and_importance_empty():
    one = guides.parse("### SQ999\n\n**Thing** · Warning\n\nWhy: x.\n")["SQ999"]
    assert (one["area"], one["importance"]) == ("", "")
    two = guides.parse("### SQ999\n\n**Thing** · Warning · Performance\n\nWhy: x.\n")["SQ999"]
    assert (two["area"], two["importance"]) == ("Performance", "")


def test_a_later_bold_line_changes_nothing():
    md = ("### SQ999\n\n**Thing** · Warning · Performance · Advanced\n\nWhy: one line.\n"
          "**Other** · Error · Security · Critical\n")
    got = guides.parse(md)["SQ999"]
    assert (got["area"], got["importance"], got["why"]) == ("Performance", "Advanced", "One line.")


def test_a_note_on_a_mark_is_kept_as_a_comment():
    md = "### SQ999\n\nWhy: x.\n\n```sql\n-- Before: slow\nSELECT 1\n-- After: copy it once\nSELECT 2\n```\n"
    got = guides.parse(md)["SQ999"]
    assert got["before"] == "-- slow\nSELECT 1" and got["after"] == "-- copy it once\nSELECT 2"


def test_build_reads_every_page_in_rule_order(tmp_path):
    ids = ["SQ002", "NB001", "SM001", "PG1"]
    for name, rid in zip(guides.DOCS, ids, strict=True):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(f"### {rid}\n\nWhy: {rid}.\n", encoding="utf-8")
    got = guides.build(tmp_path)
    assert list(got) == sorted(ids) and got["PG1"]["why"] == "PG1."


def test_the_shipped_guides_are_read_only_when_none_are_given():
    assert guides.for_code("notebook_not_an_item_id") == "PG1"
    assert guides.for_code("notebook_not_an_item_id", {}) is None
