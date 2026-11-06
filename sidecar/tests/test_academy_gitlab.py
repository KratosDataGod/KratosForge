"""git_lab exercises: the learner's Git session replayed with real Git in a fresh repository built from
the exercise's history. Only allow-listed subcommands run, nothing reaches outside the repository, and
every failure says what is wrong in words."""

from __future__ import annotations

import functools
import json
import shutil
import sys

import pytest

from fabric_desktop.academy import exercises, gitlab, lab, registry
from fabric_desktop.academy_service import AcademyMixin

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

PACK, EID = "dp-700", "E36"
ITEM = "Sales.DataPipeline/pipeline-content.json"


def pipeline(days):
    return json.dumps({"name": "Sales", "properties": {"activities": [{"name": "Copy", "type": "Copy"}],
                                                       "parameters": {"days": days}}}, indent=2) + "\n"


FILES = {"main.json": pipeline(7), "feature.json": pipeline(30), "main2.json": pipeline(14)}
REPO = {"commits": [{"branch": "main", "message": "Add the Sales pipeline", "files": {ITEM: "main.json"}},
                    {"branch": "feature/days", "from": "main", "message": "Load 30 days", "files": {ITEM: "feature.json"}},
                    {"branch": "main", "message": "Load 14 days", "files": {ITEM: "main2.json"}}],
        "head": "main"}
VALIDATION = [
    {"kind": "repo_matches", "fail": "Merge feature/days into main and finish the merge.",
     "rules": [{"clean": True, "why": "A merge isn't done until it's committed."},
               {"ancestor": "feature/days", "of": "main"}, {"ref": "main", "parents": 2}]},
    {"kind": "repo_matches", "fail": "Resolve the conflict by keeping the feature's 30 days.",
     "rules": [{"conflict_markers": False},
               {"file": ITEM, "ref": "main", "json": [{"path": "properties.parameters.days", "equals": 30}]}]},
]
EX = {"id": EID, "starter": "session.git", "files": sorted(FILES), "repo": REPO, "validation": VALIDATION}
STARTER = "# Merge feature/days into main, resolve the conflict, and commit.\ngit status\n"
SOLUTION = f"""git merge feature/days
write {ITEM}
{pipeline(30).rstrip()}
end
git add {ITEM}
git commit -m "Merge feature/days: keep 30 days"
"""
WRONG = {"wrong-no-merge.git": "git status\n",
         "wrong-ours.git": "git merge -X ours feature/days\n",
         "wrong-markers-committed.git": f"git merge feature/days\ngit add {ITEM}\ngit commit -m 'merged'\n",
         "wrong-not-committed.git": f"git merge feature/days\nwrite {ITEM}\n{pipeline(30).rstrip()}\nend\ngit add {ITEM}\n"}


@pytest.fixture
def folder(tmp_path):
    for name, text in FILES.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


# ---- the adapter

def test_reference_passes_and_wrong_answers_fail(folder, tmp_path):
    grading = tmp_path / "grading"
    grading.mkdir()
    (grading / "solution.git").write_text(SOLUTION, encoding="utf-8")
    for name, text in WRONG.items():
        (grading / name).write_text(text, encoding="utf-8")
    result = gitlab.self_test(folder, grading, EX)
    assert result["solution"]["passed"], result["solution"]
    assert set(result["wrong"]) == set(WRONG) and not any(r["passed"] for r in result["wrong"].values()), result["wrong"]
    said = {k: [r["detail"] for r in v["results"] if not r["passed"]] for k, v in result["wrong"].items()}
    assert "feature/days isn't merged into main." in said["wrong-no-merge.git"]
    assert any("days is 14; it should be 30" in d for d in said["wrong-ours.git"])
    assert any("still has conflict markers" in d for d in said["wrong-markers-committed.git"])
    assert any("A merge is still in progress." in d for d in said["wrong-not-committed.git"])


def test_same_session_same_commits(folder, tmp_path):
    rows = []
    for n in range(2):
        dest = tmp_path / f"run{n}"
        dest.mkdir()
        rows.append(gitlab.run(dest, folder, EX, SOLUTION)["rows"])
    assert rows[0] == rows[1]
    history = rows[0][-1]
    assert history[1] == "after: history" and "Merge feature/days: keep 30 days" in history[3]


def test_run_shows_each_step_and_a_failed_command_doesnt_stop_the_session(folder, tmp_path):
    got = gitlab.run(tmp_path, folder, EX, "git merge feature/days\ngit status --short\n")
    assert got["columns"] == ["line", "step", "exit", "output"]
    merge, status = got["rows"][0], got["rows"][1]
    assert merge[:3] == [1, "git merge feature/days", 1] and "CONFLICT" in merge[3]
    assert status[:3] == [2, "git status --short", 0] and ITEM in status[3]


def test_write_line_numbers_and_comments():
    steps = gitlab.parse("# a comment\n\ngit status\nwrite a/b.json\n{}\nend\ngit add -A\n")
    assert [(s["line"], s["op"]) for s in steps] == [(3, "git"), (4, "write"), (7, "git")]
    assert steps[1]["path"] == "a/b.json" and steps[1]["content"] == "{}\n"


@pytest.mark.parametrize(("session", "said"), [
    ("git -c core.fsmonitor=evil status", "Options before the subcommand"),
    ("git -C .. status", "Options before the subcommand"),
    ("git --git-dir=x status", "Options before the subcommand"),
    ("git config core.hooksPath x", "git config isn't part of this lab"),
    ("git push origin main", "git push isn't part of this lab"),
    ("git clone https://example.com/r.git", "git clone isn't part of this lab"),
    ("git difftool", "git difftool isn't part of this lab"),
    ("git diff --no-index /etc/passwd x", "--no-index isn't run here"),
    ("git log --output=out.txt", "--output isn't run here"),
    ("git show --ext-diff HEAD", "--ext-diff isn't run here"),
    ("git commit -S -m x", "-S isn't run here"),
    ("git commit -aS -m x", "-S isn't run here"),
    ("git commit -F msg.txt", "-F isn't run here"),
    ("git tag -s v1 -m x", "-s isn't run here"),
    ("git merge -s custom feature", "-s isn't run here"),
    ("git merge --strategy=custom feature", "--strategy isn't run here"),
    ("git add -p", "-p isn't run here"),
    # -m is a value only for commit, merge, tag and stash; elsewhere it's a flag and the next argument is checked.
    ("git log -m --output=/tmp/x", "--output isn't run here"),
    ("git show -m --ext-diff HEAD", "--ext-diff isn't run here"),
    ("git log -m /etc/passwd", "points outside the repository"),
    ("git commit -m --output=x", "--output isn't run here"),
    # Git takes an unambiguous prefix of a long option.
    ("git diff --fil=x", "--file isn't run here"),
    ("git log --outp=/tmp/x", "--output isn't run here"),
    ("git commit --templ=x", "--template isn't run here"),
    ("git tag --sig v1", "--sign isn't run here"),
    ("git merge --strat=x f", "--strategy isn't run here"),
    # A value glued to a short option is checked as a path.
    ("git diff -O/etc/passwd", "points outside the repository"),
    ("git log -pO../x", "points outside the repository"),
    ("git diff -OC:/x", "points outside the repository"),
    ("git merge -sXtheirs f", "-s isn't run here"),
    ("git merge -X../x f", "points outside the repository"),
    ("git add ../outside.txt", "points outside the repository"),
    ("git add /etc/passwd", "points outside the repository"),
    ("git add C:/Windows/win.ini", "points outside the repository"),
    ("git restore --source=HEAD --worktree=../x", "points outside the repository"),
    ("git add ~/x", "points outside the repository"),
    ("ls -la", "each line is a git command or a write block"),
    ("git commit -m 'unclosed", "check the quotes"),
    ("write .git/config\n[core]\nend", "out of .git"),
    ("write sub/.Git/config\nend", "out of .git"),
    ("write .git./hooks/pre-commit\nend", "out of .git"),
    ("write GIT~1/config\nend", "out of .git"),
    ("write ../x\nend", "out of .git"),
    ("write /tmp/x\nend", "isn't a path inside the repository"),
    ("write a/CON.txt\nend", "names Windows allows"),
    ("write a:b\nend", "isn't a path inside the repository"),
    ("write a.json\n{}", "has no end line"),
    ("write .gitattributes\n* filter=x\nend", "not be .gitattributes"),
    ("write sub/.GitAttributes.\n* diff=x\nend", "not be .gitattributes"),
    ("git mv notes.txt .gitattributes", "at .gitattributes"),
    ("git checkout other -- sub/.gitattributes", "at .gitattributes"),
])
def test_refused_sessions_say_why(session, said):
    with pytest.raises(gitlab.Refused, match=None) as err:
        gitlab.parse(session)
    assert said in str(err.value) and str(err.value).startswith("Line "), str(err.value)


def test_flags_named_like_refused_options_still_run():
    for line in ("git log -m main", "git branch -m old new", "git stash push -m wip", "git commit --signoff -m x",
                 "git log --first-parent", "git merge --no-ff feature/days", "git log -3", "git log --stat",
                 "git merge -Xtheirs feature/days", "git merge -X theirs feature/days", "git cherry-pick -Xours HEAD",
                 "git diff --text", "git commit --edit -m x"):
        assert gitlab.parse(line), line


def test_a_run_folder_that_cant_be_removed_keeps_runs_result(svc, monkeypatch):
    svc.academy_start(PACK, EID)

    def locked(path):
        raise PermissionError(13, "in use by another process")

    monkeypatch.setattr(lab.git_ops, "remove_clone", locked)
    got = svc.academy_run(PACK, EID, STARTER)
    assert got["rows"][0][1] == "git status"


def test_git_itself_refuses_abbreviated_options(folder, tmp_path):
    dest = tmp_path / "run"
    dest.mkdir()
    repo = gitlab.build(dest, folder, EX)
    code, out = gitlab._git(repo, ["log", "--onel"])
    assert code != 0, out


def test_only_command_line_configuration_applies(folder, tmp_path):
    """No system, global or ProgramData configuration: every setting Git sees came from the lab's -c options,
    or from the repository the lab made."""
    dest = tmp_path / "run"
    dest.mkdir()
    repo = gitlab.build(dest, folder, EX)
    code, out = gitlab._git(repo, ["config", "--list", "--show-origin", "--show-scope"])
    assert code == 0, out
    scopes = {line.split("\t", 1)[0] for line in out.splitlines() if line.strip()}
    assert scopes <= {"command", "local"}, out
    local = [l for l in out.splitlines() if l.startswith("local")]
    assert all(l.split("\t")[-1].startswith("core.") for l in local), local   # only what git init wrote


def test_a_message_may_say_anything():
    assert gitlab.parse('git commit -m "see ../docs and /etc"')[0]["argv"] == ["commit", "-m", "see ../docs and /etc"]
    assert gitlab.parse("git commit --message=../x")
    assert gitlab.parse("git commit -am fix")[0]["argv"] == ["commit", "-am", "fix"]
    assert gitlab.parse("git log main..feature/days --oneline")


def test_a_refused_session_fails_every_check_in_words(folder):
    res = gitlab.grade("git push\n", folder, EX)
    assert not res["passed"] and len(res["results"]) == 2
    assert all("git push isn't part of this lab" in r["detail"] for r in res["results"])


def test_oversized_and_too_many_steps():
    with pytest.raises(gitlab.Refused, match="over 256 KB"):
        gitlab.parse("#" * (gitlab.MAX_BYTES + 1))
    with pytest.raises(gitlab.Refused, match="more than 200 steps"):
        gitlab.parse("git status\n" * 201)


def test_global_configuration_and_hooks_never_apply(folder, tmp_path, monkeypatch):
    """The person's own Git configuration (an alias, a hook folder, a pager) is never read, and a hook the
    session can't write anyway wouldn't run."""
    home = tmp_path / "home"
    home.mkdir()
    marker = tmp_path / "ran"
    hooks = home / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o755)
    (home / ".gitconfig").write_text(f"[core]\n\thooksPath = {hooks}\n[alias]\n\tstatus = !touch '{marker}'\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
    dest = tmp_path / "run"
    dest.mkdir()
    gitlab.run(dest, folder, EX, SOLUTION)
    assert not marker.exists()


def test_a_write_never_follows_a_link(folder, tmp_path):
    dest = tmp_path / "run"
    dest.mkdir()
    repo = gitlab.build(dest, folder, EX)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (repo / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("no symbolic links here")
    out = gitlab.replay(repo, gitlab.parse("write linked/x.txt\nhi\nend\n"))
    assert out[0]["exit"] == 1 and "link" in out[0]["output"] and not (outside / "x.txt").exists()


@pytest.mark.parametrize(("rule", "said"), [
    ({"branch": "feature/days", "exists": False}, "Branch feature/days is still there."),
    ({"branch": "nope", "exists": True}, "Branch nope is missing."),
    ({"head": "feature/days"}, "The repository is on main; it should be on feature/days."),
    ({"file": "missing.json", "exists": True}, "missing.json in the work tree is missing."),
    ({"file": ITEM, "ref": "feature/days", "contains": '"days": 7'}, "doesn't include"),
    ({"file": ITEM, "not_contains": '"days": 14'}, "includes"),
    ({"ref": "main", "parents": 2}, "has 1 parent; it should have 2"),
    ({"ref": "feature/days", "since": "main", "count_min": 2}, "has 1 commit since main; it needs at least 2"),
    ({"ref": "nope", "parents": 1}, "nope isn't a branch or commit"),
    ({"ancestor": "main", "of": "feature/days"}, "main isn't merged into feature/days."),
    ({"clean": False}, "The work tree has no changes."),
])
def test_rules_say_what_they_found(folder, tmp_path, rule, said):
    dest = tmp_path / "run"
    dest.mkdir()
    repo = gitlab.build(dest, folder, EX)
    got = gitlab.repo_matches(repo, [rule])
    assert not got["passed"] and said in got["detail"], got


@pytest.mark.parametrize(("check", "said"), [
    ({"kind": "repo_matches"}, "needs rules"),
    ({"kind": "repo_matches", "rules": [{}]}, "needs one of branch"),
    ({"kind": "repo_matches", "rules": [{"branch": "x"}]}, "(branch) needs exists"),
    ({"kind": "repo_matches", "rules": [{"ancestor": "x"}]}, "(ancestor) needs of"),
    ({"kind": "repo_matches", "rules": [{"file": "a"}]}, "needs one test"),
    ({"kind": "repo_matches", "rules": [{"file": "../a", "exists": True}]}, "isn't a path this lab writes"),
    ({"kind": "repo_matches", "rules": [{"file": "a", "json": [{"path": "x"}]}]}, "needs one test"),
    ({"kind": "repo_matches", "rules": [{"ref": "main"}]}, "needs parents, or since"),
    ({"kind": "repo_matches", "rules": [{"ref": "main", "parents": -1}]}, "whole number"),
    ({"kind": "repo_matches", "rules": [{"clean": True, "why": ""}]}, "why is a sentence"),
])
def test_check_problems(check, said):
    assert any(said in p for p in gitlab.check_problems(check)), gitlab.check_problems(check)


def test_well_formed_checks_have_no_problems():
    assert [p for c in VALIDATION for p in gitlab.check_problems(c)] == []
    assert gitlab.spec_problems(EID, EX) == []


# ---- in a pack: problems() and self_test()

@pytest.fixture
def git_pack(tmp_path):
    root = tmp_path / "packs"
    shutil.copytree(registry.ROOT, root)
    folder, grading = root / PACK / "exercises" / EID, root / PACK / "grading" / EID
    shutil.rmtree(grading)
    grading.mkdir()
    for p in folder.iterdir():
        if p.suffix == ".sql":
            p.unlink()
    path = folder / "exercise.json"
    ex = json.loads(path.read_text(encoding="utf-8"))
    ex.update({"adapter": "git_lab", "class": "local_verified", "engine_label": "Git on this PC",
               "starter": "session.git", "files": sorted(FILES), "repo": REPO, "validation": VALIDATION, "parity": []})
    ex.pop("setup", None)
    path.write_text(json.dumps(ex), encoding="utf-8")
    (folder / "session.git").write_text(STARTER, encoding="utf-8")
    for name, text in FILES.items():
        (folder / name).write_text(text, encoding="utf-8")
    (grading / "solution.git").write_text(SOLUTION, encoding="utf-8")
    for name, text in WRONG.items():
        (grading / name).write_text(text, encoding="utf-8")
    return root


def test_a_git_exercise_is_well_formed_and_self_tests(git_pack):
    assert exercises.problems(git_pack) == []
    result = exercises.self_test(PACK, EID, git_pack)
    assert result["solution"]["passed"], result["solution"]
    assert set(result["wrong"]) == set(WRONG) and not any(r["passed"] for r in result["wrong"].values())


@pytest.mark.parametrize(("change", "remove", "message"), [
    ({}, "grading/E36/solution.git", "reference session at grading/E36/solution.git"),
    ({"starter": "settings.json"}, None, "starts from a Git session"),
    ({"repo": {}}, None, "needs repo.commits"),
    ({"repo": {"commits": [{"branch": "main", "message": "x", "files": {"a.json": "other.json"}}]}}, None,
     "takes 'other.json', which isn't listed in files"),
    ({"repo": {"commits": [{"branch": "main", "message": "x", "files": {".git/config": "main.json"}}]}}, None,
     "isn't a path this lab writes"),
    ({"validation": [{"kind": "result_matches"}]}, None, "git_lab can't check result_matches"),
    ({"uses": "LAB-04/rebuild"}, None, "only for duckdb_lab exercises"),
])
def test_a_git_exercise_that_cant_be_graded_is_caught(git_pack, change, remove, message):
    path = git_pack / PACK / "exercises" / EID / "exercise.json"
    ex = json.loads(path.read_text(encoding="utf-8"))
    ex.update(change)
    path.write_text(json.dumps(ex), encoding="utf-8")
    if remove:
        (git_pack / PACK / remove).unlink()
    if change.get("starter") == "settings.json":
        (git_pack / PACK / "exercises" / EID / "settings.json").write_text("{}")
    found = exercises.problems(git_pack)
    assert any(message in p for p in found), found


def test_fewer_than_two_wrong_answers_is_caught(git_pack):
    for name in list(WRONG)[1:]:
        (git_pack / PACK / "grading" / EID / name).unlink()
    assert any("at least two planted wrong answers (grading/E36/wrong-*.git)" in p for p in exercises.problems(git_pack))


# ---- through the workspace and the academy_* methods

class _Svc(AcademyMixin):
    def __init__(self, root):
        self.root = root
        self.rows = []

    def _progress_record(self, row):
        self.rows.append(row)
        return []

    def _profile_path(self):
        return self.root / "profile.json"


@pytest.fixture
def svc(git_pack, tmp_path, monkeypatch):
    monkeypatch.setattr(exercises, "get", functools.partial(exercises.get, root=git_pack))
    monkeypatch.setattr(exercises, "file_text", functools.partial(exercises.file_text, root=git_pack))
    monkeypatch.setattr(lab, "_exercise_folder", lambda pid, eid: git_pack / pid / "exercises" / eid)
    data = tmp_path / "data"
    data.mkdir()
    return _Svc(data)


def test_start_save_run_check_and_reset(svc):
    st = svc.academy_start(PACK, EID)
    assert st["started"] and st["answer"] == STARTER
    folder = lab.workspace(svc.root, PACK, EID)
    assert sorted(p.name for p in folder.iterdir()) == ["session.git", "state.json"]
    shown = svc.academy_exercise(PACK, EID)
    assert shown["checks"] == ["repo_matches", "repo_matches"] and "rules" not in json.dumps(shown)

    got = svc.academy_run(PACK, EID, STARTER)
    assert got["rows"][0][1] == "git status" and "ms" in got
    assert sorted(p.name for p in folder.iterdir()) == ["session.git", "state.json"]   # run/ is removed

    res = svc.academy_validate(PACK, EID)
    assert not res["passed"] and not svc.rows
    assert res["verification"]["label"] == "LOCAL VERIFIED"
    assert res["results"][0]["message"] == "Merge feature/days into main and finish the merge."

    svc.academy_save(PACK, EID, SOLUTION)
    res = svc.academy_validate(PACK, EID)
    assert res["passed"], res
    assert svc.rows and svc.rows[0]["detail"]["class"] == "local_verified"

    svc.academy_reset(PACK, EID)
    assert lab.state(svc.root, PACK, EID)["answer"] == STARTER


def test_run_of_a_refused_session_says_why(svc):
    svc.academy_start(PACK, EID)
    with pytest.raises(ValueError, match="git push isn't part of this lab"):
        svc.academy_run(PACK, EID, "git push\n")


# ---- limits: each command in a Job Object (Windows) or its own limited session

def _plain_env() -> dict[str, str]:
    import os
    return {k: os.environ[k] for k in ("SYSTEMROOT", "SystemRoot", "PATH") if k in os.environ}


def test_every_git_command_runs_contained_with_the_labs_limits(folder, tmp_path, monkeypatch):
    seen = []
    real = gitlab.sandbox.contained

    def spy(argv, cwd, env, **limits):
        seen.append(limits)
        return real(argv, cwd, env, **limits)

    monkeypatch.setattr(gitlab.sandbox, "contained", spy)
    (tmp_path / "run").mkdir()
    gitlab.run(tmp_path / "run", folder, EX, SOLUTION)
    assert seen and all(s["memory_limit"] == gitlab.MEMORY_LIMIT and s["cpu_limit"] == gitlab.CPU_SECONDS
                        and s["processes"] == gitlab.PROCESSES and s["timeout"] <= gitlab.STEP_SECONDS for s in seen)


@pytest.mark.parametrize(("result", "code", "said"), [
    ({"timed_out": True, "memory_hit": False}, 124, "took longer than 15 seconds"),
    ({"timed_out": False, "memory_hit": True}, 125, "used more than 512 MB of memory"),
])
def test_a_command_stopped_at_a_limit_says_which(tmp_path, monkeypatch, result, code, said):
    monkeypatch.setattr(gitlab.sandbox, "contained",
                        lambda *a, **k: {"exit_code": 1, "stdout": b"", "stderr": b"", **result})
    (tmp_path / "repo").mkdir()
    got = gitlab._git(tmp_path / "repo", ["status"])
    assert got[0] == code and said in got[1]


def test_a_command_that_runs_out_of_time_takes_what_it_started_with_it(tmp_path):
    """The command's own child outlives it unless the job or group ends it: it would write ``late``."""
    import sys
    import time
    child = "import time; time.sleep(3); open('late', 'w').close()"
    parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(60)"
    began = time.monotonic()
    out = gitlab.sandbox.contained([sys.executable, "-c", parent], tmp_path, _plain_env(), timeout=1.5,
                                   memory_limit=gitlab.MEMORY_LIMIT, cpu_limit=30, processes=4)
    assert out["timed_out"] and time.monotonic() - began < 20
    time.sleep(4)
    assert not (tmp_path / "late").exists()


def test_a_command_over_its_memory_limit_is_stopped(tmp_path):
    import sys
    code = "b = bytearray(700 * 1024 * 1024); print('done')"
    out = gitlab.sandbox.contained([sys.executable, "-c", code], tmp_path, _plain_env(), timeout=60,
                                   memory_limit=256 * 1024 ** 2, cpu_limit=30, processes=4)
    assert out["exit_code"] != 0 and b"done" not in out["stdout"]


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows only")
def test_windows_job_holds_the_process_limit_and_refuses_breakaway(tmp_path):
    code = ("import subprocess, sys\n"
            "try:\n    subprocess.run([sys.executable, '-c', 'pass'], creationflags=0x01000000)\n"   # BREAKAWAY_FROM_JOB
            "except OSError:\n    print('no breakaway')\n"
            "try:\n    subprocess.run([sys.executable, '-c', 'pass'])\n"
            "except OSError:\n    print('no child')\n")
    out = gitlab.sandbox.contained([sys.executable, "-I", "-c", code], tmp_path, _plain_env(), timeout=60,
                                   memory_limit=gitlab.MEMORY_LIMIT, cpu_limit=30, processes=1)
    assert b"no breakaway" in out["stdout"] and b"no child" in out["stdout"]
