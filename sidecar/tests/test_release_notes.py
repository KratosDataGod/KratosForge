"""Tests for the release notes draft (.github/scripts/release_notes.py)."""

import importlib.util
import os
import re
import subprocess
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "release_notes", Path(__file__).resolve().parents[2] / ".github" / "scripts" / "release_notes.py")
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)


def test_a_title_prefix_names_the_area_and_files_decide_the_rest():
    assert release_notes.classify("Docs: fix a link", ["sidecar/fabric_desktop/x.py"]) == "Docs"
    assert release_notes.classify("CI: pin a tool", []) == "CI and release"
    assert release_notes.classify("Faster joins", ["sidecar/fabric_desktop/sql.py", "sidecar/tests/test_sql.py",
                                                   "sidecar/tests/test_b.py"]) == "Engine"
    assert release_notes.classify("New button", ["app/ui/features.js", "app/tests/browser/a.test.cjs"]) == "App"
    assert release_notes.classify("Steadier test", ["sidecar/tests/test_agent.py"]) == "Tests"
    assert release_notes.classify("Pin actions", [".github/workflows/ci.yml"]) == "CI and release"


def test_a_title_matching_a_neutral_pattern_comes_out_neutral():
    patterns = [re.compile(r"\bZZ-\d+\b", re.IGNORECASE)]
    assert release_notes.neutral("Tighten the token check (ZZ-12)", patterns) == "Security hardening"
    assert release_notes.neutral("Tighten the token check (zz-12)", patterns) == "Security hardening"
    assert release_notes.neutral("Appends stop instead of converting values", patterns) == \
        "Appends stop instead of converting values"
    assert release_notes.neutral("Anything (ZZ-12)", []) == "Anything (ZZ-12)"


def test_the_draft_lists_the_range_by_area_oldest_first_and_writes_only_its_file(tmp_path):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout

    def commit(path, message):
        f = repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(message)
        git("add", "-A")
        git("commit", "-q", "-m", message)

    git("init", "-q", "-b", "main")
    commit("README.md", "Initial commit")
    base = git("rev-parse", "HEAD").strip()
    commit("sidecar/fabric_desktop/a.py", "Faster joins (#10)")
    commit("docs/a.md", "Docs: explain joins (#11)")
    commit("sidecar/fabric_desktop/b.py", "Harden the link server (ZZ-7) (#12)")
    commit("sidecar/fabric_desktop/c.py", "Steadier joins (#13)")

    out = tmp_path / "notes.md"
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        assert release_notes.main([base, "HEAD", "--out", str(out), "--neutral", r"\bZZ-\d+"]) == 0
    finally:
        os.chdir(cwd)
    text = out.read_text(encoding="utf-8")
    assert text.startswith(f"# Release notes draft: {base}..HEAD")
    assert "4 changes" in text
    engine = text.index("## Engine")
    docs = text.index("## Docs")
    assert engine < docs
    assert text.index("- Faster joins (#10)") < text.index("- Security hardening (#12)") < text.index("- Steadier joins (#13)") < docs
    assert "- Docs: explain joins (#11)" in text[docs:]
    assert "ZZ-7" not in text and "Initial commit" not in text
    assert git("status", "--porcelain") == ""  # nothing written into the repository
