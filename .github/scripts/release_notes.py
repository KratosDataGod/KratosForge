"""Draft release notes: every change merged to main between two refs, grouped by area.

    python .github/scripts/release_notes.py <from> [<to>] --out notes.md

For example, from the last Stable build to a candidate: ``release_notes.py 6b631e29 HEAD --out notes.md``.
It reads only the local git history and writes only the --out file: nothing is published or posted,
and a person edits the draft before it goes anywhere.

Each change goes under the area its title names ("Docs: ...", "CI: ...") or, failing that, the
area most of its files are in. PR titles are already written neutrally; as a second check, a title
matching any ``--neutral`` pattern (a regular expression the person running the script supplies) is
written as plain "Security hardening".
"""
import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("whats_new", Path(__file__).with_name("whats_new.py"))
whats_new = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(whats_new)
whats_new.MAX_ITEMS = sys.maxsize  # What's new keeps 200; release notes list the whole range

# In the order the notes list them.
AREAS = ("App", "Engine", "VS Code extension", "CI and release", "Docs", "Tests")
# Title prefixes that name the area outright.
PREFIXES = {"docs": "Docs", "ci": "CI and release", "release": "CI and release", "vs code": "VS Code extension",
            "vs code extension": "VS Code extension", "lint": "CI and release"}
NEUTRAL = "Security hardening"


def area_of(path: str) -> str:
    if path.startswith(("sidecar/tests/", "app/tests/")) or "/tests/" in path:
        return "Tests"
    if path.startswith("sidecar/"):
        return "Engine"
    if path.startswith("app/"):
        return "App"
    if path.startswith("vscode/"):
        return "VS Code extension"
    if path.startswith((".github/", "deploy/")):
        return "CI and release"
    if path.startswith("docs/") or path.endswith(".md"):
        return "Docs"
    return "Engine"


def classify(title: str, files: list[str]) -> str:
    """The area a change goes under: its title's prefix, else where most of its non-test files are."""
    prefix = title.split(":", 1)[0].strip().lower() if ":" in title else ""
    if prefix in PREFIXES:
        return PREFIXES[prefix]
    counts: dict[str, int] = {}
    for f in files:
        counts[area_of(f)] = counts.get(area_of(f), 0) + 1
    if not counts:
        return "Engine"
    if set(counts) == {"Tests"}:
        return "Tests"
    counts.pop("Tests", None)
    return max(AREAS, key=lambda a: (counts.get(a, 0), -AREAS.index(a)))


def neutral(title: str, patterns: list[re.Pattern[str]]) -> str:
    return NEUTRAL if any(p.search(title) for p in patterns) else title


def render(base: str, head: str, items: list[dict]) -> str:
    """Markdown for ``items`` (dicts with title, pr, area), oldest first within each area."""
    lines = [f"# Release notes draft: {base}..{head}", "",
             f"{len(items)} changes merged to main, drafted from their titles. Edit before publishing.", ""]
    for area in AREAS:
        group = [i for i in items if i["area"] == area]
        if not group:
            continue
        lines += [f"## {area}", ""]
        for i in reversed(group):  # git lists newest first
            lines.append(f"- {i['title']}" + (f" (#{i['pr']})" if i["pr"] else ""))
        lines.append("")
    return "\n".join(lines)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def collect(base: str, head: str, patterns: list[re.Pattern[str]] = []) -> list[dict]:  # noqa: B006 - never changed
    log = _git("log", "--first-parent", "--format=%H%x1f%cI%x1f%B%x1e", f"{base}..{head}")
    items = []
    for item in whats_new.parse(log) if log.strip() else []:
        files = _git("diff", "--name-only", f"{item['commit']}^1", item["commit"]).split()
        items.append({"title": neutral(item["title"], patterns), "pr": item["pr"], "area": classify(item["title"], files)})
    return items


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base", help="the last released commit, e.g. Stable's")
    ap.add_argument("head", nargs="?", default="HEAD")
    ap.add_argument("--out", required=True, type=Path, help="the Markdown file to write")
    ap.add_argument("--neutral", action="append", default=[], metavar="REGEX",
                    help="a title matching this is written as plain 'Security hardening' (repeatable)")
    args = ap.parse_args(argv)
    items = collect(args.base, args.head, [re.compile(p, re.IGNORECASE) for p in args.neutral])
    args.out.write_text(render(args.base, args.head, items), encoding="utf-8", newline="\n")
    print(f"Wrote {len(items)} changes to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
