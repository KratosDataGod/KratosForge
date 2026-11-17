"""Mutation-killing tests for parity.py (batch 43): frozen cells, the skin check and the docs table's exact text."""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop import parity
from fabric_desktop.parity import Area, Cell


def test_a_cell_and_an_area_cant_be_changed_after_they_are_made():
    cell = Cell("missing")
    area = Area("fabric", "x", "X", {"local": cell})
    with pytest.raises(dataclasses.FrozenInstanceError):
        cell.status = "built"
    with pytest.raises(dataclasses.FrozenInstanceError):
        area.label = "Y"


def test_a_missing_or_unassessed_look_needs_no_skin(monkeypatch):
    monkeypatch.setattr(parity.skins, "built_in", lambda: [])
    area = Area("fabric", "x", "X", {"visual": parity.MISSING, "workflow": Cell()})
    assert area.cell("visual").status == "missing"
    with pytest.raises(ValueError, match="visual needs a built-in skin that targets fabric"):
        Area("fabric", "y", "Y", {"visual": Cell("partial", ("t.py",), note="Look.")})


def test_the_docs_table_shares_notes_only_when_every_area_agrees(monkeypatch):
    look = Cell("partial", ("t.py",), note="Look.")
    monkeypatch.setattr(parity, "PLATFORMS", {"fabric": "Microsoft Fabric", "databricks": "Azure Databricks"})
    monkeypatch.setattr(parity, "AREAS", (
        Area("fabric", "a", "A", {"visual": look, "cloud_api": Cell("verified", ("t.py",), "Run1", "Listed.")}),
        Area("fabric", "b", "B", {"visual": look, "local": Cell("partial", ("t.py",), note="Some.")}),
        Area("databricks", "c", "C", {"visual": Cell("partial", ("t.py",), note="Blue.")}),
    ))
    header = ("| Area | Visual familiarity | Workflow compatibility | Cloud API integration | Local execution "
              "| Verified runtime equivalence |")
    sep = "| --- | --- | --- | --- | --- | --- |"
    want = "\n".join([
        "### Microsoft Fabric", "", header, sep,
        "| A | Partial | · | Verified | · | · |",
        "| B | Partial | · | · | Partial | · |",
        "", "Notes:", "",
        "- **Every area, Visual familiarity:** Look.",
        "- **A, Cloud API integration:** Listed. Live: Run1.",
        "- **B, Local execution:** Some.",
        "",
        "### Azure Databricks", "", header, sep,
        "| C | Partial | · | · | · | · |",
        "", "Notes:", "",
        "- **C, Visual familiarity:** Blue.",
    ]) + "\n"
    assert parity.docs_table() == want


def test_a_live_run_without_a_note_still_gets_its_own_line(monkeypatch):
    monkeypatch.setattr(parity, "PLATFORMS", {"fabric": "Microsoft Fabric"})
    monkeypatch.setattr(parity, "AREAS", (
        Area("fabric", "a", "A", {"cloud_api": Cell("built", ("t.py",), "Run2")}),
        Area("fabric", "b", "B", {"cloud_api": Cell("built", ("t.py",), "Run2")}),
    ))
    text = parity.docs_table()
    assert "- **A, Cloud API integration:** Live: Run2.\n- **B, Cloud API integration:** Live: Run2.\n" in text
    assert "Every area" not in text
