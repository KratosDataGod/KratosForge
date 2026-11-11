"""ci.yml's ui-browser job installs Chromium's system packages without failing on a short outage of
Ubuntu's apt mirrors, which the runner sometimes can't resolve for a few minutes. That failed the job
before any test ran, on main and on pull requests alike."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _step(name: str) -> str:
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    steps = [s for s in jobs["ui-browser"]["steps"] if s.get("name") == name]
    assert len(steps) == 1, name
    return steps[0]["run"]


def test_apt_runs_only_for_missing_packages_and_rides_out_a_short_outage():
    run = _step("Install Chromium's system packages")
    # It asks Playwright which packages Chromium needs and skips apt when every one is installed.
    assert "install-deps --dry-run chromium" in run and "dpkg-query" in run
    # Newer Playwright exits 1 from the dry run when packages are missing; under bash -e that must not end the step.
    assert '$("$pw" install-deps --dry-run chromium || true)' in run
    assert run.index("dpkg-query") < run.index("Acquire::Retries")
    # Playwright 1.63 and later print only this line when nothing is missing: that is a pass, not an error.
    assert "All system dependencies are installed" in run
    assert run.index("All system dependencies are installed") < run.index("Acquire::Retries")
    # apt retries each download itself, with a timeout, and the whole install is tried three times.
    assert 'Acquire::Retries "5"' in run and 'Acquire::http::Timeout "30"' in run
    assert "for wait in 60 120 0; do" in run
    # apt runs only once the mirrors resolve, so a DNS outage costs seconds, not apt's retry budget.
    assert run.count('if resolve && "$pw" install-deps chromium; then exit 0; fi') == 2
    assert "getent hosts" in run
    # The last failure says it was the network, so nobody reads it as a test failure.
    assert "not a test" in run


def test_missing_fonts_alone_never_fail_the_job():
    run = _step("Install Chromium's system packages")
    # Playwright 1.63 and later list missing packages one to a line; those are read too.
    assert "/Missing system dependencies/" in run
    # The browser tests compare no pixels, so fonts are optional: if only fonts are missing and apt
    # can't get them, the step warns and the tests go on.
    fonts = run.index("grep -qvE '^x?fonts-'")
    assert fonts < run.index("for wait in 60 120 0; do")
    assert "::warning::Only fonts were missing" in run[fonts : run.index("for wait")]


def test_the_step_cannot_hang_the_job():
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    steps = [s for s in jobs["ui-browser"]["steps"] if s.get("name") == "Install Chromium's system packages"]
    assert steps[0].get("timeout-minutes") == 30
