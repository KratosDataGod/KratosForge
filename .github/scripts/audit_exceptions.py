"""The advisories the audit job lets through for now (.github/audit-exceptions.txt), as flags for
pip-audit or cargo audit. Each one names an advisory, the date it lapses and why it's accepted.
One that has lapsed, or is missing either, fails the job, so an exception is always a decision
someone wrote down and has to make again.

    python .github/scripts/audit_exceptions.py pip     # --ignore-vuln ID ... (PYSEC, GHSA, CVE)
    python .github/scripts/audit_exceptions.py cargo   # --ignore ID ...      (RUSTSEC)

After a failed audit, ``hint pip|cargo REPORT`` reads the tool's JSON report and names each
advisory with the line that would let it through, so nobody has to dig before deciding.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "audit-exceptions.txt"
# ID  until=YYYY-MM-DD  reason
LINE = re.compile(r"^(?P<id>[A-Z]+-[A-Za-z0-9-]+)\s+until=(?P<until>\d{4}-\d{2}-\d{2})\s+(?P<reason>\S.*)$")
FLAG = {"pip": "--ignore-vuln", "cargo": "--ignore"}
CARGO = re.compile(r"^RUSTSEC-")


def parse(text: str, today: dt.date) -> tuple[list[str], list[str]]:
    """The advisory ids in force, and what's wrong with the file (lapsed, malformed)."""
    ids, problems = [], []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = LINE.match(line)
        if not m:
            problems.append(f"line {n}: expected 'ID  until=YYYY-MM-DD  reason', got {line!r}")
            continue
        try:
            until = dt.date.fromisoformat(m["until"])
        except ValueError:
            problems.append(f"line {n}: {m['until']} isn't a date")
            continue
        if m["reason"].startswith("<"):
            problems.append(f"line {n}: say why {m['id']} is accepted instead of {m['reason']!r}")
            continue
        if until < today:
            problems.append(f"line {n}: the exception for {m['id']} lapsed on {until}; move the package or decide again")
            continue
        ids.append(m["id"])
    return ids, problems


def flags(ids: list[str], tool: str) -> list[str]:
    mine = [i for i in ids if bool(CARGO.match(i)) == (tool == "cargo")]
    return [x for i in mine for x in (FLAG[tool], i)]


def found(report: dict, tool: str) -> list[dict[str, str]]:
    """The advisories in pip-audit's or cargo audit's JSON report."""
    if tool == "pip":
        return [{"id": v.get("id", ""), "package": d.get("name", ""), "version": d.get("version", ""),
                 "fix": ", ".join(v.get("fix_versions") or []) or "no fixed version yet"}
                for d in report.get("dependencies") or [] for v in d.get("vulns") or []]
    return [{"id": (v.get("advisory") or {}).get("id", ""), "package": (v.get("package") or {}).get("name", ""),
             "version": (v.get("package") or {}).get("version", ""),
             "fix": ", ".join((v.get("versions") or {}).get("patched") or []) or "no fixed version yet"}
            for v in (report.get("vulnerabilities") or {}).get("list") or []]


def hints(report: dict, tool: str, today: dt.date) -> list[str]:
    until = today + dt.timedelta(days=30)
    return [f"{a['id']} in {a['package']} {a['version']} (fixed in {a['fix']}). Move the package in its own pull "
            f"request, or let it through for now by adding this line to .github/audit-exceptions.txt: "
            f"{a['id']}  until={until}  <why it is accepted>"
            for a in {a["id"]: a for a in found(report, tool)}.values()  # pip-audit can list one twice
            if LINE.match(f"{a['id']}  until={until}  x")]


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "hint" and argv[2] in FLAG:
        try:
            report = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # the tool failed before it wrote a report: its own output says why
            print(f"No {argv[2]} audit report to read ({exc.__class__.__name__}).", file=sys.stderr)
            return 0
        for line in hints(report if isinstance(report, dict) else {}, argv[2], dt.datetime.now(dt.timezone.utc).date()):
            print(f"::error file=.github/audit-exceptions.txt::{line}")
        return 0
    if len(argv) != 2 or argv[1] not in FLAG:
        print("usage: audit_exceptions.py pip|cargo | hint pip|cargo REPORT", file=sys.stderr)
        return 2
    text = FILE.read_text(encoding="utf-8") if FILE.exists() else ""
    ids, problems = parse(text, dt.datetime.now(dt.timezone.utc).date())
    for p in problems:
        print(f"::error file=.github/audit-exceptions.txt::{p}", file=sys.stderr)
    if problems:
        return 1
    print(" ".join(flags(ids, argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
