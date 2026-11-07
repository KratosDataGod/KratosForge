"""Deterministic analyzers (OE-001): identical notebooks, repeated copy ingestion, full refreshes.

Every analyzer takes the workloads (inventory.py) and a context (usage by item, price, now) and
returns opportunities (model.py). Code is parsed, never run. A match is called *proven* only when
the evidence shows it exactly; anything the engine can't see is named in the opportunity's
validation steps instead of being assumed.

- :func:`duplicate_notebooks`: notebooks whose code is the same once comments, docstrings, spacing
  and SQL letter case are set aside (Python is compared by its syntax tree, not its text). Names
  are never compared.
- :func:`repeated_copies`: Copy activities in different pipelines that read the same source,
  into the same sink (the same data moved twice) or into different sinks (extracted twice).
- :func:`full_refreshes`: workloads that run on a schedule and rewrite whole tables: a notebook
  overwrite with no replaceWhere (rule NB007), an INSERT OVERWRITE or CREATE OR REPLACE TABLE ...
  AS SELECT, or a Copy activity whose table action is Overwrite.
"""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .. import depgraph, magics, notebook_practices
from . import impact, model

_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_PY_COMMENT = re.compile(r"(?m)^\s*#.*$")
_SPACE = re.compile(r"\s+")
_SQL_FULL = re.compile(r"(?is)\b(insert\s+overwrite(?:\s+table\b)?+\s+[\w.`\[\]]++(?!\s*partition)|"
                       r"create\s+or\s+replace\s+table\s+[\w.`\[\]]+[^;]*?\bas\s+select)\b")
_SPARK_SQL = re.compile(r"""(?is)\bspark\.sql\(\s*(?:[rfbu]{0,2})(\"\"\"|'''|"|')(.*?)\1""")
_VOLATILE = {"annotations", "description"}
_ONLY_NB007 = {r.id: {"enabled": r.id == "NB007"} for r in notebook_practices.RULES}
INVENTORY = "KratosForge workspace inventory (item definitions read with getDefinition)"


@dataclass
class Context:
    usage: dict[tuple[str, str], dict[str, Any]]
    now: dt.datetime
    price: dict[str, Any] | None = None


# ---- normalizing code


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]  # type: ignore[attr-defined]
    return tree


def normalize_cell(source: str) -> str:
    """A cell's code with what doesn't change what it does set aside: comments, docstrings, spacing,
    and the letter case of SQL. Python is compared by its syntax tree. Empty when the cell has no code."""
    cell = magics.cell(source)
    body = cell.body
    if cell.language == "sql":
        return "sql:" + _SPACE.sub(" ", _SQL_COMMENT.sub(" ", body)).strip().lower()
    if cell.language == "python":
        try:
            tree = _strip_docstrings(ast.parse(body))
        except SyntaxError:
            return "text:" + _SPACE.sub(" ", _PY_COMMENT.sub("", body)).strip()
        if not getattr(tree, "body", None) or all(isinstance(n, ast.Pass) for n in tree.body):  # type: ignore[attr-defined]
            return ""
        return "py:" + ast.dump(tree, include_attributes=False)
    return f"{cell.language}:" + _SPACE.sub(" ", body).strip()


def code_fingerprint(cells: list[str]) -> str | None:
    """The same for two notebooks whose code does the same, cell by cell. None for a notebook with no code."""
    norm = [n for n in (normalize_cell(c) for c in cells) if n]
    if not norm:
        return None
    return hashlib.sha256("\n\x00".join(norm).encode()).hexdigest()


def _tables(cells: list[str]) -> tuple[list[str], list[str]]:
    reads: set[str] = set()
    writes: set[str] = set()
    for c in cells:
        a = depgraph.analyze(c)
        reads.update(r["name"] for r in a["reads"] if r.get("kind") == "table")
        writes.update(r["name"] for r in a["writes"] if r.get("kind") == "table")
    return sorted(reads), sorted(writes)


def _runs_text(w: dict[str, Any]) -> str:
    r = w["runs"]
    if not r["read"]:
        return "run history not read"
    secs = f", {r['seconds_total']:.0f} s in total" if r["seconds_total"] else ""
    return f"{r['runs']} run(s) in the last {r['days']} days ({r['scheduled']} scheduled){secs}"


def _run_evidence(ws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [model.evidence("run_history", f"{w['name']}: {_runs_text(w)}", impact.JOB_HISTORY,
                           item_id=w["item_id"], runs=w["runs"]) for w in ws]


def _open(ws: list[dict[str, Any]], label: str) -> dict[str, Any]:
    return {"kind": "inspect", "label": label, "items": [{"workspace_id": w.get("workspace_id"), "item_id": w["item_id"],
                                                          "type": w["type"], "name": w["name"]} for w in ws]}


# ---- 3.1 identical notebooks


def duplicate_notebooks(workloads: list[dict[str, Any]], ctx: Context) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for w in workloads:
        if w["type"] == "Notebook" and w["definition"] == "read":
            fp = code_fingerprint(w.get("cells") or [])
            if fp:
                groups[fp].append(w)
    out = []
    for fp, same in groups.items():
        if len(same) < 2:
            continue
        by_lh: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        for w in same:
            by_lh[w.get("default_lakehouse")].append(w)
        known = [k for k in by_lh if k is not None]
        reads, writes = _tables(same[0].get("cells") or [])
        if len(known) > 1 and None not in by_lh:
            out.append(_same_code_other_data(same, fp, reads, writes))
            continue
        out.append(_duplicate(same, fp, reads, writes, unknown_lh=None in by_lh, ctx=ctx))
    return out


def _keep_order(w: dict[str, Any]) -> tuple[int, int, str]:
    r = w["runs"]
    return (-(r["runs"] or 0), -(r["scheduled"] or 0), w["name"].lower())


def _duplicate(same: list[dict[str, Any]], fp: str, reads: list[str], writes: list[str], *, unknown_lh: bool,
               ctx: Context) -> dict[str, Any]:
    keep, *rest = sorted(same, key=_keep_order)
    running = [w for w in rest if w["runs"]["read"] and (w["runs"]["runs"] or 0) > 0]
    unread = [w for w in rest if not w["runs"]["read"]]
    names = ", ".join(w["name"] for w in rest)
    ev = [model.evidence("identical_code", f"{len(same)} notebooks have the same code once comments, spacing and "
                         "docstrings are set aside, compared cell by cell", INVENTORY, fingerprint=fp[:16],
                         items=[w["item_id"] for w in same], cells=len(same[0].get("cells") or []))]
    if not unknown_lh:
        ev.append(model.evidence("same_lakehouse", "They all have the same default lakehouse, so they read and "
                                 "write the same tables", INVENTORY, lakehouse=same[0].get("default_lakehouse")))
    if reads or writes:
        ev.append(model.evidence("tables", f"Reads {', '.join(reads) or 'no named table'}; writes "
                                 f"{', '.join(writes) or 'no named table'}", "notebook code, read by depgraph",
                                 reads=reads, writes=writes))
    ev += _run_evidence(same)
    validation = ["Check nothing else calls the copies by name (pipelines, notebookutils.notebook.run, schedules).",
                  "Run the kept notebook once and compare its output tables with the last run of each copy "
                  "(row counts and content)."]
    if unknown_lh:
        validation.insert(0, "Check both notebooks use the same lakehouse: one has no default lakehouse in its "
                          "definition, so it may read other data through absolute paths or parameters.")
    if unread:
        validation.append("Read the run history of " + ", ".join(w["name"] for w in unread) + "; it couldn't be read.")
    if running:
        title = f"{len(running) + 1} notebooks do the same work: keep {keep['name']}"
        rec = (f"Keep {keep['name']}. Stop the schedules of {', '.join(w['name'] for w in running)} and point "
               "anything that calls them at the kept notebook, then retire the copies.")
    else:
        title = f"Copies of {keep['name']} that don't run: {names}"
        rec = f"Keep {keep['name']}. Retire {names}: they hold the same code and didn't run in the period read."
    return model.make(
        rule="OE-DUP-NB", category="duplicate_processing", action="eliminate", title=title,
        artifacts=[model.artifact(keep, "keep")] + [model.artifact(w, "remove") for w in rest],
        current=f"{len(same)} notebooks with identical code: {', '.join(w['name'] for w in same)}.",
        problem="The same transformation is kept, and possibly run, more than once.",
        evidence=ev, recommendation=rec,
        alternatives=["Keep both if they must stay isolated (separate owners, security or release cycles); record why.",
                      "Turn the shared code into one parameterized notebook that each caller runs."],
        effort="low", risk="low" if not running else "medium",
        confidence="high" if unknown_lh or unread else "proven",
        validation=validation, rollback="Turn the copy's schedule back on; nothing is deleted until the copy is "
                                        "retired, and retiring is a separate, confirmed step in Fabric.",
        source="duplicate notebook analyzer (OE-001)", next_step=_open(same, "Compare the notebooks"),
        affects=[w["item_id"] for w in running],
        impact=impact.avoidable(running, ctx.usage, ctx.now, price=ctx.price) if running
        else {"basis": "no runs to avoid", "note": "The copies didn't run in the period read; retiring them saves "
                                                   "maintenance and confusion, not capacity."})


def _same_code_other_data(same: list[dict[str, Any]], fp: str, reads: list[str], writes: list[str]) -> dict[str, Any]:
    return model.make(
        rule="OE-DUP-NB-PARAM", category="duplicate_processing", action="consolidate",
        title=f"{len(same)} notebooks share code but use different lakehouses",
        artifacts=[model.artifact(w, "change") for w in same],
        current=f"The same code is kept in {len(same)} notebooks, each with its own default lakehouse.",
        problem="Each copy has to be changed and tested on its own; they drift apart over time.",
        evidence=[model.evidence("identical_code", "Same code once comments, spacing and docstrings are set aside",
                                 INVENTORY, fingerprint=fp[:16], items=[w["item_id"] for w in same]),
                  model.evidence("different_lakehouses", "Their default lakehouses differ, so they process "
                                 "different data: this is not duplicate processing", INVENTORY,
                                 lakehouses=sorted({str(w.get("default_lakehouse")) for w in same}))],
        recommendation="Keep one notebook with the lakehouse as a parameter, and run it once per lakehouse.",
        alternatives=["Leave them as they are if each team owns its copy on purpose."],
        effort="medium", risk="low", confidence="proven",
        validation=["Run the parameterized notebook for each lakehouse and compare its outputs with the copy it replaces."],
        rollback="Keep the copies until the parameterized notebook has run cleanly for each lakehouse.",
        source="duplicate notebook analyzer (OE-001)", next_step=_open(same, "Compare the notebooks"), affects=[],
        impact={"basis": "maintenance", "note": "No capacity is avoided: each copy processes its own data."})


# ---- 3.2 / 5 the same data copied twice


def _canon(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _canon(v) for k, v in sorted(value.items()) if k not in _VOLATILE}
    if isinstance(value, list):
        return [_canon(v) for v in value]
    return value


def _sig(value: Any) -> str:
    return json.dumps(_canon(value), sort_keys=True, separators=(",", ":"), default=str)


def _copies(w: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for a in w.get("activities") or [] if a["type"] == "Copy" and not a["inactive"]]


def repeated_copies(workloads: list[dict[str, Any]], ctx: Context) -> list[dict[str, Any]]:
    by_both: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    by_source: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for w in workloads:
        if w["type"] != "DataPipeline" or w["definition"] != "read":
            continue
        for a in _copies(w):
            src, snk = a["type_properties"].get("source"), a["type_properties"].get("sink")
            if not isinstance(src, dict) or not isinstance(snk, dict):
                continue
            by_both[(_sig(src), _sig(snk))].append((w, a))
            by_source[_sig(src)].append((w, a))
    out = []
    paired: set[str] = set()
    for (src, _sink), hits in by_both.items():
        if len({w["item_id"] for w, _ in hits}) < 2:
            continue
        paired.add(src)
        out.append(_copy_opportunity(hits, src, same_sink=True, ctx=ctx))
    for src, hits in by_source.items():
        if src in paired or len({w["item_id"] for w, _ in hits}) < 2:
            continue
        out.append(_copy_opportunity(hits, src, same_sink=False, ctx=ctx))
    return out


def _copy_opportunity(hits: list[tuple[dict[str, Any], dict[str, Any]]], src: str, *, same_sink: bool,
                      ctx: Context) -> dict[str, Any]:
    pipelines = list({w["item_id"]: w for w, _ in hits}.values())
    keep, *rest = sorted(pipelines, key=_keep_order)
    dynamic = "@" in src
    acts = [{"pipeline": w["name"], "item_id": w["item_id"], "activity": a["name"], "path": a["path"]} for w, a in hits]
    ev = [model.evidence("same_source", f"{len(hits)} Copy activities in {len(pipelines)} pipelines read the same "
                         "source (same connection, table or query)" + (" and write the same sink" if same_sink else
                                                                       ", each into its own sink"),
                         INVENTORY, activities=acts, source_settings=json.loads(src))]
    ev += _run_evidence(pipelines)
    whole = [w for w in rest if len(_copies(w)) == 1 and len(w.get("activities") or []) == 1]
    partial = "" if whole and len(whole) == len(rest) else (
        "The Copy is one step of a larger pipeline; its share of the pipeline's run isn't measured yet. "
        "Read the pipeline's activity runs (Monitor > Pipeline runs) to measure it.")
    removed = [w for w in rest if w["runs"]["read"] and (w["runs"]["runs"] or 0) > 0]
    validation = ["Check the copies run at times that let one serve both (the later reader needs the data no "
                  "older than it gets today)."]
    if dynamic:
        validation.insert(0, "The source uses expressions (@...): check they resolve to the same table or query at run time.")
    if same_sink:
        title = f"The same data is copied into the same place by {len(pipelines)} pipelines"
        rec = (f"Keep the copy in {keep['name']}. Remove it from {', '.join(w['name'] for w in rest)}, and make "
               "those pipelines wait for the kept one if they need its data.")
        alts = ["Move the copy into a shared ingestion pipeline every consumer depends on."]
    else:
        title = f"The same source is extracted {len(hits)} times"
        rec = (f"Land the source once (in {keep['name']}) and let the other consumers read it from there, through "
               "a OneLake shortcut if they're in another lakehouse.")
        alts = ["Keep separate extracts if a consumer needs a different point in time or must not depend on another team.",
                "Use incremental extraction (a watermark) so each extract moves less."]
        validation.append("Check each sink's consumers can read the shared copy (permissions, schema, freshness).")
    return model.make(
        rule="OE-DUP-COPY" if same_sink else "OE-REPEAT-EXTRACT", category="redundant_data_movement",
        action="eliminate" if same_sink else "consolidate", title=title,
        artifacts=[model.artifact(keep, "keep")] + [model.artifact(w, "change") for w in rest],
        current=f"Copy activities: {', '.join(a['pipeline'] + ' / ' + a['activity'] for a in acts)}.",
        problem=("The same rows are read from the source and written to the same table more than once."
                 if same_sink else "The source is read, and the data moved, once per consumer."),
        evidence=ev, recommendation=rec, alternatives=alts, effort="low" if same_sink else "medium",
        risk="medium", confidence="medium" if dynamic else ("high" if same_sink else "medium"),
        validation=validation, rollback="Re-enable the removed Copy activity (set it back to Active); the pipeline "
                                        "definition is versioned in Fabric and in Git when connected.",
        source="pipeline copy analyzer (OE-001)", next_step=_open(pipelines, "Open the pipelines"),
        affects=[w["item_id"] for w in removed],
        key=[f"{w['item_id']}/{a['name']}" for w, a in hits],
        impact=impact.avoidable(removed, ctx.usage, ctx.now, price=ctx.price, partial_reason=partial) if removed
        else {"basis": "no runs to avoid", "note": "The pipelines to change didn't run in the period read."})


# ---- 4.4 / 5 full refreshes


def _sql_full(cells: list[str]) -> list[tuple[int, str]]:
    hits = []
    for n, c in enumerate(cells, 1):
        cell = magics.cell(c)
        texts = [cell.body] if cell.language == "sql" else [m.group(2) for m in _SPARK_SQL.finditer(cell.body)]
        for t in texts:
            for m in _SQL_FULL.finditer(t):
                hits.append((n, _SPACE.sub(" ", m.group(1))[:120]))
    return hits


def full_refreshes(workloads: list[dict[str, Any]], ctx: Context) -> list[dict[str, Any]]:
    out = []
    for w in workloads:
        if w["definition"] != "read" or not w["runs"]["read"] or not w["runs"]["scheduled"]:
            continue
        found: list[dict[str, Any]] = []
        if w["type"] == "Notebook":
            cells = w.get("cells") or []
            for f in notebook_practices.check(cells, scheduled=True, settings=_ONLY_NB007)["findings"]:
                found.append(model.evidence("whole_table_overwrite", f"{f['object']}: {f['rule']} (rule {f['rule_id']})",
                                            "notebook code, read by the notebook best-practice rules",
                                            cell=f["cell"], line=f["line"], rule=f["rule_id"]))
            for cell, text in _sql_full(cells):
                found.append(model.evidence("whole_table_overwrite", f"cell {cell}: {text}", "notebook SQL", cell=cell))
        elif w["type"] == "DataPipeline":
            for a in _copies(w):
                sink = a["type_properties"].get("sink") or {}
                if str(sink.get("tableActionOption") or "").lower() == "overwrite":
                    found.append(model.evidence("copy_overwrite", f"Copy activity {a['name']} overwrites its table on "
                                                "every run", "pipeline definition", activity=a["name"], path=a["path"]))
        if not found:
            continue
        what = "notebook" if w["type"] == "Notebook" else "pipeline"
        out.append(model.make(
            rule="OE-FULL-REFRESH", category="full_refresh", action="optimize_in_place",
            title=f"{w['name']} rewrites whole tables on every scheduled run",
            artifacts=[model.artifact(w, "change")],
            current=f"The {what} ran {w['runs']['scheduled']} time(s) on a schedule in the last {w['runs']['days']} "
                    "days and rewrites every row each time.",
            problem="Unchanged rows are read, rewritten and stored again on every run; each run adds a full new "
                    "set of files until VACUUM.",
            evidence=found + _run_evidence([w]),
            recommendation="Load only what changed: MERGE on the business key, or overwrite only the changed "
                           "partitions (replaceWhere), driven by a watermark column.",
            alternatives=["Keep the full load if the source has no reliable change column and is small; record why.",
                          "Run it less often if consumers don't need every refresh."],
            effort="medium", risk="medium", confidence="medium",
            validation=["Check the source has a reliable change column (modified time, increasing key) or change feed.",
                        "Run the incremental version beside the full one and compare the target tables "
                        "(row counts and content) before switching.",
                        "Measure both runs' time and CU (before and after) to record the real saving."],
            rollback="Switch the schedule back to the full-load version; a full load rebuilds the table.",
            source="full refresh analyzer (OE-001)",
            next_step={"kind": "run_benchmark", "label": "Measure a full and an incremental run",
                       "items": [{"workspace_id": w.get("workspace_id"), "item_id": w["item_id"], "type": w["type"],
                                  "name": w["name"]}]},
            impact=impact.avoidable([w], ctx.usage, ctx.now, price=ctx.price,
                                    partial_reason="How much an incremental load saves depends on how much of "
                                                   "the data changes between runs; it isn't measured yet.")))
    return out


ANALYZERS = (duplicate_notebooks, repeated_copies, full_refreshes)
