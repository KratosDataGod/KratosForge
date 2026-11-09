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
    runs-on: ubuntu-24.04
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
    bad = GOOD.replace("on:\n  push:\n", "").replace("    runs-on: ubuntu-24.04\n", "")
    bad = bad.replace("      - run: echo hi\n", "      - name: neither\n")
    got = check.problems(_write(tmp_path, bad))
    assert any("no `on` trigger" in p for p in got)
    assert any("has no runs-on" in p for p in got)
    assert any("step 2: needs exactly one of run or uses" in p for p in got)


def test_a_latest_runner_label_is_refused_directly_or_through_the_matrix(tmp_path):
    latest = GOOD.replace("runs-on: ubuntu-24.04", "runs-on: ubuntu-latest")
    assert check.problems(_write(tmp_path, latest)) == [
        "w.yml: job a runs on ubuntu-latest, which GitHub moves to new images on its own; "
        "name the image (for example ubuntu-24.04 or windows-2025-vs2026)"]
    matrix = GOOD.replace("    runs-on: ubuntu-24.04\n", "    name: a (${{ matrix.os }})\n"
                          "    strategy:\n      matrix:\n        os: [windows-2025-vs2026]\n"
                          "        include:\n          - os: macos-latest\n"
                          "    runs-on: ${{ matrix.os }}\n")
    got = check.problems(_write(tmp_path, matrix))
    assert len(got) == 1 and "runs on macos-latest" in got[0]
    named = GOOD.replace("    runs-on: ubuntu-24.04\n", "    strategy:\n      matrix:\n        include:\n"
                         "          - {os: windows-latest, image: windows-2025-vs2026}\n"
                         "          - {os: ubuntu-latest, image: ubuntu-24.04}\n"
                         "    runs-on: ${{ matrix.image }}\n")
    assert check.problems(_write(tmp_path, named)) == [], "a matrix key that only names the check is fine"
    unknown = GOOD.replace("runs-on: ubuntu-24.04", "runs-on: ${{ inputs.runner }}")
    assert check.problems(_write(tmp_path, unknown))[0].endswith("runs-on can't be checked; name the image or use ${{ matrix.<key> }}")
    labels = GOOD.replace("runs-on: ubuntu-24.04", "runs-on: [self-hosted, windows-latest]")
    assert "runs on windows-latest" in check.problems(_write(tmp_path, labels))[0]


def test_the_repositorys_workflows_name_every_runner_image():
    assert [p for f in sorted((ROOT / ".github" / "workflows").glob("*.yml")) for p in check.problems(f)] == []


def test_an_empty_folder_fails(tmp_path):
    assert check.main(["x", str(tmp_path)]) == 1
