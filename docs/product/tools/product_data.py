"""Checks, scores and renders the product management data in docs/product/.

FEATURE_INVENTORY.json and PRODUCT_BACKLOG.json are the source of truth. The Markdown pages
keep their own prose; the tables between <!-- BEGIN GENERATED: name --> and
<!-- END GENERATED: name --> markers are written by this script, so never edit those by hand.

    python docs/product/tools/product_data.py check     # schema, cross-reference and generated-table checks
    python docs/product/tools/product_data.py score     # recompute every backlog item's score
    python docs/product/tools/product_data.py render    # rewrite the generated tables
    python docs/product/tools/product_data.py all       # score, render, then check
    python docs/product/tools/product_data.py summary   # counts for the executive status report

Standard library only. If the jsonschema package is installed it also validates both files
against docs/product/schema/; without it the structural checks below still run.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

PRODUCT = Path(__file__).resolve().parents[1]
INVENTORY = PRODUCT / "FEATURE_INVENTORY.json"
BACKLOG = PRODUCT / "PRODUCT_BACKLOG.json"
SCHEMAS = PRODUCT / "schema"
REPO = PRODUCT.parents[1]

STATUSES = ["Implemented", "Partially Implemented", "Implemented but Not Exposed", "Implemented but Unreliable",
            "Documented Only", "Missing", "Planned", "Deprecated"]
VERIFICATION = ["Verified", "Partially Verified", "Unverified"]
PRIORITIES = ["P0", "P1", "P2", "P3"]
BENEFITS = ["developer_value", "cu_reduction", "enterprise_value", "differentiation", "strategic_alignment"]
COSTS = ["complexity", "risk"]
SCORE_LABELS = {"developer_value": "Dev", "cu_reduction": "CU", "enterprise_value": "Ent", "differentiation": "Diff",
                "strategic_alignment": "Align", "complexity": "Cplx", "risk": "Risk"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def categories() -> list[str]:
    schema = load(SCHEMAS / "feature-inventory.schema.json")
    return schema["$defs"]["category"]["enum"]


# ---------- check ----------

def _schema_errors(data: dict, schema_name: str) -> list[str]:
    try:
        import jsonschema
        from referencing import Registry, Resource
    except ImportError:
        return []
    schemas = {p.name: load(p) for p in SCHEMAS.glob("*.json")}
    registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s)) for s in schemas.values())
    validator = jsonschema.Draft202012Validator(schemas[schema_name], registry=registry)
    return [f"{schema_name}: {'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in validator.iter_errors(data)]


def check() -> list[str]:
    inv, bl = load(INVENTORY), load(BACKLOG)
    errors = _schema_errors(inv, "feature-inventory.schema.json") + _schema_errors(bl, "product-backlog.schema.json")
    cats = categories()
    features = {f["id"]: f for f in inv["features"]}
    items = {i["id"]: i for i in bl["items"]}
    for kind, rows in (("feature", inv["features"]), ("backlog item", bl["items"])):
        for rid, n in Counter(r["id"] for r in rows).items():
            if n > 1:
                errors.append(f"{kind} id {rid} is used {n} times")
    for f in inv["features"]:
        where = f"feature {f['id']}"
        if f["status"] not in STATUSES:
            errors.append(f"{where}: unknown status {f['status']!r}")
        if f["verification"] not in VERIFICATION:
            errors.append(f"{where}: unknown verification {f['verification']!r}")
        if f["category"] not in cats:
            errors.append(f"{where}: unknown category {f['category']!r}")
        kinds = {e["kind"] for e in f["evidence"]}
        if f["verification"] == "Verified" and "smoke" not in kinds and "test" not in kinds:
            errors.append(f"{where}: Verified needs smoke or test evidence observed in this assessment")
        if f["verification"] != "Unverified" and not kinds & {"smoke", "test"}:
            errors.append(f"{where}: {f['verification']} needs smoke or test evidence")
        for dep in f.get("depends_on", []):
            if dep not in features:
                errors.append(f"{where}: depends on unknown feature {dep}")
        for ref in f.get("backlog", []):
            if ref not in items:
                errors.append(f"{where}: names unknown backlog item {ref}")
        for path in f.get("source", []) + f.get("tests", []) + f.get("docs", []):
            file = path.split(" ")[0].split(":")[0]
            if file and not (REPO / file).exists():
                errors.append(f"{where}: path {file} does not exist")
    for i in bl["items"]:
        where = f"backlog item {i['id']}"
        if i["category"] not in cats:
            errors.append(f"{where}: unknown category {i['category']!r}")
        for dep in i.get("depends_on", []):
            if dep not in items:
                errors.append(f"{where}: depends on unknown item {dep}")
            elif dep == i["id"]:
                errors.append(f"{where}: depends on itself")
        for ref in i.get("features", []):
            if ref not in features:
                errors.append(f"{where}: names unknown feature {ref}")
        if i["mandatory"] != "none" and not i.get("mandatory_reason"):
            errors.append(f"{where}: mandatory items say why (mandatory_reason)")
        if i.get("package") and not (REPO / i["package"]).exists():
            errors.append(f"{where}: package {i['package']} does not exist")
        expected = computed(i, bl["scoring"])
        if i.get("computed") != expected:
            errors.append(f"{where}: computed scores are stale; run `product_data.py score`")
    errors += _cycles(items)
    if not errors:
        errors += _render_drift()
    return errors


def _cycles(items: dict) -> list[str]:
    state: dict[str, int] = {}
    errors: list[str] = []

    def visit(node: str, path: list[str]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            errors.append("dependency cycle: " + " -> ".join(path[path.index(node):] + [node]))
            return
        state[node] = 1
        for dep in items.get(node, {}).get("depends_on", []):
            if dep in items:
                visit(dep, path + [node])
        state[node] = 2

    for node in items:
        visit(node, [])
    return errors


# ---------- score ----------

def computed(item: dict, scoring: dict) -> dict:
    """The model documented in PRIORITIZED_ROADMAP.md, section "Scoring model"."""
    s, bw, cw = item["scores"], scoring["benefit_weights"], scoring["cost_weights"]
    benefit = sum(bw[k] * s[k] for k in BENEFITS) / sum(bw[k] for k in BENEFITS)
    cost = sum(cw[k] * s[k] for k in COSTS) / sum(cw[k] for k in COSTS)
    score = benefit * (6 - cost) / 5
    if item.get("priority_override"):
        priority = item["priority_override"]["priority"]
    elif item["mandatory"] == "blocker":
        priority = "P0"
    elif score >= scoring["thresholds"]["P1"] or item["mandatory"] == "required":
        priority = "P1"
    elif score >= scoring["thresholds"]["P2"]:
        priority = "P2"
    else:
        priority = "P3"
    return {"benefit": round(benefit, 2), "cost": round(cost, 2), "score": round(score, 2), "priority": priority}


def score() -> None:
    bl = load(BACKLOG)
    for item in bl["items"]:
        item["computed"] = computed(item, bl["scoring"])
    save(BACKLOG, bl)


# ---------- render ----------

def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _anchor(text: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", text.lower()).strip().replace(" ", "-")


def _link(path: str, here: Path) -> str:
    file = path.split(" ")[0].split(":")[0]
    rel = Path(*([".."] * len(here.relative_to(REPO).parent.parts))) / file
    if file.startswith("docs/"):
        rel = Path(*([".."] * (len(here.relative_to(REPO / "docs").parent.parts)))) / file[len("docs/"):]
        return f"[{path}]({rel.as_posix()})"
    return f"`{path}`"


def _pr(n: int) -> str:
    return f"[#{n}](https://github.com/KratosDataGod/KratosForge/pull/{n})"


# While check() compares the pages with a fresh render, rendered pages are kept here instead of written.
_pending: dict[Path, str] | None = None


def _replace(path: Path, name: str, body: str) -> None:
    text = _pending.get(path, path.read_text(encoding="utf-8")) if _pending is not None else path.read_text(encoding="utf-8")
    begin, end = f"<!-- BEGIN GENERATED: {name} -->", f"<!-- END GENERATED: {name} -->"
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)
    if not pattern.search(text):
        raise SystemExit(f"{path.name} has no {begin} marker")
    text = pattern.sub(lambda _: f"{begin}\n{body.rstrip()}\n{end}", text)
    if _pending is not None:
        _pending[path] = text
    else:
        path.write_text(text, encoding="utf-8")


def _render_drift() -> list[str]:
    """Generated tables that differ from what the JSON renders to, such as a hand edit render would revert."""
    global _pending
    _pending = {}
    try:
        render_inventory()
        render_backlog()
        render_roadmap()
        rendered = _pending
    finally:
        _pending = None
    return [f"{path.relative_to(REPO).as_posix()}: generated tables differ from the JSON; edit the JSON, then run `product_data.py render`"
            for path, text in rendered.items() if text != path.read_text(encoding="utf-8")]


def render_inventory() -> None:
    inv = load(INVENTORY)
    md = PRODUCT / "FEATURE_INVENTORY.md"
    feats = inv["features"]
    # Status by verification.
    rows = ["| Status | " + " | ".join(VERIFICATION) + " | Total |", "| --- |" + " ---: |" * (len(VERIFICATION) + 1)]
    for st in STATUSES:
        counts = [sum(1 for f in feats if f["status"] == st and f["verification"] == v) for v in VERIFICATION]
        if sum(counts):
            rows.append(f"| {st} | " + " | ".join(map(str, counts)) + f" | {sum(counts)} |")
    totals = [sum(1 for f in feats if f["verification"] == v) for v in VERIFICATION]
    rows.append("| **Total** | " + " | ".join(f"**{t}**" for t in totals) + f" | **{len(feats)}** |")
    _replace(md, "summary", "\n".join(rows))
    # Category index.
    out = []
    for cat in categories():
        group = [f for f in feats if f["category"] == cat]
        if not group:
            continue
        out.append(f"### {cat}\n")
        out.append("| Id | Feature | Status | Verification | Where people reach it | Next action |")
        out.append("| --- | --- | --- | --- | --- | --- |")
        for f in group:
            surface = f["ui"]["surface"] + (" (Preview)" if f["ui"].get("preview") else "")
            out.append(f"| [{f['id']}](#{f['id'].lower()}) | {_cell(f['name'])} | {f['status']} | {f['verification']} | "
                       f"{_cell(surface)} | {_cell(f['next_action'])} |")
        out.append("")
    _replace(md, "index", "\n".join(out))
    # Details.
    out = []
    for f in feats:
        out.append(f"### {f['id']}\n")
        out.append(f"**{f['name']}.** {f['description']}\n")
        out.append(f"- **Pillar and category:** {f['pillar']}; {f['category']}")
        out.append(f"- **Status:** {f['status']}. **Verification:** {f['verification']}.")
        surface = f["ui"]["surface"] + (" (Preview)" if f["ui"].get("preview") else "")
        out.append(f"- **Where people reach it:** {surface}" + (f". Accessibility: {f['ui']['accessibility']}" if f["ui"].get("accessibility") else ""))
        out.append("- **Evidence:** " + "; ".join(f"{e['kind']}: {e['ref']}" + (f" ({e['note']})" if e.get("note") else "") for e in f["evidence"]))
        if f.get("source"):
            out.append("- **Source:** " + ", ".join(f"`{s}`" for s in f["source"]))
        if f.get("tests"):
            out.append("- **Tests:** " + ", ".join(f"`{t}`" for t in f["tests"]))
        if f.get("docs"):
            out.append("- **Docs:** " + ", ".join(_link(d, md) for d in f["docs"]))
        if f.get("depends_on"):
            out.append("- **Depends on:** " + ", ".join(f"[{d}](#{d.lower()})" for d in f["depends_on"]))
        if f.get("limitations"):
            out.append("- **Known limitations:** " + " ".join(l if l.endswith(".") else l + "." for l in f["limitations"]))
        refs = [_pr(p) for p in f.get("prs", [])] + [f"issue [#{n}](https://github.com/KratosDataGod/KratosForge/issues/{n})" for n in f.get("issues", [])]
        if refs:
            out.append("- **Pull requests and issues:** " + ", ".join(refs))
        if f.get("workstream"):
            out.append(f"- **Owning workstream:** {f['workstream']}")
        if f.get("backlog"):
            out.append("- **Backlog:** " + ", ".join(f"[{b}](PRODUCT_BACKLOG.md#{b.lower()})" for b in f["backlog"]))
        out.append(f"- **Recommended next action:** {f['next_action']}\n")
    _replace(md, "details", "\n".join(out))


def render_backlog() -> None:
    bl = load(BACKLOG)
    md = PRODUCT / "PRODUCT_BACKLOG.md"
    items = bl["items"]
    rows = ["| Category | " + " | ".join(PRIORITIES) + " | Total |", "| --- |" + " ---: |" * 5]
    for cat in categories():
        counts = [sum(1 for i in items if i["category"] == cat and i["computed"]["priority"] == p) for p in PRIORITIES]
        rows.append(f"| [{cat}](#{_anchor(cat)}) | " + " | ".join(map(str, counts)) + f" | {sum(counts)} |")
    totals = [sum(1 for i in items if i["computed"]["priority"] == p) for p in PRIORITIES]
    rows.append("| **Total** | " + " | ".join(f"**{t}**" for t in totals) + f" | **{len(items)}** |")
    _replace(md, "summary", "\n".join(rows))
    out = []
    for cat in categories():
        out.append(f"## {cat}\n")
        group = [i for i in items if i["category"] == cat]
        if not group:
            out.append("No items.\n")
            continue
        out.append("| Id | Item | Priority | Owner | Effort | State |")
        out.append("| --- | --- | --- | --- | --- | --- |")
        for i in group:
            out.append(f"| [{i['id']}](#{i['id'].lower()}) | {_cell(i['title'])} | {i['computed']['priority']} | {_cell(i['owner'])} | {i['effort']} | {i['state']} |")
        out.append("")
        for i in group:
            out.append(f"### {i['id']}\n")
            out.append(f"**{i['title']}** · {i['computed']['priority']} (score {i['computed']['score']}) · {i['effort']} · confidence {i['confidence']} · {i['state']}\n")
            out.append(f"- **Problem:** {i['problem']}")
            out.append(f"- **Proposed capability:** {i['capability']}")
            out.append("- **Current-state evidence:** " + "; ".join(i["evidence"]))
            if i.get("features"):
                out.append("- **Inventory features:** " + ", ".join(f"[{f}](FEATURE_INVENTORY.md#{f.lower()})" for f in i["features"]))
            deps = [f"[{d}](#{d.lower()})" for d in i.get("depends_on", [])] + i.get("external_dependencies", [])
            out.append("- **Dependencies:** " + (", ".join(deps) if deps else "none"))
            out.append(f"- **Owner:** {i['owner']}" + (f" ({', '.join(i['source_refs'])})" if i.get("source_refs") else ""))
            if i["mandatory"] != "none":
                out.append(f"- **Mandatory ({i['mandatory']}):** {i['mandatory_reason']}")
            if i.get("priority_override"):
                o = i["priority_override"]
                out.append(f"- **Priority set by the product owner:** {o['priority']} ({o['by']}, {o['date']}): {o['reason']}")
            if i.get("duplicates"):
                out.append("- **Merged duplicates:** " + "; ".join(i["duplicates"]))
            if i.get("prs"):
                out.append("- **Pull requests:** " + ", ".join(_pr(p) for p in i["prs"]))
            if i.get("package"):
                rel = Path(i["package"]).relative_to("docs/product").as_posix()
                out.append(f"- **Implementation package:** [{rel}]({rel})")
            out.append("- **Acceptance criteria:**")
            out.extend(f"    - {a}" for a in i["acceptance"])
            out.append("")
    _replace(md, "items", "\n".join(out))


def render_roadmap() -> None:
    bl = load(BACKLOG)
    md = PRODUCT / "PRIORITIZED_ROADMAP.md"
    items = sorted(bl["items"], key=lambda i: (PRIORITIES.index(i["computed"]["priority"]), -i["computed"]["score"], i["id"]))
    head = ("| Id | Item | " + " | ".join(SCORE_LABELS[k] for k in BENEFITS + COSTS)
            + " | Benefit | Cost | Score | Mandatory | Effort | Confidence | Depends on | Owner |")
    out = []
    for p in PRIORITIES:
        group = [i for i in items if i["computed"]["priority"] == p]
        out.append(f"### {p} ({len(group)} items)\n")
        if not group:
            out.append("None.\n")
            continue
        out.append(head)
        out.append("| --- | --- |" + " ---: |" * 10 + " --- | --- | --- | --- | --- |")
        for i in group:
            s, c = i["scores"], i["computed"]
            out.append(f"| [{i['id']}](PRODUCT_BACKLOG.md#{i['id'].lower()}) | {_cell(i['title'])} | "
                       + " | ".join(str(s[k]) for k in BENEFITS + COSTS)
                       + f" | {c['benefit']} | {c['cost']} | {c['score']} | {i['mandatory']} | {i['effort']} | {i['confidence']} | "
                       + (", ".join(i.get("depends_on", [])) or "none") + f" | {_cell(i['owner'])} |")
        out.append("")
    _replace(md, "ranking", "\n".join(out))
    mand = [i for i in items if i["mandatory"] != "none"]
    rows = ["| Id | Item | Kind | Commercial score | Priority | Why it is not ranked down |", "| --- | --- | --- | ---: | --- | --- |"]
    for i in mand:
        rows.append(f"| [{i['id']}](PRODUCT_BACKLOG.md#{i['id'].lower()}) | {_cell(i['title'])} | {i['mandatory']} | {i['computed']['score']} | {i['computed']['priority']} | {_cell(i['mandatory_reason'])} |")
    _replace(md, "mandatory", "\n".join(rows))
    over = [i for i in items if i.get("priority_override")]
    rows = ["| Id | Item | Computed score | Priority | Decided by | Reason |", "| --- | --- | ---: | --- | --- | --- |"]
    for i in over:
        o = i["priority_override"]
        rows.append(f"| [{i['id']}](PRODUCT_BACKLOG.md#{i['id'].lower()}) | {_cell(i['title'])} | {i['computed']['score']} | {o['priority']} | {_cell(o['by'])}, {o['date']} | {_cell(o['reason'])} |")
    _replace(md, "overrides", "\n".join(rows) if over else "None.")
    # Sequencing: an item that a higher-priority item depends on is built first, whatever its own score.
    by_id = {i["id"]: i for i in items}
    pulled: dict[str, tuple[str, str]] = {}

    def pull(item_id: str, priority: str, via: str) -> None:
        dep = by_id[item_id]
        if PRIORITIES.index(priority) < PRIORITIES.index(pulled.get(item_id, (dep["computed"]["priority"], ""))[0]):
            pulled[item_id] = (priority, via)
            for d in dep.get("depends_on", []):
                pull(d, priority, via)

    for i in items:
        for d in i.get("depends_on", []):
            pull(d, i["computed"]["priority"], i["id"])
    rows = ["| Id | Item | Own priority | Sequenced as | Because of |", "| --- | --- | --- | --- | --- |"]
    for item_id in sorted(pulled, key=lambda k: (PRIORITIES.index(pulled[k][0]), k)):
        i = by_id[item_id]
        rows.append(f"| [{item_id}](PRODUCT_BACKLOG.md#{item_id.lower()}) | {_cell(i['title'])} | {i['computed']['priority']} | "
                    f"{pulled[item_id][0]} | [{pulled[item_id][1]}](PRODUCT_BACKLOG.md#{pulled[item_id][1].lower()}) |")
    _replace(md, "pulled", "\n".join(rows) if pulled else "None.")


def summary() -> str:
    inv, bl = load(INVENTORY), load(BACKLOG)
    status = Counter(f["status"] for f in inv["features"])
    ver = Counter(f["verification"] for f in inv["features"])
    pri = Counter(i["computed"]["priority"] for i in bl["items"])
    lines = [f"Inventory at {inv['assessed']['commit']} ({inv['assessed']['date']}): {len(inv['features'])} features"]
    lines += [f"  {s}: {status[s]}" for s in STATUSES if status[s]]
    lines += [f"  {v}: {ver[v]}" for v in VERIFICATION]
    lines.append(f"Backlog ({bl['updated']}): {len(bl['items'])} items")
    lines += [f"  {p}: {pri[p]}" for p in PRIORITIES]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd in ("score", "all"):
        score()
    if cmd in ("render", "all"):
        render_inventory()
        render_backlog()
        render_roadmap()
    if cmd == "summary":
        print(summary())
        return 0
    if cmd in ("check", "all"):
        errors = check()
        for e in errors:
            print(e)
        print(f"{len(errors)} problem(s)" if errors else "Product data is consistent.")
        return 1 if errors else 0
    if cmd not in ("score", "render"):
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
