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
permissions:
  contents: read
jobs:
  a:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
      - run: echo hi
  b:
    uses: ./.github/workflows/other.yml
"""
CHECKOUT = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4"


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
        ("w.yml: job a runs on ubuntu-latest, which GitHub moves to new images on its own; "
        "name the image (for example ubuntu-24.04 or windows-2025-vs2026)")]
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


def test_an_action_or_reusable_workflow_named_by_a_tag_or_branch_is_refused(tmp_path):
    tag = GOOD.replace(CHECKOUT, "actions/checkout@v4")
    assert check.problems(_write(tmp_path, tag)) == [
        "w.yml: job a, step 1: actions/checkout@v4 isn't pinned to a commit (owner/repo@<40 hex>)"]
    short = GOOD.replace(CHECKOUT, "actions/checkout@11d5960")
    assert "isn't pinned" in check.problems(_write(tmp_path, short))[0]
    sub = GOOD.replace(CHECKOUT, "github/codeql-action/init@" + "a" * 40)
    assert check.problems(_write(tmp_path, sub)) == [], "an action in a subfolder pinned to a commit is fine"
    docker = GOOD.replace(CHECKOUT, "docker://alpine:3.20")
    assert "docker://alpine:3.20 isn't pinned" in check.problems(_write(tmp_path, docker))[0]
    digest = GOOD.replace(CHECKOUT, "docker://alpine@sha256:" + "b" * 64)
    assert check.problems(_write(tmp_path, digest)) == []
    reusable = GOOD.replace("uses: ./.github/workflows/other.yml", "uses: someone/repo/.github/workflows/w.yml@main")
    assert check.problems(_write(tmp_path, reusable)) == [
        "w.yml: job b uses someone/repo/.github/workflows/w.yml@main, which isn't pinned to a commit"]


def test_every_job_has_a_scoped_token(tmp_path):
    bare = GOOD.replace("permissions:\n  contents: read\n", "")
    got = check.problems(_write(tmp_path, bare))
    assert got == [f"w.yml: job {j} has no permissions; give it (or the workflow) the token rights it needs, "
                   "for example contents: read" for j in ("a", "b")]
    per_job = bare.replace("    runs-on: ubuntu-24.04\n", "    runs-on: ubuntu-24.04\n    permissions:\n      contents: read\n")
    per_job = per_job.replace("    uses: ./.github", "    permissions: {}\n    uses: ./.github")
    assert check.problems(_write(tmp_path, per_job)) == []


def test_event_text_and_inputs_reach_a_script_only_through_env(tmp_path):
    for expr in ("${{ github.event.pull_request.title }}", "${{github.head_ref}}", "${{ inputs.notes }}",
                 "${{ format('{0}', github.event.issue.title) }}", "${{ github.event['issue']['title'] }}",
                 "${{ github['event'].comment.body }}", "${{ inputs['notes'] }}"):
        bad = GOOD.replace("run: echo hi", f'run: echo "{expr}"')
        assert check.problems(_write(tmp_path, bad)) == [f"w.yml: job a, step 2: {expr} is pasted into its run; pass it through env"]
    script = GOOD.replace("      - run: echo hi\n", "      - uses: actions/github-script@" + "c" * 40 + "\n"
                          "        with:\n          script: console.log('${{ github.event.comment.body }}')\n")
    assert "is pasted into its script" in check.problems(_write(tmp_path, script))[0]
    env = GOOD.replace("      - run: echo hi\n", "      - env:\n          TITLE: ${{ github.event.pull_request.title }}\n"
                       '        run: echo "$TITLE" ${{ github.sha }} ${{ steps.x.outputs.y }}\n')
    assert check.problems(_write(tmp_path, env)) == []
    name = GOOD.replace("run: echo hi", 'run: echo "${{ github.event_name }} ${{ github.repository }}"')
    assert check.problems(_write(tmp_path, name)) == [], "github.event_name and the repository's own names are fine"


def test_a_pull_request_target_workflow_checks_out_only_the_base(tmp_path):
    target = GOOD.replace("  push:\n", "  pull_request_target:\n")
    assert check.problems(_write(tmp_path, target)) == [], "no ref checks out the base"
    head = target.replace(CHECKOUT, CHECKOUT + "\n        with:\n          ref: ${{ github.event.pull_request.head.sha }}")
    assert check.problems(_write(tmp_path, head)) == [
        ("w.yml: job a, step 1: a pull_request_target workflow checks out ${{ github.event.pull_request.head.sha }}; "
         "check out only the base (github.sha)")]
    base = target.replace(CHECKOUT, CHECKOUT + "\n        with:\n          ref: ${{ github.sha }}")
    assert check.problems(_write(tmp_path, base)) == []
    fork = target.replace(CHECKOUT, CHECKOUT + "\n        with:\n          repository: ${{ github.event.pull_request.head.repo.full_name }}")
    assert check.problems(_write(tmp_path, fork)) == [
        ("w.yml: job a, step 1: a pull_request_target workflow checks out ${{ github.event.pull_request.head.repo.full_name }}; "
         "check out only this repository's base")]
    own = target.replace(CHECKOUT, CHECKOUT + "\n        with:\n          repository: ${{ github.repository }}")
    assert check.problems(_write(tmp_path, own)) == []
    push = head.replace("  pull_request_target:\n", "  push:\n")
    assert check.problems(_write(tmp_path, push)) == [], "the rule is only for pull_request_target"


def test_the_repositorys_workflows_name_every_runner_image():
    assert [p for f in sorted((ROOT / ".github" / "workflows").glob("*.yml")) for p in check.problems(f)] == []


def test_an_empty_folder_fails(tmp_path):
    assert check.main(["x", str(tmp_path)]) == 1
