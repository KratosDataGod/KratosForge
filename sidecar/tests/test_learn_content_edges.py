"""Learning content checks the content tests don't reach: missing fields, bad schema and sizes, exercise
shapes, an unreadable module or paths file, retired modules and the get() refusals. Data only."""

from __future__ import annotations

import json
import shutil

import pytest

from fabric_desktop.learn import content

from test_learn_content import _edit, _one


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "content"
    shutil.copytree(content.ROOT, root)
    return root


def test_a_module_missing_a_required_field_stops_at_that(tree):
    _edit(tree, "LM-01", title="")
    found = [p for p in content.problems(tree) if p.startswith("LM-01")]
    assert found == ["LM-01: module.json needs title"]


def test_schema_kind_minutes_size_and_version_are_checked(tree):
    _edit(tree, "LM-01", schema="other@1", kind="talk", minutes=500, size="huge", version=0)
    _one(tree, f"schema is {content.SCHEMA}")
    _one(tree, "kind is one of module, lab")
    _one(tree, "minutes is a whole number from 1 to 120")
    _one(tree, "size is one of")
    _one(tree, "version is a whole number from 1")


def test_each_exercise_needs_an_id_a_prompt_an_engine_checks_and_sentences(tree):
    m = _edit(tree, "LM-01", datasets=["nope"])
    ex = dict(m["exercises"][0])
    bad = {**ex, "id": "", "prompt": "", "engine": "cobol", "checks": [], "hints": [""]}
    twin = {**ex, "checks": [{"kind": "table_shape"}, {"kind": "faster_than", "baseline": "slow.sql"}]}
    _edit(tree, "LM-01", exercises=[ex, twin, bad])
    _one(tree, "unknown dataset nope")
    _one(tree, "each exercise needs its own id")
    _one(tree, "needs a prompt and a solution")
    _one(tree, "its engine must be one of the module's engines")
    _one(tree, "needs at least one check")
    _one(tree, "hints are a list of sentences")
    _one(tree, "check table_shape needs table")
    _one(tree, "slow.sql is missing")


def test_an_unreadable_module_or_paths_file_is_said_in_words(tree):
    (tree / "LM-01" / "module.json").write_text("{not json", encoding="utf-8")
    _one(tree, "LM-01: module.json can't be read")
    (tree / "paths.json").write_text("[broken", encoding="utf-8")
    assert content.problems(tree)[-1].startswith("paths.json can't be read")


def test_retired_and_draft_modules_and_the_get_refusals(tree):
    _edit(tree, "LM-01", status="retired")
    ids = [m["id"] for m in content.modules(tree)]
    assert "LM-01" not in ids and ids
    assert content.is_id("LAB-02") and not content.is_id("lesson")
    with pytest.raises(ValueError, match="Not a learning module: nope"):
        content.get("nope", tree)
    with pytest.raises(ValueError, match="Not a learning module: LM-99"):
        content.get("LM-99", tree)
    assert content.get("LM-01", tree)["status"] == "retired"
    assert json.loads((tree / "paths.json").read_text(encoding="utf-8")) == content.paths(tree)
