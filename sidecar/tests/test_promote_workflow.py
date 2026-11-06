"""Promote to stable waits for approval without holding the release-assets group (#2073).

The approve job is the only one in the stable environment and holds no concurrency group, so a
promote waiting for a decision never stops Preview or main's CI. The promote job runs only after
it and takes release-assets, so two publishes never write the release files at once. Dropping any
of these would either remove the approval or bring the stall back.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _jobs(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))["jobs"]


def test_the_approval_holds_no_concurrency_group():
    approve = _jobs("promote.yml")["approve"]
    assert approve["environment"] == "stable"
    assert "concurrency" not in approve


def test_stable_is_written_only_after_approval_and_one_publish_at_a_time():
    jobs = _jobs("promote.yml")
    promote = jobs["promote"]
    assert promote["needs"] in ("approve", ["approve"])
    assert promote["concurrency"] == {"group": "release-assets", "cancel-in-progress": False}
    assert "${{ needs.approve.outputs.build }}" in str(promote["steps"])
    # Only the promote job can write: approval alone changes nothing.
    writers = [name for name, job in jobs.items() if (job.get("permissions") or {}).get("contents") == "write"]
    assert writers == ["promote"]


def test_preview_publishes_in_the_same_group():
    release = _jobs("ci.yml")["release"]
    assert release["concurrency"] == {"group": "release-assets", "cancel-in-progress": False}
