"""Work queues for the six workstreams (docs/product/operations/workstreams.json).

    python docs/product/operations/tools/queues.py check    # the queue file is well formed, names real stories, and keeps to the WR id ledger
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
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[4]
OPS = ROOT / "docs" / "product" / "operations"
QUEUES = OPS / "workstreams.json"
LEDGER = OPS / "story-ids.json"
WR_ID = re.compile(r"WR-(\d+)")
README = OPS / "README.md"
BACKLOG = ROOT / "docs" / "product" / "PRODUCT_BACKLOG.json"
INVENTORY = ROOT / "docs" / "product" / "FEATURE_INVENTORY.json"
BEGIN, END = "<!-- BEGIN GENERATED: queues -->", "<!-- END GENERATED: queues -->"
SIDE_BEGIN, SIDE_END = "<!-- BEGIN GENERATED: side-programs -->", "<!-- END GENERATED: side-programs -->"
REPO = os.environ.get("GITHUB_REPOSITORY", "KratosDataGod/KratosForge")


def load() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    queues = json.loads(QUEUES.read_text(encoding="utf-8"))
    backlog = {i["id"]: i for i in json.loads(BACKLOG.read_text(encoding="utf-8"))["items"]}
    return queues, backlog


def load_ledger() -> dict[str, Any]:
    return json.loads(LEDGER.read_text(encoding="utf-8"))


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


def slot_story(slot: dict[str, Any], queues: dict[str, Any], backlog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A queue slot's story. A slot names a backlog or new story, or, for a story with no PB id yet, its issue and title."""
    if slot.get("story"):
        return story(slot["story"], queues, backlog)
    if not (slot.get("issue") and slot.get("title")):
        raise KeyError("a slot without a story needs an issue and a title")
    return {"id": f"#{slot['issue']}", "title": slot["title"], "state": "Proposed"}


def ci_summary(ci: list[str]) -> str:
    """Main CI results in words. Batch merging cancels superseded runs, so cancelled ones are counted apart from failures."""
    green = sum(1 for c in ci if c == "success")
    cancelled = sum(1 for c in ci if c == "cancelled")
    failed = sum(1 for c in ci if c in ("failure", "timed_out", "startup_failure", "action_required"))
    other = len(ci) - green - cancelled - failed
    text = f"{green} green, {failed} failed, {cancelled} cancelled"
    return text + (f", {other} running or other" if other else "")


def slots(ws: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out = [("active", ws["active"])] if ws.get("active") else []
    return out + [("ready", r) for r in ws.get("ready", [])]


def check() -> tuple[list[str], list[str]]:
    """Errors (the file is wrong; the Docs workflow fails) and shortfalls (a queue is short; the weekly review refills it)."""
    queues, backlog = load()
    rules, errors, short, seen = queues["rules"], [], [], {}
    if len(queues["workstreams"]) != 6:
        errors.append(f"expected 6 workstreams, found {len(queues['workstreams'])}")
    for ws in queues["workstreams"]:
        key = ws["key"]
        for field in ("name", "scope", "lead", "next_action"):
            if not ws.get(field):
                errors.append(f"{key}: no {field}")
        if not ws.get("active"):
            short.append(f"{key}: no active story")
        if len(ws.get("ready", [])) < rules["ready_minimum"]:
            short.append(f"{key}: {len(ws.get('ready', []))} ready stories, needs {rules['ready_minimum']}")
        for kind, slot in slots(ws):
            try:
                s = slot_story(slot, queues, backlog)
            except KeyError:
                errors.append(f"{key}: {kind} story {slot.get('story') or slot.get('issue')} is in neither the backlog "
                              "nor new_stories, and has no issue and title")
                continue
            sid = s["id"]
            if s["state"] == "Accepted":
                errors.append(f"{key}: {sid} is Accepted; take it out of the queue")
            if sid in seen:
                errors.append(f"{sid} is queued in both {seen[sid]} and {key}")
            seen[sid] = key
        for sid in ws.get("backlog", []):
            if sid not in backlog and sid not in queues.get("new_stories", {}):
                errors.append(f"{key}: backlog story {sid} doesn't exist")
    errors += _check_side(queues, seen)
    errors += duplicate_keys(QUEUES.read_text(encoding="utf-8"))
    errors += check_ids(queues, load_ledger())
    short += [f"side program {sp.get('key') or '?'}: {n} stories at review, over its cap of {sp['review_cap']}"
              for sp in queues.get("side_programs", [])
              if isinstance(sp.get("review_cap"), int)
              and (n := sum(1 for st in sp.get("stories", []) if st.get("status") == "review")) > sp["review_cap"]]
    text = README.read_text(encoding="utf-8")
    if not errors and (BEGIN in text or SIDE_BEGIN in text) and text != _rendered(text, queues, backlog):
        errors.append("README.md's queue table is out of date: run queues.py render")
    return errors, short


def _check_side(queues: dict[str, Any], seen: dict[str, str]) -> list[str]:
    """Side programs: a separate thread's stories under one workstream, with their own cap on PRs at review."""
    errors: list[str] = []
    keys = {ws["key"] for ws in queues["workstreams"]}
    statuses = queues["rules"].get("side_program_statuses", ["active", "ready", "blocked", "review"])
    for sp in queues.get("side_programs", []):
        key = sp.get("key") or "?"
        for field in ("key", "name", "thread"):
            if not sp.get(field):
                errors.append(f"side program {key}: no {field}")
        if sp.get("workstream") not in keys:
            errors.append(f"side program {key}: workstream {sp.get('workstream')} doesn't exist")
        cap = sp.get("review_cap")
        if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= 4:
            errors.append(f"side program {key}: review_cap must be 1 to 4")
        if not sp.get("stories"):
            errors.append(f"side program {key}: no stories")
        for st in sp.get("stories", []):
            if not (isinstance(st.get("issue"), int) and st.get("title")):
                errors.append(f"side program {key}: a story needs an issue number and a title")
                continue
            if st.get("status") not in statuses:
                errors.append(f"side program {key}: #{st['issue']} has status {st.get('status')}, not one of {', '.join(statuses)}")
            sid = f"#{st['issue']}"
            if sid in seen:
                errors.append(f"{sid} is queued in both {seen[sid]} and {key}")
            seen[sid] = key
    return errors


class _Use(NamedTuple):
    """One place the queue file uses a WR id. defines: the place carries the story itself (a title), not a reference."""
    sid: str
    number: int
    workstream: str | None
    place: str
    title: str
    issue: int | None
    defines: bool


def _wr_number(sid: Any) -> int | None:
    m = WR_ID.fullmatch(sid) if isinstance(sid, str) else None
    return int(m.group(1)) if m else None


def _titled_id(title: Any) -> str | None:
    """The WR id a free-text title starts with, as in "WR-250 Prod waits for the typed name"."""
    m = re.match(r"(WR-\d+)(?![\w-])", title) if isinstance(title, str) else None
    return m.group(1) if m else None


def _same_title(a: str, b: str) -> bool:
    def norm(t: str) -> str:
        return re.sub(r"^WR-\d+[\s:.-]*", "", t).strip().casefold()
    return norm(a) == norm(b)


def _id_uses(queues: dict[str, Any]) -> list[_Use]:
    uses = [_Use(sid, n, None, "new_stories", str(st.get("title") or ""), None, True)
            for sid, st in queues.get("new_stories", {}).items() if (n := _wr_number(sid)) is not None]
    for ws in queues["workstreams"]:
        key = ws["key"]
        for kind, slot in slots(ws):
            sid = slot.get("story")
            if sid:
                if (n := _wr_number(sid)) is not None:
                    uses.append(_Use(sid, n, key, f"{key} {kind} slot", "", None, False))
            elif (sid := _titled_id(slot.get("title"))) and (n := _wr_number(sid)) is not None:
                issue = slot.get("issue") if isinstance(slot.get("issue"), int) else None
                uses.append(_Use(sid, n, key, f"{key} {kind} slot #{slot.get('issue')}", slot["title"], issue, True))
        uses += [_Use(sid, n, key, f"{key} backlog", "", None, False)
                 for sid in ws.get("backlog", []) if (n := _wr_number(sid)) is not None]
    for sp in queues.get("side_programs", []):
        for st in sp.get("stories", []):
            if (sid := _titled_id(st.get("title"))) and (n := _wr_number(sid)) is not None:
                issue = st.get("issue") if isinstance(st.get("issue"), int) else None
                uses.append(_Use(sid, n, sp.get("workstream"), f"side program {sp.get('key') or '?'} #{st.get('issue')}",
                                 st["title"], issue, True))
    return uses


def _span(entry: Any) -> tuple[int, int] | None:
    """A ledger block or exception's first and last id numbers, or None when it isn't two WR ids in order."""
    if not isinstance(entry, dict):
        return None
    first, last = _wr_number(entry.get("first")), _wr_number(entry.get("last"))
    return (first, last) if first is not None and last is not None and first <= last else None


def _span_text(first: int, last: int) -> str:
    return f"WR-{first}" if first == last else f"WR-{first} to WR-{last}"


def duplicate_keys(text: str) -> list[str]:
    """A key twice in one object of workstreams.json: json.loads keeps the last, so the first story vanishes silently."""
    errors: list[str] = []

    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        for k, v in pairs:
            if k in seen:
                titles = [x.get("title") for x in (seen[k], v) if isinstance(x, dict)]
                both = f": {titles[0]!r} and {titles[1]!r}" if len(titles) == 2 and all(titles) else ""
                errors.append(f"workstreams.json defines {k} twice in one object{both}; the second hides the first")
            seen[k] = v
        return dict(pairs)

    json.loads(text, object_pairs_hook=hook)
    return errors


def check_ids(queues: dict[str, Any], ledger: dict[str, Any]) -> list[str]:
    """WR ids keep to the ledger (story-ids.json): a queued id is in a block of one of its workstream's lanes,
    and one id never names two stories, apart from the ledger's recorded exceptions."""
    errors: list[str] = []
    keys = {ws["key"] for ws in queues["workstreams"]}
    lanes: dict[str, dict[str, Any]] = {}
    blocks: list[tuple[int, int, dict[str, Any]]] = []
    for lane in ledger.get("lanes", []):
        lk = lane.get("key") or "?"
        for field in ("key", "name", "workstream"):
            if not lane.get(field):
                errors.append(f"story-ids.json: lane {lk}: no {field}")
        if lk in lanes:
            errors.append(f"story-ids.json: lane {lk} is listed twice")
        lanes[lk] = lane
        if lane.get("workstream") and lane["workstream"] not in keys:
            errors.append(f"story-ids.json: lane {lk}: workstream {lane['workstream']} doesn't exist")
        if not lane.get("blocks"):
            errors.append(f"story-ids.json: lane {lk}: no blocks")
        for b in lane.get("blocks") or []:
            if (r := _span(b)) is None:
                errors.append(f"story-ids.json: lane {lk}: a block needs a first and a last WR id, first not after last")
            else:
                blocks.append((*r, lane))
        if "next" in lane:
            n = _wr_number(lane["next"])
            own = [r for b in lane.get("blocks") or [] if (r := _span(b)) is not None]
            if n is None or not any(b0 <= n <= b1 for b0, b1 in own):
                errors.append(f"story-ids.json: lane {lk}: next {lane['next']} isn't a WR id in its own blocks")
    for i, (a0, a1, la) in enumerate(blocks):
        for b0, b1, lb in blocks[i + 1:]:
            nested = (a0 <= b0 and b1 <= a1) or (b0 <= a0 and a1 <= b1)
            if (a0, a1) == (b0, b1) or (a0 <= b1 and b0 <= a1 and not nested):
                errors.append(f"story-ids.json: {_span_text(a0, a1)} ({la.get('key')}) and {_span_text(b0, b1)} "
                              f"({lb.get('key')}) overlap; a block may only sit inside a larger one")
    exceptions: dict[int, dict[str, Any]] = {}
    for ex in ledger.get("exceptions", []):
        if (r := _span(ex)) is None:
            errors.append("story-ids.json: an exception needs a first and a last WR id, first not after last")
            continue
        name = _span_text(*r)
        if not ex.get("note"):
            errors.append(f"story-ids.json: exception {name}: no note saying what collided")
        if not ex.get("lanes") or any(k not in lanes for k in ex["lanes"]):
            errors.append(f"story-ids.json: exception {name}: lanes must name lanes in the ledger")
        for n in range(r[0], r[1] + 1):
            if n in exceptions:
                errors.append(f"story-ids.json: WR-{n} is in two exceptions")
            exceptions[n] = ex

    def owner(n: int) -> tuple[int, int, dict[str, Any]] | None:
        """The smallest block holding n: a block inside a larger one belongs to its own lane."""
        return min((b for b in blocks if b[0] <= n <= b[1]), key=lambda b: b[1] - b[0], default=None)

    uses = _id_uses(queues)
    for u in uses:
        block, ex = owner(u.number), exceptions.get(u.number)
        if block is None and ex is None:
            errors.append(f"{u.sid} ({u.place}) is in no block of story-ids.json; take the next id from its lane's block")
            continue
        if u.workstream is None:
            continue
        allowed = {block[2].get("workstream")} if block else set()
        allowed |= {lanes[k].get("workstream") for k in (ex or {}).get("lanes", []) if k in lanes}
        if u.workstream not in allowed:
            given = (f"{block[2].get('name')} ({_span_text(block[0], block[1])}, workstream {block[2].get('workstream')})"
                     if block else "no lane")
            errors.append(f"{u.sid} is in the {u.place}, but story-ids.json gives {u.sid} to {given}")
    defs: dict[str, list[_Use]] = {}
    for u in uses:
        if u.defines:
            defs.setdefault(u.sid, []).append(u)
    for sid, found in defs.items():
        if _wr_number(sid) in exceptions:
            continue
        first = found[0]
        for other in found[1:]:
            same = (first.issue is not None and first.issue == other.issue) or _same_title(first.title, other.title)
            if not same:
                errors.append(f"{sid} names two stories: {first.place} ({first.title!r}) and {other.place} ({other.title!r})")
    return errors


def side_table(queues: dict[str, Any]) -> str:
    rows = ["| Side program | Workstream | Thread | Review cap | Stories |", "| --- | --- | --- | --- | --- |"]
    names = {ws["key"]: ws["name"] for ws in queues["workstreams"]}
    for sp in queues.get("side_programs", []):
        stories = "<br>".join(f"{st['status']}: {st['title']} ({_issue_link(st)})" + (f" ({st['note']})" if st.get("note") else "")
                              for st in sp["stories"])
        rows.append(f"| {sp['name']} | {names.get(sp['workstream'], sp['workstream'])} | {sp['thread']} | {sp['review_cap']} | {stories} |")
    return "\n".join(rows)


def _issue_link(slot: dict[str, Any]) -> str:
    return f"[#{slot['issue']}](https://github.com/{REPO}/issues/{slot['issue']})" if slot.get("issue") else "no issue yet"


def table(queues: dict[str, Any], backlog: dict[str, dict[str, Any]]) -> str:
    rows = ["| Workstream | Lead thread | Active | Ready next | Blockers | Next action |",
            "| --- | --- | --- | --- | --- | --- |"]
    for ws in queues["workstreams"]:
        def cell(slot: dict[str, Any]) -> str:
            s = slot_story(slot, queues, backlog)
            return f"{s['title']} ({_issue_link(slot)})" if s["id"].startswith("#") else f"{s['id']} {s['title']} ({_issue_link(slot)})"
        active = cell(ws["active"]) if ws.get("active") else "none"
        ready = "<br>".join(cell(r) for r in ws.get("ready", []))
        blockers = "<br>".join(ws.get("blockers", [])) or "none"
        rows.append(f"| {ws['name']} | {ws['lead']} | {active} | {ready} | {blockers} | {ws['next_action']} |")
    return "\n".join(rows)


def _replace(text: str, begin: str, end: str, body: str) -> str:
    if begin not in text:
        return text
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{begin}\n{body}\n{end}{tail}"


def _rendered(text: str, queues: dict[str, Any], backlog: dict[str, dict[str, Any]]) -> str:
    text = _replace(text, BEGIN, END, table(queues, backlog))
    return _replace(text, SIDE_BEGIN, SIDE_END, side_table(queues))


def render() -> None:
    queues, backlog = load()
    README.write_text(_rendered(README.read_text(encoding="utf-8"), queues, backlog), encoding="utf-8")


def body(sid: str) -> str:
    """The GitHub issue body for a queued story. Never use it for security findings: issues are public."""
    queues, backlog = load()
    s = story(sid, queues, backlog)
    ws = next((w for w in queues["workstreams"] if any(sl.get("story") == sid for _, sl in slots(w))
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
                                                                      for _, sl in slots(w) if sl.get("story") == sid), None)) else []),
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
    lines = [f"# Weekly review: week ending {today.isoformat()}", "",
             "Generated by the Weekly review workflow from GitHub and the repo's product data. "
             "The orchestrator thread adds recommendations, the roadmap update and decisions for Chris as a comment.", "",
             "## Delivery", f"- PRs merged since {gh['since']}: **{gh['merged']['total']}**; PRs opened: {gh['opened']}",
             f"- Main CI, last {len(ci)} runs: {ci_summary(ci)}",
             f"- Open PRs: {len(gh['open_prs'])}, of which drafts: {sum(1 for p in gh['open_prs'] if p['draft'])}", "",
             "## Product", f"- Features: {len(inv)}; verified end to end: {verified}",
             "- Backlog states: " + ", ".join(f"{k} {v}" for k, v in sorted(states.items())), "",
             "## Story issues by status"]
    for status in ("active", "ready", "blocked", "review", "untriaged"):
        items = by_status.get(status, [])
        lines.append(f"- **{status}** ({len(items)})" + ("".join(f"\n  - {t}" for t in items)))
    lines += ["", "## Work queues", table(queues, backlog), "",
              "## Side programs", side_table(queues), "",
              "## Token utilization",
              "Not measurable here: neither GitHub nor the Claude threads can read the subscription allocation, "
              "so no percentage is reported. Proxies: open story issues per status above, and workstreams "
              "with fewer than two ready stories (queues.py check)."]
    errors, short = check()
    problems = errors + short
    lines += ["", "## Queue check", *(f"- {p}" for p in problems)] if problems else ["", "## Queue check", "- Every workstream has an active story and two ready ones."]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd in ("render", "all"):
        render()
    if cmd in ("check", "all"):
        errors, short = check()
        for p in short:
            print(f"short: {p}")
        for p in errors:
            print(p)
        if errors:
            return 1
        print("Work queues are consistent." + (" Some queues are short; the weekly review refills them." if short else ""))
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
