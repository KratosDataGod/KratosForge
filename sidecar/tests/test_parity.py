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
