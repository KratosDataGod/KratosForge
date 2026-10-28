"""Tests for the advisories the CI audit job lets through (.github/scripts/audit_exceptions.py)."""

import datetime as dt
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("audit_exceptions", ROOT / ".github" / "scripts" / "audit_exceptions.py")
audit_exceptions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_exceptions)

TODAY = dt.date(2026, 10, 28)


def test_exceptions_in_force_become_each_tools_flags():
    ids, problems = audit_exceptions.parse(
        "# comment\n\nGHSA-abcd-1234-wxyz  until=2026-11-30  not reachable; moves in #900\n"
        "RUSTSEC-2026-0001 until=2026-10-28 no fixed release yet\nPYSEC-2026-12  until=2027-01-01  docs only\n", TODAY)
    assert problems == [] and ids == ["GHSA-abcd-1234-wxyz", "RUSTSEC-2026-0001", "PYSEC-2026-12"]
    assert audit_exceptions.flags(ids, "pip") == ["--ignore-vuln", "GHSA-abcd-1234-wxyz", "--ignore-vuln", "PYSEC-2026-12"]
    assert audit_exceptions.flags(ids, "cargo") == ["--ignore", "RUSTSEC-2026-0001"]


def test_a_lapsed_or_unexplained_exception_fails():
    ids, problems = audit_exceptions.parse(
        "GHSA-abcd-1234-wxyz  until=2026-10-27  lapsed yesterday\nRUSTSEC-2026-0001  until=2026-12-01\n"
        "CVE-2026-1  until=2026-13-01  bad month\nGHSA-x; rm -rf /  until=2026-12-01  injected\n", TODAY)
    assert ids == []
    assert [p.split(":")[0] for p in problems] == ["line 1", "line 2", "line 3", "line 4"]
    assert "lapsed on 2026-10-27" in problems[0]


def test_the_checked_in_file_is_valid_and_the_script_runs():
    assert audit_exceptions.parse(audit_exceptions.FILE.read_text(encoding="utf-8"), dt.date.today())[1] == []
    for tool in ("pip", "cargo"):
        out = subprocess.run([sys.executable, str(ROOT / ".github" / "scripts" / "audit_exceptions.py"), tool],
                             capture_output=True, text=True, check=True)
        assert out.stdout.strip() == ""  # no exceptions recorded today
    bad = subprocess.run([sys.executable, str(ROOT / ".github" / "scripts" / "audit_exceptions.py"), "npm"],
                         capture_output=True, text=True)
    assert bad.returncode == 2
