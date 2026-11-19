"""Mutation batch 96: platform levels at their edges, the evidence each strength may ask for, and the
docs table's rows, checked without reading docs/."""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop import platforms
from fabric_desktop.orchestration.activities import default_registry
from fabric_desktop.platforms import AREAS, FEATURES, PLATFORMS, Feature, Platform
from fabric_desktop.sqlcompat import profiles as sql_profiles


def all_areas(evidence):
    return {a: {"evidence": evidence, "sources": []} for a in AREAS}


def test_every_area_met_is_enterprise_ready_with_nothing_next():
    assert platforms._level(all_areas("live_verified"), "live_verified") == {
        "level": 5, "name": "Enterprise ready", "next_needs": []}


def test_nothing_met_is_discovered_and_needs_sign_in_and_catalog():
    assert platforms._level(all_areas("none"), "unit_tests") == {
        "level": 0, "name": "Discovered", "next_needs": ["connect", "catalog"]}


def test_each_level_is_named_and_says_what_the_next_one_needs():
    areas = all_areas("none")
    for a in ("connect", "catalog", "execute", "test", "deploy"):
        areas[a] = {"evidence": "unit_tests", "sources": []}
    assert platforms._level(areas, "unit_tests") == {"level": 3, "name": "Integrated", "next_needs": ["optimize"]}
    areas["optimize"] = {"evidence": "unit_tests", "sources": []}
    assert platforms._level(areas, "unit_tests") == {"level": 4, "name": "Optimized", "next_needs": ["enterprise"]}
    areas["test"] = {"evidence": "not_applicable", "sources": []}
    assert platforms._level(areas, "unit_tests")["level"] == 4
    assert platforms._level(areas, "live_used") == {"level": 0, "name": "Discovered",
                                                    "next_needs": ["connect", "catalog"]}


def test_a_strength_asks_no_more_than_the_platform_can_verify():
    local, fabric = PLATFORMS["local"], PLATFORMS["fabric"]
    assert platforms._weakest(local, "live_verified") == "unit_tests"
    assert platforms._weakest(local, "live_used") == "unit_tests"
    assert platforms._weakest(local, "unit_tests") == "unit_tests"
    assert platforms._weakest(fabric, "unit_tests") == "unit_tests"
    assert platforms._weakest(fabric, "live_used") == "live_used"
    assert platforms._weakest(fabric, "live_verified") == "live_verified"


def test_platforms_and_features_cant_be_changed_once_made():
    with pytest.raises(dataclasses.FrozenInstanceError):
        PLATFORMS["fabric"].label = "x"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        FEATURES[0].evidence = "none"  # type: ignore[misc]
    assert Platform("p", "P").verified_by == "live_verified"


def test_a_feature_without_evidence_names_the_evidence_that_counts():
    with pytest.raises(ValueError) as caught:
        Feature("fabric", "deploy", "x", "planned", ("publish.py",), ("test_cloud.py",))
    assert str(caught.value) == ("x: a feature needs evidence (unit_tests, live_used, live_verified); "
                                 "put planned work in the platform's notes instead.")


def test_only_real_evidence_is_listed_as_a_source():
    for pid in PLATFORMS:
        for area in platforms._areas(pid).values():
            assert all(s["evidence"] not in ("none", "not_applicable") for s in area["sources"])


def test_evidence_for_an_area_that_doesnt_apply_is_ignored(monkeypatch):
    extra = (Feature("local", "connect", "x", "unit_tests", ("auth.py",), ("test_cloud.py",)),)
    monkeypatch.setattr(platforms, "FEATURES", FEATURES + extra)
    assert platforms._areas("local")["connect"] == {"evidence": "not_applicable", "sources": []}


def test_stronger_evidence_wins_and_weaker_doesnt_lower_it(monkeypatch):
    extra = (Feature("snowflake", "deploy", "a", "live_used", ("publish.py",), ("test_cloud.py",), "used"),
             Feature("snowflake", "deploy", "b", "unit_tests", ("publish.py",), ("test_cloud.py",)))
    monkeypatch.setattr(platforms, "FEATURES", FEATURES + extra)
    deploy = platforms._areas("snowflake")["deploy"]
    assert deploy["evidence"] == "live_used"
    assert [s["source"] for s in deploy["sources"]] == ["a", "b"]


def test_sql_targets_are_the_ones_the_sql_profiles_know():
    known = {p["id"] for p in sql_profiles()}
    assert platforms.manifest("fabric")["sql_targets"] == [
        t for t in ("spark", "warehouse", "endpoint", "sqldb") if t in known]
    assert platforms.manifest("local")["sql_targets"] == (["duckdb"] if "duckdb" in known else [])
    assert platforms.manifest("fabric")["sql_targets"]
    assert platforms.manifest("databricks")["sql_targets"] == []


def test_pipeline_activities_are_counted_by_classification():
    acts = default_registry().entries()
    got = platforms.manifest("fabric")["pipeline_activities"]
    assert list(got) == ["local", "simulated", "static", "cloud"]
    for c, n in got.items():
        assert n == len([a for a in acts if a.classification == c])
    assert "pipeline_activities" not in platforms.manifest("local")


def test_the_docs_table_has_a_row_per_platform(monkeypatch):
    shown = {
        "fabric": ((4, "Optimized", []), (2, "Development", []), (2, "Development", ["test", "deploy"])),
        "databricks": ((2, "Development", []), (0, "Discovered", []), (0, "Discovered", ["connect", "catalog"])),
        "snowflake": ((0, "Discovered", []), (0, "Discovered", []), (0, "Discovered", ["connect"])),
        "local": ((5, "Enterprise ready", []), (5, "Enterprise ready", []), (5, "Enterprise ready", [])),
    }

    def fake(pid):
        lv = {k: {"level": n, "name": name, "next_needs": needs}
              for k, (n, name, needs) in zip(("built", "in_use", "verified"), shown[pid], strict=True)}
        return {"label": PLATFORMS[pid].label, "levels": lv}
    monkeypatch.setattr(platforms, "manifest", fake)
    assert platforms.docs_table() == "\n".join([
        "Manifest 1.", "",
        "| Platform | Built | In use | Verified | To verify the next level |",
        "| --- | --- | --- | --- | --- |",
        ("| Microsoft Fabric | 4 Optimized | 2 Development | 2 Development | "
         "compare or validate results, publish or deploy |"),
        "| Azure Databricks | 2 Development | 0 Discovered | 0 Discovered | sign in, list what the platform holds |",
        "| Snowflake | 0 Discovered | 0 Discovered | 0 Discovered | sign in |",
        "| This PC | 5 Enterprise ready | 5 Enterprise ready | 5 Enterprise ready | nothing |",
    ])
