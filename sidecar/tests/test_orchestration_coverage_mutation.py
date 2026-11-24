"""The local pipeline coverage page (orchestration/coverage.py), held to exact values: every reference example the
evaluator runs gives the reference's value (one differs, and says why), and the page's counts, tables, value
columns and notes are written exactly, checked on a small coverage made here rather than the docs page."""

from __future__ import annotations

from fabric_desktop.orchestration import coverage as C


def test_every_checked_example_gives_the_references_value_but_one():
    rows = {r["name"]: r for r in C.functions()}
    checked = {n for n, r in rows.items() if r["status"] in ("runs", "differs", "fails")}
    assert checked == set(C.EXAMPLES), "every example is run, and only those"
    assert {n for n in checked if rows[n]["status"] != "runs"} == {"convertFromUtc"}, "its note says why"
    assert rows["convertFromUtc"]["got"] == "2018-01-01T00:00:00.0000000"
    for name in checked - {"convertFromUtc"}:
        assert rows[name]["got"] == C.EXAMPLES[name][1] or C._canonical(rows[name]["got"]) == C._canonical(C.EXAMPLES[name][1])


def test_a_cell_is_json_with_its_pipes_escaped_and_nothing_for_none():
    assert C._cell(None) == ""
    assert C._cell(0) == "0" and C._cell(False) == "false"
    assert C._cell("a|b") == '"a\\|b"'


ACTS = [{"type": "Copy", "title": "Copy data", "aliases": ["CopyData"], "classification": "local",
         "status": "runs locally", "evidence": "unit_tests", "planned": ""},
        {"type": "Web", "title": "Web", "aliases": [], "classification": "cloud", "status": "Fabric only",
         "evidence": "", "planned": "AS-2"}]
FNS = [{"name": "concat", "category": "String", "status": "runs", "example": "concat('a')", "expected": "a", "got": "a"},
       {"name": "guid", "category": "String", "status": "not checked", "example": "", "expected": None, "got": None},
       {"name": "div", "category": "Math", "status": "differs", "example": "div(1, 2)", "expected": 0, "got": 0.5},
       {"name": "xml", "category": "Conversion", "status": "refused", "example": "", "expected": None, "got": None},
       {"name": "ticks", "category": "Date", "status": "fails", "example": "ticks('x')", "expected": 1,
        "got": "error: bad|x"}]


def _sections(page: str) -> dict[str, str]:
    return {part.split("\n", 1)[0]: part.split("\n", 1)[1] for part in page.split("\n### ")[1:]}


def test_the_page_counts_each_status():
    lines = C.markdown({"activities": ACTS, "functions": FNS}).split("\n")
    assert any(line.startswith("2 activity types: 1 runs locally, 0 simulated, 0 refused, 1 Fabric only. ")
               for line in lines)
    assert "5 functions: 1 runs, 1 differs, 1 fails, 1 not checked, 1 refused." in lines


def test_the_activity_table_names_every_type_and_its_evidence():
    lines = C.markdown({"activities": ACTS, "functions": FNS}).split("\n")
    assert "| Copy data | `Copy`, `CopyData` | runs locally | unit tests |  |" in lines
    assert "| Web | `Web` | Fabric only | none | AS-2 |" in lines


def test_each_function_is_in_its_own_category_with_a_value_only_where_it_differs_or_fails():
    sections = _sections(C.markdown({"activities": ACTS, "functions": FNS}))
    assert list(sections) == list(C.FUNCTIONS)
    head = "\n| Function | Status | Example | Reference value | Value here |\n| --- | --- | --- | --- | --- |\n"
    assert sections["String"] == (head + "| `concat` | runs | `concat('a')` | \"a\" |  |\n"
                                  "| `guid` | not checked |  |  |  |\n")
    assert sections["Math"] == head + "| `div` | differs | `div(1, 2)` | 0 | 0.5 |\n"
    assert sections["Conversion"] == head + "| `xml` | refused |  |  |  |\n"
    assert sections["Date"] == head + f"| `ticks` | fails | `ticks('x')` | 1 | \"error: bad\\|x\" |\n\n`ticks`: {C.NOTES['ticks']}\n"
    assert sections["Collection"] == head and sections["Scope"] == head
