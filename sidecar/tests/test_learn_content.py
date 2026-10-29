"""LX-02: the Learn content that ships is well formed, its reference solutions give the expected answer,
and the checker catches each kind of broken lesson."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb
import pytest

from fabric_desktop.learn import content

DOCS = Path(__file__).resolve().parents[2] / "docs"


def test_bundled_content_has_no_problems():
    assert content.problems() == []
    assert [m["id"] for m in content.modules()] and content.paths()


def _duckdb_exercises():
    for m in content.modules():
        for ex in m["exercises"]:
            if ex["engine"] == "duckdb" and ex.get("expected"):
                yield pytest.param(m["id"], ex, id=f"{m['id']}:{ex['id']}")


@pytest.mark.parametrize(("module_id", "ex"), list(_duckdb_exercises()))
def test_reference_solution_gives_the_expected_answer(module_id, ex):
    con = duckdb.connect(":memory:")
    if ex.get("setup"):
        con.execute(content.file_text(module_id, ex["setup"]))
    cur = con.execute(content.file_text(module_id, ex["solution"]))
    columns = [d[0] for d in cur.description]
    rows = [list(r) for r in cur.fetchall()]
    want = ex["expected"]
    assert columns == want["columns"]
    ordered = any(c.get("ordered") for c in ex["checks"] if c["kind"] == "result_matches")
    assert rows == want["rows"] if ordered else sorted(map(repr, rows)) == sorted(map(repr, want["rows"]))


def test_docs_pages_a_module_names_exist():
    for m in content.modules():
        for d in m.get("docs") or []:
            if not d.startswith("https://"):
                assert (DOCS / d.split("#")[0]).is_file(), f"{m['id']}: {d}"


def test_drafts_can_be_left_out():
    live = content.modules(include_drafts=False)
    assert all(m["status"] == "live" for m in live)


def test_file_text_stays_in_the_module_folder():
    for bad in ("../module.json", "..", "/etc/passwd", "sub/file.sql", ""):
        with pytest.raises(ValueError):
            content.file_text("LM-01", bad)
    with pytest.raises(ValueError):
        content.file_text("../LM-01", "lesson.md")


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "content"
    shutil.copytree(content.ROOT, root)
    return root


def _edit(root: Path, mid: str, **changes):
    path = root / mid / "module.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    m.update(changes)
    path.write_text(json.dumps(m), encoding="utf-8")
    return m


def _one(root: Path, needle: str):
    found = content.problems(root)
    assert any(needle in p for p in found), found


def test_catches_an_unknown_rule(tree):
    _edit(tree, "LM-01", rules=["SQ999"])
    _one(tree, "unknown best-practice rule SQ999")


def test_catches_an_unknown_check_and_a_check_rule(tree):
    m = _edit(tree, "LM-01")
    m["exercises"][0]["checks"] = [{"kind": "looks_nice"}, {"kind": "no_findings", "rules": ["NB999"]}]
    _edit(tree, "LM-01", exercises=m["exercises"])
    _one(tree, "unknown check looks_nice")
    _one(tree, "no_findings names unknown rule NB999")


def test_catches_a_missing_file_and_a_name_outside_the_folder(tree):
    m = _edit(tree, "LM-01")
    m["exercises"][0]["solution"] = "../../server.py"
    m["exercises"][0]["setup"] = "gone.sql"
    _edit(tree, "LM-01", exercises=m["exercises"])
    _one(tree, "isn't a plain file name")
    _one(tree, "gone.sql is missing")


def test_catches_raw_html_but_not_html_in_code(tree):
    lesson = tree / "LM-01" / "lesson.md"
    lesson.write_text(lesson.read_text(encoding="utf-8") + "\n`<b>` is fine\n\n```html\n<b>also fine</b>\n```\n",
                      encoding="utf-8")
    assert content.problems(tree) == []
    lesson.write_text(lesson.read_text(encoding="utf-8") + "\nClick <a href=x>here</a>\n", encoding="utf-8")
    _one(tree, "raw HTML isn't allowed")


def test_catches_prerequisite_problems(tree):
    shutil.copytree(tree / "LM-01", tree / "LM-90")
    _edit(tree, "LM-90", id="LM-90", prerequisites=["LM-01"])
    _edit(tree, "LM-01", prerequisites=["LM-90"])
    _one(tree, "go round in a circle")
    _edit(tree, "LM-01", prerequisites=["LM-77"])
    _one(tree, "unknown prerequisite LM-77")


def test_catches_identity_and_publishing_problems(tree):
    _edit(tree, "LM-01", id="LM-90", status="live", reviewed=None, docs=["https://example.com/x"], engines=["duckdb", "cobol"])
    _one(tree, "doesn't match its folder")
    _one(tree, "needs the date it was last reviewed")
    _one(tree, "neither a docs page nor a platform's documentation")
    _one(tree, "unknown engine cobol")
    (tree / "lesson-one").mkdir()
    _one(tree, "named by its id")


def test_catches_paths_that_name_unknown_things(tree):
    (tree / "paths.json").write_text(json.dumps([{"id": "x", "title": "X", "modules": ["LM-09"]}]), encoding="utf-8")
    _one(tree, "unknown module LM-09")
    _one(tree, "unknown path data-fundamentals")
