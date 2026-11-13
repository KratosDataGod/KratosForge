"""The five-level parity matrix (parity.py): every claim names tests that exist, and the page says the same."""

from pathlib import Path

import pytest

from fabric_desktop import parity, skins

TESTS = Path(__file__).resolve().parent
DOC = Path(__file__).resolve().parents[2] / "docs" / "parity.md"
BEGIN, END = "<!-- BEGIN GENERATED: parity -->\n", "<!-- END GENERATED: parity -->"


def test_every_claim_names_tests_that_exist():
    missing = [(a.platform, a.id, lv, t) for a in parity.AREAS for lv in parity.LEVELS
               for t in a.cell(lv).tests if not (TESTS / t).exists()]
    assert not missing


def test_docs_page_matches_the_code():
    text = DOC.read_text(encoding="utf-8")
    assert text[text.index(BEGIN) + len(BEGIN):text.index(END)] == parity.docs_table()


def test_area_ids_are_unique_per_platform():
    keys = [(a.platform, a.id) for a in parity.AREAS]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("cell, message", [
    (dict(status="built"), "needs the tests"),
    (dict(status="verified", tests=("test_x.py",)), "recorded live run"),
    (dict(status="partial", tests=("test_x.py",)), "needs a note"),
    (dict(status="done"), "unknown status"),
])
def test_a_cell_cant_claim_more_than_its_evidence(cell, message):
    with pytest.raises(ValueError, match=message):
        parity.Cell(**cell)


def test_familiar_looks_need_a_skin_and_equivalence_needs_a_comparison(monkeypatch):
    built = parity.Cell("built", ("test_x.py",))
    # Without a built-in skin that targets Snowflake, its look can't be claimed; Fabric's can (Fabric-familiar).
    monkeypatch.setattr(parity.skins, "built_in", lambda: [s for s in skins.BUILT_IN.values() if s["id"] != "snowflake"])
    with pytest.raises(ValueError, match="needs a built-in skin that targets snowflake"):
        parity.Area("snowflake", "x", "X", {"visual": built})
    assert parity.Area("fabric", "x", "X", {"visual": built}).cell("visual").status == "built"
    with pytest.raises(ValueError, match="recorded comparison"):
        parity.Area("fabric", "x", "X", {"equivalence": built})


def test_nothing_claims_snowflake_runs_on_this_pc():
    cell = next(a for a in parity.AREAS if a.platform == "snowflake" and a.id == "worksheets").cell("local")
    assert cell.status == "partial" and "Not a Snowflake runtime" in cell.note
    assert all(a.cell("equivalence").status == "not_assessed" for a in parity.AREAS if a.platform != "fabric")


def test_the_databricks_and_snowflake_skins_raise_looks_but_not_workflow_without_a_page():
    for pid in ("databricks", "snowflake"):
        areas = [a for a in parity.AREAS if a.platform == pid]
        assert {a.cell("visual").status for a in areas} == {"partial"}
        assert all("pages keep KratosForge's own layout" in a.cell("visual").note for a in areas)
    workflow = {(a.platform, a.id) for a in parity.AREAS if a.platform != "fabric" and a.cell("workflow").status != "missing"}
    assert workflow == {("databricks", "jobs")}  # Monitor, named "Jobs and pipelines", holds the job runs


def test_a_note_every_area_shares_is_shown_once():
    table = parity.docs_table()
    assert table.count("Databricks-familiar skin (Preview)") == 1
    assert "- **Every area, Visual familiarity:** The Databricks-familiar skin" in table


def test_the_fabric_skin_raises_looks_and_only_the_workflows_its_rail_places():
    areas = [a for a in parity.AREAS if a.platform == "fabric"]
    assert {a.cell("visual").status for a in areas} == {"partial"}
    assert all("pages keep KratosForge's own layout" in a.cell("visual").note for a in areas)
    workflow = {a.id for a in areas if a.cell("workflow").status != "missing"}
    assert workflow == {"workspaces", "lakehouse", "monitoring"}  # Workspaces, OneLake and Monitor sit where Fabric puts them
    assert {a.cell("workflow").status for a in areas if a.id in workflow} == {"partial"}


def _status_counts(page: str) -> dict[str, int]:
    """The status table at the top of a generated function list: ``{"Runs": 440, ...}``."""
    counts = {}
    for line in page.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0] in ("Runs", "Differs", "Refused", "Fails", "Not checked") and cells[1].isdigit():
            counts.setdefault(cells[0], int(cells[1]))
    return counts


@pytest.mark.parametrize("platform, area, page, test", [
    ("databricks", "sql", "databricks-functions.md", "test_databricks_coverage.py"),
    ("snowflake", "worksheets", "snowflake-functions.md", "test_snowflake_coverage.py"),
])
def test_a_sql_rows_local_claim_follows_its_function_list(platform, area, page, test):
    """WR-303: the SQL row's Local execution cell rests on the generated function list, points to it,
    and can't claim more than partial while the list has functions that differ, are refused, fail or
    aren't checked yet."""
    cell = next(a for a in parity.AREAS if (a.platform, a.id) == (platform, area)).cell("local")
    assert test in cell.tests and f"docs/{page}" in cell.note
    counts = _status_counts((DOC.parent / page).read_text(encoding="utf-8"))
    assert set(counts) == {"Runs", "Differs", "Refused", "Fails", "Not checked"}
    if counts["Differs"] or counts["Refused"] or counts["Fails"] or counts["Not checked"]:
        assert cell.status in ("missing", "partial"), f"{platform} {area}: {counts} doesn't back {cell.status}"
