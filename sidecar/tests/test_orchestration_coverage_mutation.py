"""Local pipeline coverage: what the first mutation run of orchestration/coverage.py left alive (batch 336).

test_pipeline_coverage.py compares the generated page with docs/orchestration/coverage.md, which a copy of
sidecar/ alone doesn't have. These tests pin the same facts without reading docs/."""

from __future__ import annotations

import pytest

from fabric_desktop.orchestration import coverage

# Every function that doesn't give the reference's value, by status, as docs/orchestration/coverage.md lists them.
NOT_RUNNING = {
    "guid": "not checked", "uriComponent": "not checked", "uriComponentToString": "not checked",
    "getFutureTime": "not checked", "getPastTime": "not checked", "utcNow": "not checked",
    "activity": "not checked", "item": "not checked", "pipeline": "not checked", "variables": "not checked",
    "base64ToBinary": "refused", "binary": "refused", "dataUriToBinary": "refused", "decodeDataUri": "refused",
    "uriComponentToBinary": "refused", "xml": "refused", "xpath": "refused", "rand": "refused",
    "convertFromUtc": "differs",
}


@pytest.fixture(scope="module")
def cov():
    return coverage.coverage()


def test_every_example_but_convert_from_utc_gives_the_references_value(cov):
    status = {r["name"]: r["status"] for r in cov["functions"]}
    assert {n: s for n, s in status.items() if s != "runs"} == NOT_RUNNING
    assert all(status[n] == "runs" for n in coverage.EXAMPLES if n != "convertFromUtc")


def test_a_checked_row_keeps_its_example_and_what_it_gave(cov):
    rows = {r["name"]: r for r in cov["functions"]}
    assert rows["div"]["example"] == "div(11, 5)" and rows["div"]["expected"] == 2 and rows["div"]["got"] == 2
    assert rows["convertFromUtc"]["got"] == "2018-01-01T00:00:00.0000000"
    assert rows["utcNow"]["example"] == "" and rows["utcNow"]["got"] is None
    assert rows["item"]["category"] == "Scope"


def test_canonical_writes_whole_floats_as_ints_inside_lists_and_dicts_with_sorted_keys():
    assert coverage._canonical(2.0) == "2"
    assert coverage._canonical(2.5) == "2.5"
    assert coverage._canonical([1.0, "a"]) == '[1, "a"]'
    assert coverage._canonical({"b": 1.0, "a": [2.0]}) == '{"a": [2], "b": 1}'
    assert coverage._canonical(None) == "null"


def test_a_cell_is_json_with_pipes_escaped_and_empty_for_none():
    assert coverage._cell(None) == ""
    assert coverage._cell("a|b") == '"a\\|b"'
    assert coverage._cell([1, 2]) == "[1, 2]"


def test_the_page_counts_each_status_and_lists_each_category_apart(cov):
    page = coverage.markdown(cov)
    assert "88 functions: 69 runs, 1 differs, 0 fails, 10 not checked, 8 refused." in page
    assert page.count("### ") == len(coverage.FUNCTIONS)
    scope = page.split("### Scope\n", 1)[1]
    assert scope.count("\n| `") == len(coverage.FUNCTIONS["Scope"])
    assert "| `div` | runs | `div(11, 5)` | 2 |  |" in page
    assert ('| `convertFromUtc` | differs | `convertFromUtc(\'2018-01-01T08:00:00.0000000Z\', '
            '\'Pacific Standard Time\')` | "2018-01-01T00:00:00Z" | "2018-01-01T00:00:00.0000000" |') in page
    assert "| `utcNow` | not checked |  |  |  |" in page
    for name, note in coverage.NOTES.items():
        assert f"`{name}`: {note}\n" in page
    assert page.count("`: ") == len(coverage.NOTES)


def test_an_example_that_stops_with_an_error_fails(monkeypatch):
    monkeypatch.setitem(coverage.EXAMPLES, "int", ("int('ten')", 10))
    row = coverage._function_row("Conversion", "int")
    assert row["status"] == "fails" and row["got"].startswith("error: ")
    assert row["example"] == "int('ten')" and row["expected"] == 10
