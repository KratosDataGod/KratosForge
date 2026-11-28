"""academy/guide_check.py held to its exact limits and words: a page of 4 MB is read and one byte more isn't,
a guide without skill areas is refused by name, how objectives are paired (alike at 0.6 exactly, the most
alike first, never twice), how the page's HTML is read (entities, unclosed and nested items), a removed
skill area, and the report's lines. A small made-up guide stands in for the vendor's page."""

from __future__ import annotations

import copy

import pytest

from fabric_desktop.academy import guide_check as gc

PAGE = ("<h2>Skills measured</h2><h3>Implement (30–35%)</h3>"
        "<h4>Plan</h4><ul><li>Plan the lakehouse</li><li>Plan the warehouse</li></ul>"
        "<h4>Build</h4><ul><li>Build the pipeline</li></ul>"
        "<p>Last updated on 2026-02-02</p>")
CERT = {"id": "xx-100", "source": {"url": "https://learn.microsoft.com/x", "read": "2026-01-01", "page_updated": "2026-01-01"},
        "domains": [{"id": "1", "title": "Implement", "weight": {"min": 30, "max": 35}, "skills": [
            {"id": "1.1", "title": "Plan", "objectives": [{"id": "1.1.1", "text": "Plan the lakehouse"},
                                                          {"id": "1.1.2", "text": "Plan the warehouse"}]},
            {"id": "1.2", "title": "Build", "objectives": [{"id": "1.2.1", "text": "Build the pipeline"}]}]}]}


def test_a_page_of_exactly_4_mb_is_read_and_one_byte_more_is_refused():
    padded = PAGE + " " * (4 * 1024 * 1024 - len(PAGE.encode("utf-8")))
    assert len(padded.encode("utf-8")) == gc.MAX_PAGE_BYTES == 4 * 1024 * 1024
    assert gc.extract(padded)["updated"] == "2026-02-02"
    with pytest.raises(ValueError) as caught:
        gc.extract(padded + " ")
    assert str(caught.value) == "The page is over 4 MB; a study guide is much smaller."


def test_a_guide_without_skill_areas_is_refused_by_name():
    flat = {"id": "db-1", "domains": [{"id": "1", "title": "T", "weight": {"min": 1, "max": 2},
                                       "objectives": [{"id": "1.1", "text": "x"}]}]}
    with pytest.raises(ValueError) as caught:
        gc.compare(flat, gc.extract(PAGE))
    assert str(caught.value) == ("db-1: its guide lists objectives straight under each section; only Learn study guides "
                                 "with skill areas are checked here")


def test_objectives_exactly_0_6_alike_are_one_reworded():
    assert gc._alike("abcde", "abcxy") == 0.6
    assert gc._match(["abcde"], ["abcxy"]) == [(0, 0)]
    assert gc._match(["abcde"], ["abxyz"]) == [(0, None), (None, 0)]


def test_the_most_alike_objective_is_paired_first_and_none_is_paired_twice():
    assert gc._match(["abcdef"], ["abcdxy", "abcdeg"]) == [(0, 1), (None, 0)]
    assert gc._match(["abcdef", "abcdxy"], ["abcdeg"]) == [(0, 0), (1, None)]


def test_entities_unclosed_items_and_nested_items_are_read_as_the_page_shows_them():
    outline = gc._Outline()
    outline.feed("<h4>Plan</h4><li>a<h4>Next &amp; last</h4><li>b</li>"
                 "<ul><li>outer<ul><li>inner</li></ul></li></ul>")
    outline.close()
    assert outline.items == [("h4", "Plan"), ("h4", "Next & last"), ("li", "b"), ("li", "inner"), ("li", "outer")]


def test_a_removed_skill_area_has_no_new_id():
    page = PAGE.replace("<h4>Build</h4><ul><li>Build the pipeline</li></ul>", "")
    diff = gc.compare(CERT, gc.extract(page))
    assert diff["skills"] == [{"id": "1.2", "new_id": None, "was": "Build", "now": None}]
    assert diff["removed"] == [{"id": "1.2.1", "text": "Build the pipeline"}]
    assert diff["crosswalk"] == {"1.1.1": ["1.1.1"], "1.1.2": ["1.1.2"], "1.2.1": []}


def test_the_report_says_where_and_when_and_its_crosswalk_marks_what_was_removed():
    page = PAGE.replace("<h4>Build</h4><ul><li>Build the pipeline</li></ul>", "")
    got = gc.check(CERT, page)
    lines = got["report"].splitlines()
    assert lines[2] == ("Compared https://learn.microsoft.com/x (page last updated 2026-02-02) with the registry "
                        "(read 2026-01-01, page updated 2026-01-01).")
    assert "| 1.1.1 | 1.1.1 |" in lines and "| 1.2.1 | (removed) |" in lines
    cert = copy.deepcopy(CERT)
    assert gc.report(cert, got["diff"], None).splitlines()[2].startswith(
        "Compared https://learn.microsoft.com/x (page last updated not shown) with")


def test_a_report_cell_is_cut_at_400_characters():
    assert gc._cell("a" * 500) == "a" * 400
    assert gc._cell("") == "-"
