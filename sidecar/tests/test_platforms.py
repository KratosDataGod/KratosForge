"""Platform manifests and support levels: built from the registries, levels only as high as the
evidence, and the docs table equal to the code (KUDP-02)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_desktop import connprofiles, platforms
from fabric_desktop.engines import registry
from fabric_desktop.platforms import AREAS, FEATURES, PLATFORMS, Feature

SIDECAR = Path(__file__).resolve().parents[1]
DOCS = SIDECAR.parent / "docs"


def levels(pid):
    return {k: v["level"] for k, v in platforms.manifest(pid)["levels"].items()}


def test_levels_today():
    # Change these only with the evidence: a live test run, or a feature with its tests.
    assert levels("fabric") == {"built": 4, "in_use": 2, "verified": 0}
    assert levels("databricks") == {"built": 2, "in_use": 0, "verified": 0}
    assert levels("snowflake") == {"built": 0, "in_use": 0, "verified": 0}
    assert levels("local") == {"built": 4, "in_use": 4, "verified": 4}


def test_no_platform_is_enterprise_ready_without_enterprise_evidence():
    for pid in PLATFORMS:
        m = platforms.manifest(pid)
        assert m["areas"]["enterprise"]["evidence"] == "none"
        assert all(lv["level"] < 5 for lv in m["levels"].values())


def test_verified_never_exceeds_in_use_and_in_use_never_exceeds_built():
    for pid in PLATFORMS:
        lv = levels(pid)
        assert lv["verified"] <= lv["in_use"] <= lv["built"]


def test_the_manifest_comes_from_the_registries_it_names():
    m = platforms.manifest("databricks")
    assert m["sign_in"] == list(connprofiles.METHODS["databricks"])
    assert {e["id"] for e in m["engines"]} == {d.id for d in registry.ENGINES.values() if d.platform == "databricks"}
    # Engines without an adapter are listed but give no evidence.
    sf = platforms.manifest("snowflake")
    assert sf["engines"] and not any(e["adapter"] for e in sf["engines"])
    assert all(a["evidence"] == "none" for a in sf["areas"].values())
    assert sum(platforms.manifest("fabric")["pipeline_activities"].values()) > 0


def test_only_engines_with_an_adapter_give_evidence():
    sources = {s["source"] for a in platforms._areas("fabric").values() for s in a["sources"]}
    assert "engine fabric-spark" in sources
    for pid in PLATFORMS:
        named = {s["source"] for a in platforms._areas(pid).values() for s in a["sources"]}
        planned = {f"engine {d.id}" for d in registry.ENGINES.values() if not d.adapter}
        assert not named & planned


def test_a_level_needs_every_area_it_lists():
    # Take away Databricks' catalog evidence and it drops to level 0, whatever else it can do.
    areas = platforms._areas("databricks")
    areas["catalog"] = {"evidence": "none", "sources": []}
    got = platforms._level(areas, "unit_tests")
    assert got["level"] == 0 and got["next_needs"] == ["catalog"]


def test_live_evidence_lifts_only_the_strength_it_meets(monkeypatch):
    extra = (Feature("databricks", "connect", "x", "live_verified", ("connprofiles.py",), ("test_connprofiles.py",),
                     "a recorded run"),
             Feature("databricks", "catalog", "y", "live_used", ("connprofiles.py",), ("test_connprofiles.py",),
                     "used live"))
    monkeypatch.setattr(platforms, "FEATURES", FEATURES + extra)
    assert levels("databricks") == {"built": 2, "in_use": 1, "verified": 0}


def test_features_must_name_tests_and_say_how_they_were_used_live():
    with pytest.raises(ValueError, match="name the tests"):
        Feature("fabric", "deploy", "x", "unit_tests", ("publish.py",))
    with pytest.raises(ValueError, match="needs evidence"):
        Feature("fabric", "deploy", "x", "none", ("publish.py",), ("test_cloud.py",))
    with pytest.raises(ValueError, match="used against the live platform"):
        Feature("fabric", "deploy", "x", "live_used", ("publish.py",), ("test_cloud.py",))
    with pytest.raises(ValueError, match="unknown area"):
        Feature("fabric", "magic", "x", "unit_tests", ("publish.py",), ("test_cloud.py",))


def test_every_feature_points_at_code_and_tests_that_exist():
    for f in FEATURES:
        assert f.platform in PLATFORMS and f.area in AREAS
        for module in f.where:
            assert (SIDECAR / "fabric_desktop" / module).is_file(), f"{f.label}: {module} doesn't exist"
        for test in f.tests:
            assert (SIDECAR / "tests" / test).is_file(), f"{f.label}: {test} doesn't exist"


def test_not_applicable_areas_count_as_met_only_where_declared():
    local = platforms.manifest("local")["areas"]
    assert local["connect"]["evidence"] == local["deploy"]["evidence"] == "not_applicable"
    assert platforms.manifest("fabric")["areas"]["connect"]["evidence"] != "not_applicable"


def test_unknown_platforms_are_refused():
    with pytest.raises(ValueError, match="Unknown platform"):
        platforms.manifest("oracle")


def test_manifests_list_every_platform_and_the_engine_registry_version():
    out = platforms.manifests()
    assert [p["id"] for p in out["platforms"]] == list(PLATFORMS)
    assert out["engine_registry"] == registry.REGISTRY_VERSION


def test_the_docs_table_is_the_codes():
    page = (DOCS / "platforms.md").read_text(encoding="utf-8")
    assert platforms.docs_table() in page
