"""Mutation batch 408: academy/guide_check.py, on small hand-made guides and registries."""

from __future__ import annotations

import pytest

from fabric_desktop.academy import guide_check as g


def _cert(objectives: list[str], source: dict | None = None) -> dict:
    cert = {"id": "x-100", "domains": [{"id": "1", "title": "Build", "weight": {"min": 10, "max": 20},
                                        "skills": [{"id": "1.1", "title": "Load",
                                                    "objectives": [{"id": f"1.1.{i + 1}", "text": t}
                                                                   for i, t in enumerate(objectives)]}]}]}
    if source is not None:
        cert["source"] = source
    return cert


def _page(objectives: list[str]) -> dict:
    return {"domains": [{"title": "Build", "weight": {"min": 10, "max": 20},
                         "skills": [{"title": "Load", "objectives": objectives}]}]}


def _html(body: str) -> str:
    return f"<h2>Skills measured</h2><h3>Build (10-20%)</h3>{body}"


def test_a_page_over_four_mb_is_refused_and_one_of_exactly_four_mb_is_read() -> None:
    limit = 4 * 1024 * 1024
    with pytest.raises(ValueError, match="over 4 MB"):
        g.extract(_html("<h4>Load</h4><li>a</li>") + " " * (limit + 1 - len(_html("<h4>Load</h4><li>a</li>"))))
    html = _html("<h4>Load</h4><li>a</li>")
    html += " " * (limit - len(html))
    assert len(html.encode("utf-8")) == limit
    assert g.extract(html)["domains"][0]["skills"][0]["objectives"] == ["a"]


def test_character_references_are_read_as_text() -> None:
    got = g.extract(_html("<h4>Plan &amp; build</h4><li>Use &lt;b&gt; tags</li>"))
    skill = got["domains"][0]["skills"][0]
    assert skill == {"title": "Plan & build", "objectives": ["Use <b> tags"]}


def test_a_list_item_inside_a_heading_closes_on_its_own() -> None:
    outline = g._Outline()
    outline.feed("<h4>A<li>b</li>C</h4>")
    outline.close()
    assert outline.items == [("li", "b"), ("h4", "AC")]


def test_a_guide_without_skill_areas_is_refused() -> None:
    flat = {"id": "dbx", "domains": [{"id": "1", "title": "T", "objectives": [{"id": "1.1", "text": "a"}]}]}
    with pytest.raises(ValueError, match="dbx: its guide lists objectives straight under each section"):
        g.compare(flat, _page(["a"]))


def test_objectives_exactly_as_alike_as_the_threshold_count_as_reworded() -> None:
    assert g._alike("abcde", "abcxy") == g.REWORDED
    diff = g.compare(_cert(["abcde"]), _page(["abcxy"]))
    assert diff["reworded"] == [{"id": "1.1.1", "new_id": "1.1.1", "was": "abcde", "now": "abcxy"}]
    assert diff["added"] == [] and diff["removed"] == []


def test_the_most_alike_new_objective_is_paired_first() -> None:
    diff = g.compare(_cert(["abcdefghij"]), _page(["abcdefXXXX", "abcdefghiX"]))
    assert [r["now"] for r in diff["reworded"]] == ["abcdefghiX"]
    assert diff["added"] == [{"new_id": "1.1.1", "text": "abcdefXXXX"}]


def test_a_removed_skill_area_has_no_new_id() -> None:
    cert = _cert(["a"])
    cert["domains"][0]["skills"].append({"id": "1.2", "title": "Serve totally different",
                                         "objectives": [{"id": "1.2.1", "text": "b"}]})
    diff = g.compare(cert, _page(["a"]))
    assert diff["skills"] == [{"id": "1.2", "new_id": None, "was": "Serve totally different", "now": None}]
    assert diff["crosswalk"] == {"1.1.1": ["1.1.1"], "1.2.1": []}


def test_a_long_cell_is_cut_to_four_hundred_characters() -> None:
    assert g._cell("x" * 500) == "x" * 400


def test_the_report_names_the_source_and_whether_the_page_shows_a_date() -> None:
    cert = _cert(["a", "b"], {"url": "https://learn.microsoft.com/x", "read": "r", "page_updated": "p"})
    diff = g.compare(cert, _page(["a"]))
    text = g.report(cert, diff, None)
    assert "Compared https://learn.microsoft.com/x (page last updated not shown)" in text
    assert "(page last updated 2026-01-02)" in g.report(cert, diff, "2026-01-02")
    assert "| 1.1.2 | (removed) |" in text
    assert "| 1.1.1 | 1.1.1 |" in text


def test_a_report_for_a_registry_without_a_source_still_renders() -> None:
    cert = _cert(["a", "b"])
    text = g.report(cert, g.compare(cert, _page(["a"])), None)
    assert "Compared None (page last updated not shown)" in text
