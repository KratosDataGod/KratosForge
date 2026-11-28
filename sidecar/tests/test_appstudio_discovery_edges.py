"""App Studio discovery: each cap on what is read from a model, odd item shapes, and description text
that isn't text. A model.bim dict only; nothing is fetched."""

from __future__ import annotations

from fabric_desktop.appstudio import discovery


def _bim(tables, relationships=()):
    return {"model": {"tables": tables, "relationships": list(relationships)}}


def test_text_that_isnt_a_string_reads_as_nothing():
    assert discovery._text(42, 10) == ""
    assert discovery._text(["line one", "line two"], 100) == "line one\nline two"


def test_the_column_cap_stops_reading_and_says_so(monkeypatch):
    monkeypatch.setattr(discovery, "MAX_COLUMNS", 1)
    got = discovery.from_bim(_bim([{"name": "T", "columns": [{"name": "a"}, {"name": "b"}]}]))
    assert [c["name"] for c in got["tables"][0]["columns"]] == ["a"] and got["truncated"]


def test_the_measure_cap_stops_reading_and_hidden_measures_and_formats_are_kept(monkeypatch):
    monkeypatch.setattr(discovery, "MAX_MEASURES", 1)
    got = discovery.from_bim(_bim([{"name": "T", "measures": [
        "not a measure", {"name": "m1", "isHidden": True, "formatString": "0.0"}, {"name": "m2"}]}]))
    assert got["tables"][0]["measures"] == [{"name": "m1", "hidden": True, "format": "0.0"}]
    assert got["truncated"]


def test_hidden_tables_keep_their_description_and_odd_relationships_are_skipped(monkeypatch):
    monkeypatch.setattr(discovery, "MAX_RELATIONSHIPS", 1)
    got = discovery.from_bim(_bim(
        [{"name": "T", "isHidden": True, "description": "Sales by day"}],
        ["not a relationship",
         {"fromTable": "T", "fromColumn": "a", "toTable": "U", "toColumn": "b"},
         {"fromTable": "T", "fromColumn": "c", "toTable": "U", "toColumn": "d"}]))
    assert got["tables"][0]["hidden"] and got["tables"][0]["description"] == "Sales by day"
    assert got["relationships"] == [{"from": "T[a]", "to": "U[b]"}] and got["truncated"]
