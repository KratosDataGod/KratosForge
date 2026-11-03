"""DBA-001: the Databricks Data Engineer Associate pack has every objective of the current exam guide word
for word, uses the shared registry (made platform-aware, not copied), and can't borrow Fabric's classes,
checks or documentation, nor Fabric packs Databricks'."""

from __future__ import annotations

import json
import shutil

import pytest

from fabric_desktop import progress
from fabric_desktop.academy import exercises, readiness, registry

PACK = "dbx-de-associate"

# The exam guide, "Exam Published May 4, 2026 / Exam Guide Updated August 6, 2026", as read on 2026-11-03
# (certification page https://www.databricks.com/learn/certification/data-engineer-associate). Kept here
# apart from the registry so a change to either is caught.
SECTIONS = {
    "1": ("Databricks Intelligence Platform", 6), "2": ("Data Ingestion and Loading", 21),
    "3": ("Data Transformation and Modeling", 22), "4": ("Working with Lakeflow Jobs", 16),
    "5": ("Implementing CI/CD", 10), "6": ("Troubleshooting, Monitoring, and Optimization", 10),
    "7": ("Governance and Security", 15),
}
GUIDE_STARTS = {  # each objective's first words; the full text is checked through the snapshot hash
    "1.1": "Understand the core components of the Databricks Platform",
    "1.2": "Understand Databricks Platform's compute services",
    "2.1": "Enable and detail data ingestion patterns", "2.2": "Use the COPY INTO command",
    "2.3": "Use Auto Loader with schema enforcement", "2.4": "Configure Lakeflow Connect",
    "2.5": "Use JDBC/ODBC or REST clients in notebooks", "2.6": "Prioritize between Auto Loader",
    "2.7": "Ingest semi-structured and unstructured data", "3.1": "Implement data cleaning by reading bronze tables",
    "3.2": "Combine DataFrames", "3.3": "Manipulate columns, rows, and table structures",
    "3.4": "Perform data deduplication operations", "3.5": "Understand the basic tuning parameters",
    "3.6": "Understand the difference between, and how to build, Gold layer objects",
    "3.7": "Apply data quality checks", "4.1": "Implement control flows", "4.2": "Configure common tasks",
    "4.3": "Implement job schedules", "4.4": "Choose between time-based and data-driven triggers",
    "5.1": "Manage your code development workflow", "5.2": "Understand environment-specific configuration",
    "5.3": "Deploy Declarative Automation Bundles", "5.4": "Understand the Databricks CLI",
    "6.1": "Identify trends in job performance", "6.2": "Use the Lakeflow Jobs UI to monitor pipeline health",
    "6.3": "Identify common performance bottlenecks", "6.4": "Understand the features of Liquid Clustering",
    "6.5": "Diagnose cluster startup failures", "7.1": "Differentiate between managed and external tables",
    "7.2": "Configure access controls using the UI and SQL", "7.3": "Understand column-level masking",
    "7.4": "Understand Unity Catalog ABAC policies",
}
SNAPSHOT = "the guide's published text hashes to the registry's snapshot"


@pytest.fixture
def cert():
    return registry.certification(PACK)


def test_the_pack_is_a_databricks_pack_on_the_shared_registry(cert):
    assert registry.problems() == [] and exercises.problems() == []
    assert registry.pack(PACK)["platform"] == "databricks" and cert["platform"] == "databricks"
    assert registry.platform(cert)["label"] == "DATABRICKS VERIFIED"
    assert cert["version"] == "2026-08-06" and cert["source"]["effective"] == "2026-05-04"
    assert cert["exam"] == {"scored_questions": 45, "unscored_max": 10, "minutes": 90,
                            "question_type": "multiple choice", "code_languages": "SQL when possible, otherwise Python"}


def test_every_section_and_objective_is_there_in_the_guides_own_numbering(cert):
    assert {d["id"]: (d["title"], d["weight"]["min"]) for d in cert["domains"]} == SECTIONS
    assert all(d["weight"]["min"] == d["weight"]["max"] for d in cert["domains"])
    assert sum(d["weight"]["min"] for d in cert["domains"]) == 100
    objs = registry.objectives(cert)
    assert [o["id"] for o in objs] == list(GUIDE_STARTS)
    for o in objs:
        assert o["text"].startswith(GUIDE_STARTS[o["id"]]), o["id"]
        assert o["skill"] == o["domain"] == o["id"].split(".")[0]
    assert cert["source"]["snapshot_sha256"] == registry.snapshot(cert), SNAPSHOT


def test_the_may_wording_is_kept_where_the_august_update_changed_it(cert):
    changed = {o["id"]: o["earlier_wording"]["2026-05-04"] for o in registry.objectives(cert) if o.get("earlier_wording")}
    assert sorted(changed) == ["1.1", "1.2", "3.5", "5.1", "5.2", "5.3"]
    assert "Databricks Repos, committing" in changed["5.1"]
    assert "Lakeflow Spark Declarative Pipelines" in changed["5.3"]


def test_coverage_and_gaps(cert):
    cov = registry.coverage(cert)
    assert cov["objectives"] == cov["with_exercises"] == 33
    assert cov["databricks_required"] == cov["platform_required"] == 20 and "fabric_required" not in cov
    assert cov["gaps"] == 10
    for o in registry.objectives(cert):
        assert "fabric_required" not in o and "fabric_check" not in o["validation"]
        assert bool(o["gaps"]) == o["databricks_required"]
        assert ("databricks_check" in o["validation"]) == o["databricks_required"]
        assert all(d.startswith("https://docs.databricks.com/") for d in o["docs"])
    exercises_listed = {e for o in registry.objectives(cert) for e in o["exercises"]}
    assert exercises_listed == {f"E{n:02d}" for n in range(1, 65)}
    assert {s["id"] for s in cert["supplementary"]} == {"X01", "X02", "X03", "X04"}


def test_the_deny_note_on_7_2(cert):
    note = registry.objective(cert, "7.2")["content_note"]
    assert "GRANT and REVOKE" in note and "MANAGE ACCESS CONTROL" in note


@pytest.fixture
def broken(tmp_path):
    root = tmp_path / "packs"
    shutil.copytree(registry.ROOT, root)

    def edit(pack, fn, resnap=True, pack_fn=None):
        path = root / pack / "certification.json"
        c = json.loads(path.read_text(encoding="utf-8"))
        fn(c)
        if resnap:
            c["source"]["snapshot_sha256"] = registry.snapshot(c)
        path.write_text(json.dumps(c), encoding="utf-8")
        if pack_fn:
            pj = root / pack / "pack.json"
            p = json.loads(pj.read_text(encoding="utf-8"))
            pack_fn(p)
            pj.write_text(json.dumps(p), encoding="utf-8")
        return [p for p in registry.problems(root) if p.startswith(pack)]

    return edit


def _obj(c, oid):
    return next(o for d in c["domains"] for g in (d.get("skills") or [d]) for o in g["objectives"] if o["id"] == oid)


@pytest.mark.parametrize(("change", "message"), [
    ({"fabric_required": False}, "fabric_required belongs to another platform's packs"),
    ({"validation": ["result_matches", "fabric_check"]}, "fabric_check checks another platform"),
    ({"docs": ["https://learn.microsoft.com/azure/databricks/delta/"]}, "isn't the vendor's documentation"),
    ({"databricks_required": "yes"}, "databricks_required is true or false"),
    ({"best_local": "databricks_verified"}, "DATABRICKS VERIFIED never runs on this PC"),
], ids=["fabric-flag", "fabric-check", "other-docs", "flag-type", "class"])
def test_a_databricks_objective_cant_borrow_fabrics_fields(broken, change, message):
    found = broken(PACK, lambda c: _obj(c, "3.1").update(change))
    assert any(message in p for p in found), found


def test_a_databricks_objective_without_its_gap_or_check_is_caught(broken):
    found = broken(PACK, lambda c: _obj(c, "2.3").update(validation=["delta_table"]))
    assert any("2.3: needs Databricks, so its validation includes databricks_check" in p for p in found)


def test_a_changed_objective_without_a_new_snapshot_is_caught(broken):
    found = broken(PACK, lambda c: _obj(c, "2.2").update(text="Use COPY INTO."), resnap=False)
    assert any("published objectives changed" in p for p in found)


def test_a_section_cant_have_skill_areas_and_objectives(broken):
    found = broken(PACK, lambda c: c["domains"][0].update(skills=[]))
    assert any("has skill areas or objectives, not both" in p for p in found)


def test_a_fabric_pack_cant_use_databricks_fields(broken):
    found = broken("dp-700", lambda c: _obj(c, "2.2.9").update(databricks_required=False))
    assert any("databricks_required belongs to another platform's packs" in p for p in found)


def test_a_pack_must_name_a_known_platform(broken):
    found = broken(PACK, lambda c: None, pack_fn=lambda p: p.update(platform="snowflake"))
    assert any("pack platform is one of fabric, databricks" in p for p in found)


@pytest.mark.parametrize(("objective", "cls", "platform", "done"), [
    ("3.1", "local_verified", "databricks", True),       # portable: local work completes it
    ("2.3", "simulated", "databricks", False),           # needs Databricks
    ("2.3", "databricks_verified", "databricks", True),
    ("2.3", "fabric_verified", "databricks", False),     # another platform's class never counts
    ("3.1", "fabric_verified", "databricks", False),
], ids=["portable", "needs-platform", "verified", "fabric-class", "fabric-class-portable"])
def test_completes_by_platform(cert, objective, cls, platform, done):
    assert exercises.completes(registry.objective(cert, objective), cls, platform) is done


def test_databricks_verified_never_completes_a_fabric_objective():
    dp = registry.certification("dp-700")
    assert not exercises.completes(registry.objective(dp, "2.2.9"), "databricks_verified")
    assert exercises.completes(registry.objective(dp, "2.2.9"), "local_verified")


def test_readiness_for_the_databricks_path_starts_empty_and_labelled():
    out = readiness.readiness(progress.empty(), PACK)
    assert out["platform"] == "databricks" and out["platform_label"] == "DATABRICKS VERIFIED"
    assert "isn't Databricks' score" in out["note"] and "Microsoft" not in out["note"]
    assert [d["objectives"] for d in out["domains"]] == [2, 7, 7, 4, 4, 5, 4]
    assert [d["need_platform"] for d in out["domains"]] == [1, 3, 2, 3, 2, 5, 4]
    assert all(x["state"] == "not_started" for x in out["objectives"])
    assert out["next"] == []   # no live exercises yet


@pytest.fixture
def dbx_exercise(tmp_path):
    """A copy of DP-700's E36 placed in the Databricks pack as E19 (objective 3.1), to check exercise rules."""
    root = tmp_path / "packs"
    shutil.copytree(registry.ROOT, root)
    for part in ("exercises", "grading"):
        shutil.copytree(root / "dp-700" / part / "E36", root / PACK / part / "E19")
    path = root / PACK / "exercises" / "E19" / "exercise.json"
    base = json.loads(path.read_text(encoding="utf-8"))
    base.update(id="E19", objectives=["3.1"], status="draft")

    def edit(change):
        path.write_text(json.dumps({**base, **change}), encoding="utf-8")
        return [p for p in exercises.problems(root) if p.startswith(PACK)]

    return edit


def test_a_local_exercise_in_the_databricks_pack_is_accepted(dbx_exercise):
    assert dbx_exercise({}) == []


@pytest.mark.parametrize(("change", "message"), [
    ({"class": "fabric_verified", "adapter": "fabric"}, "a Databricks pack can't hold Fabric exercises"),
    ({"class": "databricks_verified"}, "only the databricks adapter can give DATABRICKS VERIFIED"),
    ({"adapter": "databricks"}, "a databricks exercise is DATABRICKS VERIFIED"),
    ({"engine_label": "Databricks Runtime 16.4"}, "can't claim a Databricks engine"),
    ({"verifies": ["Your query is correct on a SQL warehouse."]}, "can't claim a Databricks engine"),
    ({"engine_label": "Fabric Spark"}, "can't claim a Fabric engine"),
    ({"difficulty": 5}, "difficulty is 1, 2 or 3"),
], ids=["fabric-class", "verified-adapter", "adapter-class", "dbr", "warehouse", "fabric-engine", "difficulty"])
def test_databricks_exercise_rules(dbx_exercise, change, message):
    found = dbx_exercise(change)
    assert any(message in p for p in found), found


def test_the_estimate_note_names_each_vendor():
    assert "isn't Microsoft's score" in readiness.note("fabric") and "isn't Databricks' score" in readiness.note("databricks")
    assert "{" not in readiness.note()
