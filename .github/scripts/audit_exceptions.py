"""The advisories the audit job lets through for now (.github/audit-exceptions.txt), as flags for
pip-audit or cargo audit. Each one names an advisory, the date it lapses and why it's accepted.
One that has lapsed, or is missing either, fails the job, so an exception is always a decision
someone wrote down and has to make again.

    python .github/scripts/audit_exceptions.py pip     # --ignore-vuln ID ... (PYSEC, GHSA, CVE)
    python .github/scripts/audit_exceptions.py cargo   # --ignore ID ...      (RUSTSEC)
"""

from __future__ import annotations

import datetime as dt
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
        if until < today:
            problems.append(f"line {n}: the exception for {m['id']} lapsed on {until}; move the package or decide again")
            continue
        ids.append(m["id"])
    return ids, problems


def flags(ids: list[str], tool: str) -> list[str]:
    mine = [i for i in ids if bool(CARGO.match(i)) == (tool == "cargo")]
    return [x for i in mine for x in (FLAG[tool], i)]


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in FLAG:
        print("usage: audit_exceptions.py pip|cargo", file=sys.stderr)
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
