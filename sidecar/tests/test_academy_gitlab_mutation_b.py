"""Mutation tests for academy/gitlab.py (batch 454): the session's caps at their exact edges, the wording
of refusals and rules, and the checks that read files at a ref."""

from __future__ import annotations

import shutil

import pytest

from fabric_desktop.academy import gitlab
from test_academy_gitlab import EX, FILES, ITEM

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.fixture
def folder(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for name, text in FILES.items():
        (src / name).write_text(text, encoding="utf-8")
    return src


@pytest.fixture
def repo(folder, tmp_path):
    dest = tmp_path / "run"
    dest.mkdir()
    return gitlab.build(dest, folder, EX)


# ---- parsing


def test_a_bare_git_line_needs_a_subcommand():
    with pytest.raises(gitlab.Refused, match="Line 1: A git line needs a subcommand, such as git status."):
        gitlab.parse("git\n")


def test_a_lone_dash_is_an_argument_not_flags():
    assert gitlab.parse("git checkout -\n")[0]["argv"] == ["checkout", "-"]


def test_a_session_of_exactly_the_byte_cap_is_read():
    text = "#" * (gitlab.MAX_BYTES - 1) + "\n"
    assert len(text.encode("utf-8")) == gitlab.MAX_BYTES and gitlab.parse(text) == []
    with pytest.raises(gitlab.Refused, match="over 256 KB"):
        gitlab.parse("#" + text)


def test_a_path_of_twelve_parts_or_240_characters_is_inside_and_more_isnt():
    assert gitlab.safe_path("/".join("a" * 12)) == "/".join("a" * 12)
    with pytest.raises(gitlab.Refused, match="nested too deeply"):
        gitlab.safe_path("/".join("a" * 13))
    assert gitlab.safe_path("a" * 240) == "a" * 240
    with pytest.raises(gitlab.Refused, match="nested too deeply"):
        gitlab.safe_path("a" * 241)


def test_spec_problems_count_commits_from_1():
    ex = {"repo": {"commits": [{"branch": "main"}, {"message": "m"}]}}
    assert gitlab.spec_problems("E1", ex) == ["E1: repo commit 1 needs a branch and a message",
                                              "E1: repo commit 2 needs a branch and a message"]


def test_a_failed_build_step_quotes_400_characters_of_gits_output(monkeypatch, tmp_path):
    monkeypatch.setattr(gitlab, "_git", lambda repo, argv: (1, "x" * 500))
    with pytest.raises(RuntimeError) as caught:
        gitlab._must(tmp_path, ["status"])
    assert str(caught.value) == "Building the exercise's repository failed at git status: " + "x" * 400


# ---- replay and run


def test_a_session_stops_at_ninety_seconds(monkeypatch, tmp_path):
    clock = iter([0.0, 90.0])
    monkeypatch.setattr(gitlab.time, "monotonic", lambda: next(clock))
    out = gitlab.replay(tmp_path, gitlab.parse("git status\n"))
    assert out == [{"line": 1, "text": "git status", "exit": 124,
                    "output": "Stopped: the session took longer than 90 seconds."}]


def test_a_write_over_a_folder_says_its_a_folder(repo):
    (repo / "box").mkdir()
    out = gitlab.replay(repo, gitlab.parse("write box\nhi\nend\n"))
    assert out[0]["exit"] == 1 and out[0]["output"] == "box can't be written: it's a folder."


def test_run_shows_200_rows_and_says_more_only_past_them(folder, tmp_path):
    session = "git ls-files\n" * 198
    first = tmp_path / "a"
    first.mkdir()
    got = gitlab.run(first, folder, EX, session)
    assert len(got["rows"]) == 200 and got["more"] is False
    second = tmp_path / "b"
    second.mkdir()
    got = gitlab.run(second, folder, EX, session + "git ls-files\n")
    assert len(got["rows"]) == 200 and got["more"] is True


# ---- rules


def test_a_branch_that_is_there_passes(repo):
    assert gitlab.repo_matches(repo, [{"branch": "main", "exists": True}])["passed"]


def test_a_file_at_a_ref_over_the_size_cap_is_read_as_missing(repo, monkeypatch):
    monkeypatch.setattr(gitlab, "MAX_FILE", 10)
    got = gitlab.repo_matches(repo, [{"file": ITEM, "ref": "main", "contains": "Sales"}])
    assert not got["passed"] and got["detail"] == f"{ITEM} on main is missing."


def test_a_json_rule_on_a_file_that_isnt_json_fails_with_the_reason(repo):
    (repo / ITEM).write_text("not json", encoding="utf-8")
    got = gitlab.repo_matches(repo, [{"file": ITEM, "json": [{"path": "x", "equals": 1}], "why": "Why."}])
    assert not got["passed"] and got["detail"].endswith(" Why.")


@pytest.mark.parametrize(("rule", "said"), [
    ({"ancestor": "nope", "of": "main"}, "nope isn't a branch or commit in the repository."),
    ({"ancestor": "main", "of": "gone"}, "gone isn't a branch or commit in the repository."),
])
def test_an_ancestor_rule_names_the_ref_that_isnt_there(repo, rule, said):
    got = gitlab.repo_matches(repo, [rule])
    assert not got["passed"] and got["detail"] == said


def test_count_max_allows_exactly_the_maximum(repo):
    assert gitlab.repo_matches(repo, [{"ref": "feature/days", "since": "main", "count_max": 1}])["passed"]
    got = gitlab.repo_matches(repo, [{"ref": "feature/days", "since": "main", "count_max": 0}])
    assert not got["passed"]
    assert got["detail"] == "feature/days has 1 commits since main; it should have at most 0."


def test_conflict_markers_are_looked_for_in_the_first_2000_files_only(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.txt").write_text("fine\n", encoding="utf-8")
    (src / "bad.txt").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n", encoding="utf-8")
    files = {f"f{i:04}.txt": "ok.txt" for i in range(2000)} | {"zz.txt": "bad.txt"}
    ex = {"repo": {"commits": [{"branch": "main", "message": "many", "files": files}]}}
    dest = tmp_path / "run"
    dest.mkdir()
    repo = gitlab.build(dest, src, ex)
    assert gitlab.repo_matches(repo, [{"conflict_markers": False}])["passed"]
