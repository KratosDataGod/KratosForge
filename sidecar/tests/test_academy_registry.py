"""ACD-001: the DP-700 registry has every published objective word for word, each one is covered or has a
gap record, and the checker catches each kind of broken registry."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from fabric_desktop.academy import registry

DOCS = Path(__file__).resolve().parents[2] / "docs" / "academy"

# Microsoft's DP-700 study guide, skills measured, as read on 2026-11-03 (page updated 2026-10-30).
# Kept here apart from the registry so a change to either is caught.
STUDY_GUIDE = {
    "1.1": ["Configure Spark workspace settings", "Configure domain workspace settings",
            "Configure OneLake workspace settings", "Configure data workflow workspace settings"],
    "1.2": ["Configure version control", "Implement database projects", "Create and configure deployment pipelines"],
    "1.3": ["Implement workspace-level access controls", "Implement item-level access controls",
            "Implement row-level, column-level, object-level, and file-level access controls",
            "Implement dynamic data masking", "Apply sensitivity labels to items", "Endorse items"],
    "1.4": ["Choose between a pipeline and a notebook", "Design and implement schedules and event-based triggers",
            "Implement orchestration patterns with notebooks and pipelines, including parameters and dynamic expressions"],
    "2.1": ["Design and implement full and incremental data loads", "Prepare data for loading into a dimensional model",
            "Design and implement a loading pattern for streaming data"],
    "2.2": ["Choose an appropriate data store", "Choose between dataflows, notebooks, and T-SQL for data transformation",
            "Create and manage shortcuts to data", "Implement mirroring", "Ingest data by using pipelines",
            "Transform data by using PySpark, SQL, and KQL", "Denormalize data", "Group and aggregate data",
            "Handle duplicate, missing, and late-arriving data"],
    "2.3": ["Choose an appropriate streaming engine", "Process data by using eventstreams",
            "Process data by using Spark structured streaming", "Process data by using KQL", "Create windowing functions"],
    "3.1": ["Monitor data ingestion", "Monitor data transformation", "Monitor semantic model refresh", "Configure alerts"],
    "3.2": ["Identify and resolve pipeline errors", "Identify and resolve dataflow errors",
            "Identify and resolve notebook errors", "Identify and resolve eventhouse errors",
            "Identify and resolve eventstream errors", "Identify and resolve T-SQL errors"],
    "3.3": ["Optimize a lakehouse table", "Optimize a pipeline", "Optimize a data warehouse",
            "Optimize eventstreams and eventhouses", "Optimize Spark performance", "Optimize query performance"],
}
SKILL_AREAS = {
    "1.1": "Configure Microsoft Fabric workspace settings", "1.2": "Implement lifecycle management in Fabric",
    "1.3": "Configure security and governance", "1.4": "Orchestrate processes",
    "2.1": "Design and implement loading patterns", "2.2": "Ingest and transform batch data",
    "2.3": "Ingest and transform streaming data", "3.1": "Monitor Fabric items",
    "3.2": "Identify and resolve errors", "3.3": "Optimize performance",
}
DOMAINS = {"1": "Implement and manage an analytics solution", "2": "Ingest and transform data",
           "3": "Monitor and optimize an analytics solution"}


@pytest.fixture
def dp700():
    return registry.certification("dp-700")


def test_bundled_packs_have_no_problems():
    assert registry.pack_ids() == ["dp-700"]
    assert registry.problems() == []


def test_every_published_objective_is_there_word_for_word(dp700):
    got = {}
    for o in registry.objectives(dp700):
        got.setdefault(o["skill"], []).append(o["text"])
    assert got == STUDY_GUIDE
    assert {s["id"]: s["title"] for d in dp700["domains"] for s in d["skills"]} == SKILL_AREAS
    assert {d["id"]: d["title"] for d in dp700["domains"]} == DOMAINS
    assert all(d["weight"] == {"min": 30, "max": 35} for d in dp700["domains"])


def test_no_objective_is_left_out_and_fabric_ones_say_why(dp700):
    objs = registry.objectives(dp700)
    assert len(objs) == 49
    assert all(o["exercises"] for o in objs)
    assert all(bool(o["gaps"]) == o["fabric_required"] for o in objs)


def test_coverage_matches_the_plan(dp700):
    cov = registry.coverage(dp700)
    assert cov["objectives"] == cov["with_exercises"] == 49
    assert cov["best_local"] == {"local_verified": 18, "simulated": 20, "knowledge_only": 11}
    assert cov["fabric_required"] == 36
    assert cov["gaps"] == 11


def test_the_version_is_honest_about_its_date(dp700):
    src = dp700["source"]
    assert dp700["version"] == src["page_updated"] == "2026-10-30"
    assert src["effective"] is None and "unverified" in src["effective_note"]
    assert any("never Microsoft's score" in n for n in dp700["notes"])


def test_the_registry_agrees_with_the_coverage_page(dp700):
    page = (DOCS / "dp-700-coverage.md").read_text(encoding="utf-8")
    for o in registry.objectives(dp700):
        row = next(line for line in page.splitlines() if line.startswith(f"| {o['id']} |"))
        cells = [c.strip() for c in row.split("|")]
        assert cells[2] == o["text"]
        assert [e.strip() for e in cells[3].split(",")] == o["exercises"] + o["capstones"]
        assert cells[5].startswith("Yes") == o["fabric_required"]


def test_objective_lookup(dp700):
    assert registry.objective(dp700, "2.2.9")["text"] == "Handle duplicate, missing, and late-arriving data"
    with pytest.raises(ValueError):
        registry.objective(dp700, "9.9.9")
    with pytest.raises(ValueError):
        registry.pack("../learn")


# --- the checker catches broken registries ---------------------------------------------------------


@pytest.fixture
def broken(tmp_path):
    root = tmp_path / "packs"
    shutil.copytree(registry.ROOT, root)
    path = root / "dp-700" / "certification.json"

    def edit(fn, resnap=True):
        cert = json.loads(path.read_text(encoding="utf-8"))
        fn(cert)
        if resnap:
            cert["source"]["snapshot_sha256"] = registry.snapshot(cert)
        path.write_text(json.dumps(cert), encoding="utf-8")
        return registry.problems(root)

    return edit


def _obj(cert, oid):
    return next(o for d in cert["domains"] for s in d["skills"] for o in s["objectives"] if o["id"] == oid)


def test_rewording_an_objective_without_a_new_snapshot_is_caught(broken):
    found = broken(lambda c: _obj(c, "1.1.1").update(text="Configure Spark"), resnap=False)
    assert any("published objectives changed" in p for p in found)


def test_dropping_an_objective_is_caught(broken):
    def drop(c):
        c["domains"][0]["skills"][0]["objectives"].pop(1)
    found = broken(drop)
    assert any("objective 1.1.2 has id 1.1.3" in p for p in found)


def test_an_objective_without_exercises_is_caught(broken):
    assert any("never left out" in p for p in broken(lambda c: _obj(c, "2.2.7").update(exercises=[])))


def test_fabric_objective_without_a_gap_is_caught(broken):
    def ungap(c):
        _obj(c, "1.3.5")["gaps"] = []
        c["gaps"] = [g for g in c["gaps"] if g["id"] != "G-05"]
    assert any("1.3.5: needs Fabric, so it needs a gap record" in p for p in broken(ungap))


def test_gap_lists_must_agree_both_ways(broken):
    found = broken(lambda c: _obj(c, "2.2.8").update(gaps=["G-01"], fabric_required=True))
    assert any("G-01: objective 2.2.8 names this gap but the gap doesn't list it" in p for p in found)
    found = broken(lambda c: _obj(c, "1.1.1").update(gaps=["G-99"]))
    assert any("unknown gap record G-99" in p for p in found)


@pytest.mark.parametrize(("change", "message"), [
    ({"best_local": "fabric_verified"}, "never runs on this PC"),
    ({"lessons": ["LM-99"]}, "unknown Learn lesson LM-99"),
    ({"validation": ["guess"]}, "unknown validator guess"),
    ({"docs": ["https://example.com/page"]}, "isn't the vendor's documentation"),
    ({"exercises": ["X1"]}, "isn't an exercise id"),
    ({"status": "done"}, "status is one of"),
    ({"questions_planned": 0}, "questions_planned"),
], ids=["class", "lesson", "validator", "docs", "exercise", "status", "questions"])
def test_bad_objective_fields_are_caught(broken, change, message):
    assert any(message in p for p in broken(lambda c: _obj(c, "2.2.9").update(change)))


def test_weights_that_cannot_add_up_are_caught(broken):
    def heavy(c):
        for d in c["domains"]:
            d["weight"] = {"min": 40, "max": 45}
    assert any("can't add up to 100%" in p for p in broken(heavy))


def test_a_pack_folder_with_the_wrong_id_is_caught(tmp_path):
    root = tmp_path / "packs"
    shutil.copytree(registry.ROOT / "dp-700", root / "dp-600")
    assert any("doesn't match its folder" in p for p in registry.problems(root))


def _expand(cell):
    ids = []
    for part in (p.strip() for p in cell.split(",")):
        if "–" in part:
            a, b = part.split("–")
            pre = a.rsplit(".", 1)[0]
            ids += [f"{pre}.{i}" for i in range(int(a.rsplit(".", 1)[1]), int(b.rsplit(".", 1)[1]) + 1)]
        else:
            ids.append(part)
    return ids


def test_the_registry_agrees_with_the_curriculum_both_ways(dp700):
    page = (DOCS / "curriculum.md").read_text(encoding="utf-8")
    planned = {}
    for line in page.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 3 and (cells[1][:1] in ("E", "C")) and cells[1][1:].isdigit():
            planned[cells[1]] = set(_expand(cells[3]))
    from_registry = {}
    for o in registry.objectives(dp700):
        for x in o["exercises"] + o["capstones"]:
            from_registry.setdefault(x, set()).add(o["id"])
    assert from_registry == planned
