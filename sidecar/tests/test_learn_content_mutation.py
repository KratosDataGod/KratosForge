"""Learning content (learn/content.py): what the mutation run (batch 126, docs/quality/mutation-testing.md)
showed no test pinned. Drafts listed by default, folders that aren't modules, line numbers of raw HTML,
the minutes' bounds, a faster_than check without a baseline and an exercise without an id. Data only."""

from __future__ import annotations

import shutil

import pytest

from fabric_desktop.learn import content

from test_learn_content import _edit


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "content"
    shutil.copytree(content.ROOT, root)
    return root


def test_drafts_are_listed_unless_left_out(tree):
    _edit(tree, "LM-01", status="draft")
    assert "LM-01" in [m["id"] for m in content.modules(tree)]
    assert "LM-01" in [m["id"] for m in content.modules(tree, include_drafts=True)]
    assert "LM-01" not in [m["id"] for m in content.modules(tree, include_drafts=False)]


def test_a_folder_not_named_like_a_module_isnt_listed(tree):
    (tree / "notes").mkdir()
    assert all(content.is_id(m["id"]) for m in content.modules(tree))


def test_raw_html_is_reported_on_its_own_line_number():
    assert content._markdown_problems("LM-01/lesson.md", "<b>bold</b>") == [
        "LM-01/lesson.md line 1: raw HTML isn't allowed in a lesson; put it in a code block"]
    assert content._markdown_problems("x", "text\n```\n<b>\n```\n<i>")[0].startswith("x line 5:")


@pytest.mark.parametrize("minutes, ok", [(0, False), (1, True), (120, True), (121, False)])
def test_minutes_run_from_1_to_120(tree, minutes, ok):
    _edit(tree, "LM-01", minutes=minutes)
    found = [p for p in content.problems(tree) if p == "LM-01: minutes is a whole number from 1 to 120"]
    assert found == ([] if ok else ["LM-01: minutes is a whole number from 1 to 120"])


def test_a_faster_than_check_without_a_baseline_names_no_file(tree):
    m = _edit(tree, "LM-01")
    ex = dict(m["exercises"][0], checks=[{"kind": "faster_than"}])
    _edit(tree, "LM-01", exercises=[ex])
    found = [p for p in content.problems(tree) if p.startswith("LM-01")]
    assert any("check faster_than needs baseline" in p for p in found), found
    assert not any("isn't a plain file name" in p for p in found), found


def test_an_exercise_without_an_id_is_named_with_a_question_mark(tree):
    m = _edit(tree, "LM-01")
    ex = {k: v for k, v in m["exercises"][0].items() if k != "id"}
    _edit(tree, "LM-01", exercises=[ex])
    assert "LM-01 exercise ?: each exercise needs its own id" in content.problems(tree)
