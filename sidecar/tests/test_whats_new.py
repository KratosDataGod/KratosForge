"""Tests for the What's new history CI adds to version.json (.github/scripts/whats_new.py)."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "whats_new", Path(__file__).resolve().parents[2] / ".github" / "scripts" / "whats_new.py")
whats_new = importlib.util.module_from_spec(spec)
spec.loader.exec_module(whats_new)

A, B, C, D, E = ("a" * 40, "b" * 40, "c" * 40, "d" * 40, "e" * 40)


def record(commit, date, message):
    return f"{commit}\x1f{date}\x1f{message}\x1e\n"


def test_parse_reads_merges_squashes_and_direct_pushes():
    log = (record(A, "2026-10-17T12:33:38-05:00", "Merge pull request #119 from KratosDataGod/claude/gov\n\nAdd the policy registry (GOV-001)\n")
           + record(B, "2026-10-16T10:00:00+00:00", "Merge branch 'main' into feature\n")
           + record(C, "2026-10-14T16:13:03-05:00", "Give Fabric its own page on the side rail (#22)\n")
           + record(D, "2026-10-13T09:00:00+00:00", "Fix a typo in the docs\n\nLonger body.\n")
           + record(E, "2026-10-09T16:08:53+00:00", "Initial commit\n"))
    assert whats_new.parse(log) == [
        {"date": "2026-10-17", "title": "Add the policy registry (GOV-001)", "pr": 119, "commit": A},
        {"date": "2026-10-14", "title": "Give Fabric its own page on the side rail", "pr": 22, "commit": C},
        {"date": "2026-10-13", "title": "Fix a typo in the docs", "pr": None, "commit": D}]


def test_a_merge_without_a_body_keeps_its_subject():
    log = record(A, "2026-10-17T00:00:00Z", "Merge pull request #5 from someone/branch\n")
    assert whats_new.parse(log)[0]["title"] == "Merge pull request #5 from someone/branch"


def test_history_lists_main_only_not_branch_commits(tmp_path):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True, env=env)
    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "Initial commit")
    git("checkout", "-q", "-b", "feature")
    git("commit", "-q", "--allow-empty", "-m", "Work in progress on the branch")
    git("checkout", "-q", "main")
    git("merge", "-q", "--no-ff", "feature", "-m", "Merge pull request #7 from me/feature", "-m", "Add the feature")
    out = subprocess.run([sys.executable, spec.origin], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    assert [(i["title"], i["pr"]) for i in json.loads(out)] == [("Add the feature", 7)]
