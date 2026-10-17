"""What's new: every change that reached main in the last 30 days, newest first.

The release job adds this list to version.json as "history", and the app's What's new
dialog shows it. Promote to stable keeps it, so Stable shows the month behind its build.

    python .github/scripts/whats_new.py [commit] [--days 30]
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta

MAX_ITEMS = 200
PR_MERGE = re.compile(r"^Merge pull request #(\d+) from \S+")
SQUASHED = re.compile(r"^(.*\S)\s+\(#(\d+)\)$")


def parse(log: str) -> list:
    """Items from `git log --first-parent --format=%H%x1f%cI%x1f%B%x1e` output."""
    items = []
    for record in log.split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) != 3:
            continue
        commit, date, message = parts
        lines = [l.strip() for l in message.strip().splitlines()]
        subject = lines[0] if lines else ""
        pr, title = None, subject
        merge = PR_MERGE.match(subject)
        if merge:
            # A merge commit's body is the pull request's title.
            pr = int(merge.group(1))
            title = next((l for l in lines[1:] if l), subject)
        elif subject.startswith("Merge ") or subject == "Initial commit" or not subject:
            continue  # main merged into itself, or nothing to tell people
        else:
            squashed = SQUASHED.match(subject)
            if squashed:
                title, pr = squashed.group(1), int(squashed.group(2))
        items.append({"date": date[:10], "title": title, "pr": pr, "commit": commit})
    return items[:MAX_ITEMS]


def history(commit: str = "HEAD", days: int = 30) -> list:
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    log = subprocess.run(
        ["git", "log", "--first-parent", f"--since={since}", "--format=%H%x1f%cI%x1f%B%x1e", commit],
        check=True, capture_output=True, text=True).stdout
    return parse(log)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("commit", nargs="?", default="HEAD")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    json.dump(history(args.commit, args.days), sys.stdout)
