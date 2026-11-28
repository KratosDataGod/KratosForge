"""Mutation-driven checks for appstudio/templates.py (batch 50).

The starting layouts tile a 12-column grid: every page fills a full-width rectangle with no
overlap and no gap, and build() refuses an unknown target and drops interactions a target
can't express.
"""

from __future__ import annotations

import pytest

from fabric_desktop.appstudio import spec as specs
from fabric_desktop.appstudio import templates

GRID = 12


def _cells(component):
    lay = component["layout"]
    return {(col, row) for col in range(lay["x"], lay["x"] + lay["w"]) for row in range(lay["y"], lay["y"] + lay["h"])}


@pytest.mark.parametrize("template", ["sales", "operations"])
def test_each_page_tiles_the_grid_without_overlap_or_gaps(template):
    doc = templates.build(template, "Tiles")
    for page in doc["pages"]:
        seen: set[tuple[int, int]] = set()
        for c in page["components"]:
            cells = _cells(c)
            assert all(0 <= col < GRID for col, _ in cells), c["id"]
            assert not (cells & seen), f"{c['id']} overlaps another component"
            seen |= cells
        bottom = max(row for _, row in seen) + 1
        assert seen == {(col, row) for col in range(GRID) for row in range(bottom)}, page["id"]


def test_layout_rows_are_cards_then_charts():
    pages = {p["id"]: p for p in templates.build("sales", "S")["pages"]}
    lay = {c["id"]: c["layout"] for c in pages["pg_overview"]["components"]}
    assert lay["c_rev"] == {"x": 0, "y": 0, "w": 3, "h": 2}
    assert lay["c_slicer"] == {"x": 9, "y": 0, "w": 3, "h": 2}
    assert lay["c_month"] == {"x": 6, "y": 2, "w": 6, "h": 4}
    ops = templates.build("operations", "O")["pages"][0]["components"]
    assert [c["layout"]["h"] for c in ops] == [2, 2, 2, 4, 4]
    assert [c["layout"]["w"] for c in ops] == [4, 4, 4, 6, 6]


def test_unknown_target_is_refused():
    with pytest.raises(ValueError, match="target must be one of"):
        templates.build("sales", "S", "excel")


def test_unknown_template_is_refused():
    with pytest.raises(ValueError, match="Choose a template"):
        templates.build("nope", "S")


def _with_drill(name, target):
    doc = templates.TEMPLATES["sales"][2](name, target)
    doc["interactions"].append({"id": "i_drill", "kind": "drillthrough", "from": "c_cat", "to": "pg_overview"})
    return doc


def test_interactions_the_target_cannot_express_are_dropped(monkeypatch):
    monkeypatch.setitem(templates.TEMPLATES, "drill", ("Drill", "test", _with_drill))
    report = templates.build("drill", "D", "powerbi-report")
    assert [i["kind"] for i in report["interactions"]] == ["cross-filter", "drillthrough"]
    app = templates.build("drill", "D", "kratosforge-app")
    assert [i["kind"] for i in app["interactions"]] == ["cross-filter"]
    assert "drillthrough" not in specs.TARGETS["kratosforge-app"]["interactions"]
