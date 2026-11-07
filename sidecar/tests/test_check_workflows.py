"""The PR check that every workflow file reads as a GitHub Actions workflow (.github/scripts)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_workflows", ROOT / ".github" / "scripts" / "check_workflows.py")
check = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(check)  # type: ignore[union-attr]

GOOD = """name: x
on:
  push:
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo hi
  b:
    uses: ./.github/workflows/other.yml
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "w.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_good_workflow_has_no_problems(tmp_path):
    assert check.problems(_write(tmp_path, GOOD)) == []
    assert check.main(["x", str(tmp_path)]) == 0


def test_an_unquoted_colon_in_a_run_line_is_caught(tmp_path):
    # The shape of the docs.yml that merged broken: a ": " inside an unquoted run line.
    bad = GOOD.replace("run: echo hi", "run: pip install --only-binary=:all: --require-hashes -r r.txt")
    got = check.problems(_write(tmp_path, bad))
    assert len(got) == 1 and "not valid YAML" in got[0]
    assert check.main(["x", str(tmp_path)]) == 1


def test_missing_trigger_runner_and_step_kinds_are_caught(tmp_path):
    bad = GOOD.replace("on:\n  push:\n", "").replace("    runs-on: ubuntu-latest\n", "")
    bad = bad.replace("      - run: echo hi\n", "      - name: neither\n")
    got = check.problems(_write(tmp_path, bad))
    assert any("no `on` trigger" in p for p in got)
    assert any("has no runs-on" in p for p in got)
    assert any("step 2: needs exactly one of run or uses" in p for p in got)


def test_an_empty_folder_fails(tmp_path):
    assert check.main(["x", str(tmp_path)]) == 1
