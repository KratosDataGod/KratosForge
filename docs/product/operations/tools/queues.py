"""Work queues for the six workstreams (docs/product/operations/workstreams.json).

    python docs/product/operations/tools/queues.py check    # the queues are complete and name real stories
    python docs/product/operations/tools/queues.py render   # rewrite the generated table in README.md
    python docs/product/operations/tools/queues.py all      # render, then check
    python docs/product/operations/tools/queues.py body PB-072   # a story's GitHub issue body
    python docs/product/operations/tools/queues.py report   # the weekly report (reads GitHub; needs GITHUB_TOKEN)

Standard library only, like docs/product/tools/product_data.py.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
OPS = ROOT / "docs" / "product" / "operations"
QUEUES = OPS / "workstreams.json"
README = OPS / "README.md"
BACKLOG = ROOT / "docs" / "product" / "PRODUCT_BACKLOG.json"
INVENTORY = ROOT / "docs" / "product" / "FEATURE_INVENTORY.json"
BEGIN, END = "<!-- BEGIN GENERATED: queues -->", "<!-- END GENERATED: queues -->"
REPO = os.environ.get("GITHUB_REPOSITORY", "KratosDataGod/KratosForge")


def load() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    queues = json.loads(QUEUES.read_text(encoding="utf-8"))
    backlog = {i["id"]: i for i in json.loads(BACKLOG.read_text(encoding="utf-8"))["items"]}
    return queues, backlog


def story(sid: str, queues: dict[str, Any], backlog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One story's fields, from the backlog item or the queue file's new_stories."""
    if sid in backlog:
        b = backlog[sid]
        return {"id": sid, "title": b["title"], "priority": (b.get("computed") or {}).get("priority", "P2"),
                "problem": b.get("problem", ""), "capability": b.get("capability", ""),
                "acceptance": b.get("acceptance", []), "depends_on": b.get("depends_on", []),
                "effort": b.get("effort", ""), "state": b.get("state", "")}
    if sid in queues.get("new_stories", {}):
        n = queues["new_stories"][sid]
        return {"id": sid, "depends_on": [], "state": "Proposed", **n}
    raise KeyError(sid)


def slots(ws: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out = [("active", ws["active"])] if ws.get("active") else []
    return out + [("ready", r) for r in ws.get("ready", [])]


def check() -> list[str]:
    queues, backlog = load()
    rules, problems, seen = queues["rules"], [], {}
    if len(queues["workstreams"]) != 6:
        problems.append(f"expected 6 workstreams, found {len(queues['workstreams'])}")
    for ws in queues["workstreams"]:
        key = ws["key"]
        for field in ("name", "scope", "lead", "next_action"):
            if not ws.get(field):
                problems.append(f"{key}: no {field}")
        if len(ws.get("ready", [])) < rules["ready_minimum"]:
            problems.append(f"{key}: {len(ws.get('ready', []))} ready stories, needs {rules['ready_minimum']}")
        for kind, slot in slots(ws):
            sid = slot["story"]
            try:
                s = story(sid, queues, backlog)
            except KeyError:
                problems.append(f"{key}: {kind} story {sid} is in neither the backlog nor new_stories")
                continue
            if s["state"] == "Accepted":
                problems.append(f"{key}: {sid} is Accepted; take it out of the queue")
            if sid in seen:
                problems.append(f"{sid} is queued in both {seen[sid]} and {key}")
            seen[sid] = key
        for sid in ws.get("backlog", []):
            if sid not in backlog and sid not in queues.get("new_stories", {}):
                problems.append(f"{key}: backlog story {sid} doesn't exist")
    text = README.read_text(encoding="utf-8")
    if BEGIN in text and text != _rendered(text, queues, backlog):
        problems.append("README.md's queue table is out of date: run queues.py render")
    return problems


def _issue_link(slot: dict[str, Any]) -> str:
    return f"[#{slot['issue']}](https://github.com/{REPO}/issues/{slot['issue']})" if slot.get("issue") else "no issue yet"


def table(queues: dict[str, Any], backlog: dict[str, dict[str, Any]]) -> str:
    rows = ["| Workstream | Lead thread | Active | Ready next | Blockers | Next action |",
            "| --- | --- | --- | --- | --- | --- |"]
    for ws in queues["workstreams"]:
        def cell(slot: dict[str, Any]) -> str:
            s = story(slot["story"], queues, backlog)
            return f"{s['id']} {s['title']} ({_issue_link(slot)})"
        active = cell(ws["active"]) if ws.get("active") else "none"
        ready = "<br>".join(cell(r) for r in ws.get("ready", []))
        blockers = "<br>".join(ws.get("blockers", [])) or "none"
        rows.append(f"| {ws['name']} | {ws['lead']} | {active} | {ready} | {blockers} | {ws['next_action']} |")
    return "\n".join(rows)


def _rendered(text: str, queues: dict[str, Any], backlog: dict[str, dict[str, Any]]) -> str:
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n{table(queues, backlog)}\n{END}{tail}"


def render() -> None:
    queues, backlog = load()
    README.write_text(_rendered(README.read_text(encoding="utf-8"), queues, backlog), encoding="utf-8")


def body(sid: str) -> str:
    """The GitHub issue body for a queued story. Never use it for security findings: issues are public."""
    queues, backlog = load()
    s = story(sid, queues, backlog)
    ws = next((w for w in queues["workstreams"] if any(sl["story"] == sid for _, sl in slots(w))
               or sid in w.get("backlog", [])), None)
    lines = [f"**Story:** {sid}" + (" (docs/product/PRODUCT_BACKLOG.json)" if sid in backlog else " (new; the product check adds a PB id)"),
             f"**Workstream:** {ws['name'] if ws else 'unassigned'}",
             f"**Target platform:** {s.get('pillar') or (ws or {}).get('pillar', 'shared')}",
             f"**Priority:** {s['priority']}  ·  **Effort:** {s.get('effort') or 'unknown'}",
             f"**Assigned agent:** {ws['lead'] if ws else 'unassigned'} thread",
             "",
             "## Business value", s["problem"], "",
             "## Technical requirements", s["capability"],
             "",
             *([f"**Orchestrator note:** {note}", ""] if (note := next((sl.get("note") for w in queues["workstreams"]
                                                                      for _, sl in slots(w) if sl["story"] == sid), None)) else []),
             "First, check main for what is already built: several backlog items were partly done by later PRs. "
             "Record in this issue which acceptance criteria already hold, and build only the rest.", "",
             "## Acceptance criteria", *[f"- [ ] {a}" for a in s["acceptance"]], "",
             "## Dependencies", *([f"- {d}" for d in s["depends_on"]] or ["- none"]), "",
             "## Tests required",
             "Tests that fail without the change and cover each criterion, plus the checks for the files touched "
             "(engine: `test_silent_except`, `py_compile`; UI: `ui.test.cjs`; docs: `zensical build`, `product_data.py check`).", "",
             "## Definition of done",
             f"Quality PASS on the head, merged by Release management, docs and inventory updated, handoff note on the PR "
             f"([gates](https://github.com/{REPO}/blob/main/docs/product/operations/README.md#handoffs-and-quality-gates)). "
             "Merged moves the item to In review; only Chris accepts. No capacity spend or new user-facing feature without his typed go."]
    return "\n".join(lines)


def _get(path: str, params: dict[str, Any]) -> Any:
    url = f"https://api.github.com/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def fetch(today: dt.date) -> dict[str, Any]:
    since = (today - dt.timedelta(days=7)).isoformat()
    merged = _get("search/issues", {"q": f"repo:{REPO} is:pr is:merged merged:>={since}", "per_page": 100})
    opened = _get("search/issues", {"q": f"repo:{REPO} is:pr created:>={since}", "per_page": 1})
    open_prs = _get(f"repos/{REPO}/pulls", {"state": "open", "per_page": 100})
    stories = _get(f"repos/{REPO}/issues", {"state": "open", "labels": "story", "per_page": 100})
    runs = _get(f"repos/{REPO}/actions/workflows/ci.yml/runs", {"branch": "main", "per_page": 20})
    return {"since": since,
            "merged": {"total": merged["total_count"], "titles": [f"#{i['number']} {i['title']}" for i in merged["items"]]},
            "opened": opened["total_count"],
            "open_prs": [{"number": p["number"], "title": p["title"], "draft": p["draft"]} for p in open_prs],
            "stories": [{"number": i["number"], "title": i["title"],
                         "labels": [lab["name"] for lab in i["labels"]]} for i in stories if "pull_request" not in i],
            "ci": [r["conclusion"] or r["status"] for r in runs["workflow_runs"]]}


def report(gh: dict[str, Any], today: dt.date) -> str:
    queues, backlog = load()
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))["features"]
    verified = sum(1 for f in inv if f.get("verification") == "Verified")
    states: dict[str, int] = {}
    for b in backlog.values():
        states[b["state"]] = states.get(b["state"], 0) + 1
    by_status: dict[str, list[str]] = {}
    for s in gh["stories"]:
        status = next((lab.split(":", 1)[1] for lab in s["labels"] if lab.startswith("status:")), "untriaged")
        by_status.setdefault(status, []).append(f"#{s['number']} {s['title']}")
    ci = gh["ci"]
    green = sum(1 for c in ci if c == "success")
    lines = [f"# Weekly review: week ending {today.isoformat()}", "",
             "Generated by the Weekly review workflow from GitHub and the repo's product data. "
             "The orchestrator thread adds recommendations, the roadmap update and decisions for Chris as a comment.", "",
             "## Delivery", f"- PRs merged since {gh['since']}: **{gh['merged']['total']}**; PRs opened: {gh['opened']}",
             f"- Main CI, last {len(ci)} runs: {green} green, {len(ci) - green} not green",
             f"- Open PRs: {len(gh['open_prs'])} ({sum(1 for p in gh['open_prs'] if p['draft'])} drafts)", "",
             "## Product", f"- Features: {len(inv)}; verified end to end: {verified}",
             "- Backlog states: " + ", ".join(f"{k} {v}" for k, v in sorted(states.items())), "",
             "## Story issues by status"]
    for status in ("active", "ready", "blocked", "review", "untriaged"):
        items = by_status.get(status, [])
        lines.append(f"- **{status}** ({len(items)})" + ("".join(f"\n  - {t}" for t in items)))
    lines += ["", "## Work queues", table(queues, backlog), "",
              "## Token utilization",
              "Not measurable here: neither GitHub nor the Claude threads can read the subscription allocation, "
              "so no percentage is reported. Proxies: open story issues per status above, and workstreams "
              "with fewer than two ready stories (queues.py check)."]
    problems = check()
    lines += ["", "## Queue check", *(f"- {p}" for p in problems)] if problems else ["", "## Queue check", "- Every workstream has an active story and two ready ones."]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd in ("render", "all"):
        render()
    if cmd in ("check", "all"):
        problems = check()
        for p in problems:
            print(p)
        if problems:
            return 1
        print("Work queues are consistent.")
        return 0
    if cmd == "body":
        print(body(argv[2]))
        return 0
    if cmd == "report":
        today = dt.date.today()
        gh = json.loads(Path(argv[2]).read_text(encoding="utf-8")) if len(argv) > 2 else fetch(today)
        print(report(gh, today), end="")
        return 0
    if cmd != "render":
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
