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


def test_a_failed_audit_names_each_advisory_and_the_line_that_would_let_it_through():
    pip = {"dependencies": [{"name": "requests", "version": "2.25.0", "vulns": [
        {"id": "PYSEC-2023-74", "fix_versions": ["2.31.0"]}, {"id": "PYSEC-2023-74", "fix_versions": ["2.31.0"]},
        {"id": "GHSA-abcd-1234-wxyz", "fix_versions": []}]}, {"name": "ok", "version": "1", "vulns": []}]}
    lines = audit_exceptions.hints(pip, "pip", TODAY)
    assert len(lines) == 2  # one line per advisory, though pip-audit lists one twice
    assert lines[0].startswith("PYSEC-2023-74 in requests 2.25.0 (fixed in 2.31.0).")
    assert lines[0].endswith(".github/audit-exceptions.txt: PYSEC-2023-74  until=2026-11-27  <why it is accepted>")
    assert "(fixed in no fixed version yet)" in lines[1]
    cargo = {"vulnerabilities": {"found": True, "count": 1, "list": [
        {"advisory": {"id": "RUSTSEC-2020-0071", "title": "t"}, "package": {"name": "time", "version": "0.2.7"},
         "versions": {"patched": [">=0.2.23"]}}]}}
    [line] = audit_exceptions.hints(cargo, "cargo", TODAY)
    assert line.startswith("RUSTSEC-2020-0071 in time 0.2.7 (fixed in >=0.2.23).")
    # The suggested line, pasted with its placeholder, is refused until someone says why.
    pasted = line.rsplit(": ", 1)[1]
    assert audit_exceptions.parse(pasted, TODAY)[1] == ["line 1: say why RUSTSEC-2020-0071 is accepted instead of "
                                                       "'<why it is accepted>'"]
    assert audit_exceptions.parse(pasted.replace("<why it is accepted>", "not reachable"), TODAY) == (["RUSTSEC-2020-0071"], [])


def test_hint_with_no_report_says_so_and_leaves_the_audits_own_exit():
    out = subprocess.run([sys.executable, str(ROOT / ".github" / "scripts" / "audit_exceptions.py"), "hint", "pip",
                          str(ROOT / "no-such-report.json")], capture_output=True, text=True)
    assert out.returncode == 0 and "No pip audit report to read" in out.stderr


def test_an_exception_can_run_at_most_ninety_days():
    ids, problems = audit_exceptions.parse("GHSA-abcd-1234-wxyz  until=2099-01-01  forever\n"
                                           "PYSEC-2026-12  until=2027-01-27  one day too far\n", TODAY)
    assert ids == [] and len(problems) == 2
    assert "more than 90 days away; pick a date by 2027-01-26" in problems[1]


def test_advisory_text_cant_start_a_workflow_command_and_an_unknown_report_is_shown(tmp_path):
    report = {"dependencies": [{"name": "x", "version": "1%", "vulns": [
        {"id": "GHSA-abcd-1234-wxyz", "fix_versions": ["2\n::add-mask::secret\r"]}]}]}
    [line] = audit_exceptions.hints(report, "pip", TODAY)
    escaped = audit_exceptions.annotation(line)
    assert "\n" not in escaped and "\r" not in escaped and "1%25" in escaped and "2%0A::add-mask::secret%0D" in escaped
    path = tmp_path / "report.json"
    path.write_text('{"results": [{"advisory": "ABC-1"}]}', encoding="utf-8")
    out = subprocess.run([sys.executable, str(ROOT / ".github" / "scripts" / "audit_exceptions.py"), "hint", "pip",
                          str(path)], capture_output=True, text=True, check=True)
    assert '"ABC-1"' in out.stdout and "::error" not in out.stdout
