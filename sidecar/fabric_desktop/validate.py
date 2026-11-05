"""Validate a notebook: is it ready to run somewhere other than the kernel it was written in?

Two parts, both kept apart from the person's own kernel:

* **Code checks** (``check``) read the cells without running them. They gather what the app
  already checks in separate places: whether the code parses, names used before any cell defines
  them (depgraph), what stops it running on this PC or in Fabric (fabric_run.compute_check),
  packages (envprofile), secrets written in the code (git.secrets), the clock and network calls
  (proof), the parameter cell and the lakehouse it reads and writes.
* **A clean run** (validate_service) runs every code cell from the top in a fresh kernel process,
  with the proof runtime bound so table writes go to a sandbox folder instead of the lakehouse's
  tables. ``run_findings`` turns its result into findings, so a notebook that works only because
  of variables left in the person's kernel fails.

Every finding has a severity (``error``, ``warning`` or ``info``), the cell it is about (numbered
from 1 among all cells, as the notebook shows them) and what to do. ``verdict`` sums them up.
Reading code can't see calls built at run time; the result says so.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from . import depgraph, envprofile, notebook_cells, proof
from .fabric_run import compute_check, uses_spark
from .git import secrets

SEVERITIES = ("error", "warning", "info")
LIMITS = ["Code checks read the code; they can't see names or calls built at run time.",
          "A clean run uses this PC's packages. Spark cells run on this PC's PySpark-compatible engine (DuckDB), "
          "not Fabric's Spark: what it doesn't have stops the run, and results can differ slightly.",
          "Outside the lakehouse's tables, a clean run does what the code does (files on this PC, network calls); "
          "the ones the code checks find are listed before it starts."]

_MAGIC_PIP = re.compile(r"^\s*[%!](pip|conda)\b", re.M)
_NAME_ERROR = re.compile(r"name '([A-Za-z_]\w*)' is not defined")


def finding(severity: str, code: str, title: str, text: str = "", fix: str = "", cell: int | None = None,
            cells: list[dict[str, Any]] | None = None, line: int | None = None, found_by: str = "code") -> dict[str, Any]:
    assert severity in SEVERITIES
    cell_id = (cells[cell - 1].get("cell_id") if cells and cell else None)
    return {"severity": severity, "code": code, "cell": cell, "cell_id": cell_id, "line": line, "title": title,
            "text": text, "fix": fix, "found_by": [found_by]}


def _code_cells(cells: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    return [(n, c) for n, c in enumerate(cells, 1)
            if c.get("type") != "markdown" and (c.get("source") or "").strip()]


def _is_params(c: dict[str, Any]) -> bool:
    return "parameters" in ((c.get("meta") or {}).get("tags") or [])


def check(cells: list[dict[str, Any]], lakehouse: dict[str, Any] | None, runs_on: str | None = None,
          observed: dict[str, dict[str, Any]] | None = None,
          load: Callable[[str], Any] | None = None, resources: str | None = None) -> dict[str, Any]:
    """The code checks. ``runs_on`` is where the notebook is set to run: "pc", "fabric", or None
    when that isn't known (then what stops either place is a warning, not an error). ``load`` reads
    another notebook by name and ``resources`` the notebook's resources folder, so the notebooks and
    Built-in files it runs are checked too (compute_check)."""
    out: list[dict[str, Any]] = []
    graph = depgraph.graph(cells)
    by_n = {info["n"]: info for info in graph["cells"]}

    # Syntax, and names used before any cell defines them.
    for n, info in sorted(by_n.items()):
        if not info.get("parsed", True):
            why = next((d["text"] for d in info.get("dynamic", []) if d["text"].startswith("doesn't parse")), "doesn't parse")
            line = next((d["line"] for d in info.get("dynamic", []) if d["text"].startswith("doesn't parse")), None)
            out.append(finding("error", "syntax", f"Cell {n} {why}", "Nothing in this cell can run until it parses.",
                               "Fix the code on that line.", n, cells, line))
            continue
        dynamic = any(not d["text"].startswith("doesn't parse") for d in info.get("dynamic", []))
        for u in info.get("unresolved", []):
            later = re.search(r"later cell \((\d+)\)", u["text"])
            if later and int(later.group(1)) == n:
                out.append(finding(
                    "error", "defined_later", f"Cell {n} uses {u['name']} before defining it",
                    f"{u['name']} is defined further down the same cell. If the kernel has it now, it's left over from "
                    "an earlier run, so the cell fails in Fabric, in a schedule and after a restart.",
                    f"Define {u['name']} above the line that uses it.", n, cells))
            elif later and u["name"] in info.get("deferred", []):
                m = int(later.group(1))
                out.append(finding(
                    "info", "defined_later", f"Cell {n} has a function that uses a name defined later",
                    f"{u['name']} is defined in cell {m}. A function reads it when it's called, so this works as long "
                    f"as nothing calls the function before cell {m} runs.", "", n, cells))
            elif later:
                m = int(later.group(1))
                out.append(finding(
                    "error", "defined_later", f"Cell {n} uses a name defined later",
                    f"{u['name']} is defined in cell {m}. If the kernel has it now, it's left over from an earlier run, "
                    "so the cell fails in Fabric, in a schedule and after a restart.",
                    f"Move cell {m} above cell {n}, or define {u['name']} before cell {n}.", n, cells))
            elif not dynamic:
                out.append(finding(
                    "warning", "undefined", f"Cell {n} uses a name no cell defines",
                    f"{u['name']} isn't defined or imported by any cell. It could be a typo, a missing import, "
                    "or something defined at run time.", f"Import or define {u['name']} in an earlier cell.", n, cells))

    # Where it can run.
    where = compute_check(cells, lakehouse, load, resources=resources)
    for side, label in (("local", "this PC"), ("fabric", "Fabric")):
        target = {"local": "pc", "fabric": "fabric"}[side]
        sev = "warning" if runs_on is None else ("error" if runs_on == target else "info")
        for r in where[side]["reasons"]:
            if side == "fabric" and r.get("cell") is None and not lakehouse:
                continue  # said once below
            where_text = f"Cell {r['cell']}" if r.get("cell") else "The notebook"
            out.append(finding(sev, f"runs_on_{target}", f"{where_text} can't run on {label}",
                               f"It {r['text']}.", "Set Runs on to the other place, or change the cell." if r.get("cell")
                               else "", r.get("cell"), cells))
        for r in where[side]["unknown"]:
            out.append(finding("warning" if runs_on in (None, target) else "info", f"runs_on_{target}_unknown",
                               f"Cell {r['cell']} may not run on {label}",
                               f"It {r['text']}.", "Prove the notebook to find out, or write the code so it can be "
                               "read (a literal name or SQL text).", r["cell"], cells))

    # Packages.
    env = envprofile.check(cells, observed)
    for m in env["modules"]:
        if m["optional"]:
            continue
        first = m["cells"][0]
        if m["pc"]["status"] == "missing":
            out.append(finding("error" if runs_on != "fabric" else "info", "package_missing_pc",
                               f"{m['module']} isn't installed on this PC", m["pc"].get("text", ""),
                               "Run the notebook in Fabric, or use a library the engine has.", first, cells))
        if m["fabric"]["status"] == "unknown":
            version = m["pc"].get("version")
            out.append(finding("warning" if runs_on != "pc" else "info", "package_unknown_fabric",
                               f"Fabric's version of {m['module']} isn't known",
                               (f"This PC has {m['module']} {version}. " if version else "")
                               + "No Fabric run of this workspace has reported its version yet.",
                               "Run it once in Fabric, or install it there with %pip.", first, cells))
    for n, c in _code_cells(cells):
        if _MAGIC_PIP.search(c.get("source") or ""):
            out.append(finding("warning", "pip", f"Cell {n} installs packages",
                               "%pip is skipped on this PC, whose packages are fixed. In Fabric it installs them for that session only.",
                               "Put the packages in a Fabric environment, so every run has the same ones.", n, cells))

    # Secrets written in the code (shown masked).
    for n, c in _code_cells(cells):
        for hit in secrets.scan_text(c.get("source") or ""):
            out.append(finding("error", "secret", f"Cell {n} has a secret in its code",
                               f"{hit['kind']} on line {hit['line']} ({hit['match']}). Anyone who can read the notebook can use it.",
                               hit["remediation"], n, cells, hit["line"]))

    # The clock, the network, other notebooks: things a clean run can't make repeatable.
    pr = proof.check(cells, lakehouse, load, resources)
    for w in pr["warnings"]:
        title, text = _effect(w["text"])
        out.append(finding("warning", "side_effect", f"Cell {w['cell']} {title}", text, "", w["cell"], cells, w.get("line")))
    for w in pr["notes"]:
        out.append(finding("info", "credentials", f"Cell {w['cell']} uses your credentials",
                           "On this PC and in Fabric it runs as whoever runs it.", "", w["cell"], cells, w.get("line")))

    # Parameters, frozen cells, the lakehouse.
    params = [n for n, c in _code_cells(cells) if _is_params(c)]
    if len(params) > 1:
        out.append(finding("warning", "parameters", "More than one parameter cell",
                           f"Cells {', '.join(map(str, params))} are tagged as parameters. Fabric uses one parameter cell.",
                           "Keep one parameter cell.", params[1], cells))
    elif params:
        names = by_n.get(params[0], {}).get("defines", [])
        out.append(finding("info", "parameters", f"Parameters: cell {params[0]}",
                           ("Pipelines and notebook runs can pass " + ", ".join(names) + ".") if names
                           else "The parameter cell defines no names yet.", "", params[0], cells))
    frozen = [n for n, c in _code_cells(cells) if (c.get("meta") or {}).get("frozen")]
    if frozen:
        out.append(finding("info", "frozen", f"Frozen: cell{'s' if len(frozen) > 1 else ''} {', '.join(map(str, frozen))}",
                           "Frozen cells don't run, here or in a clean run.", "", frozen[0], cells))
    step = notebook_cells.step_cell(cells)
    if step:
        out.append(finding("info", "fabric_step", f"Runs on Fabric: cell {step}",
                           "It runs only on Fabric Spark, after the cells before it run here, so a clean run on this "
                           "PC skips it.", "", step, cells))
    reads = sorted({r["name"] for info in graph["cells"] for r in info["reads"] if r["kind"] == "table"})
    writes = sorted({w["name"] for info in graph["cells"] for w in info["writes"] if w["kind"] == "table"})
    if lakehouse:
        out.append(finding("info", "lakehouse", f"Lakehouse: {lakehouse.get('name') or 'attached'}",
                           f"Reads {_names(reads) or 'no tables'}. Writes {_names(writes) or 'nothing'}."))
    elif reads or writes or any(r["kind"] == "file" for info in graph["cells"] for r in info["reads"]):
        out.append(finding("error", "no_lakehouse", "No lakehouse is attached",
                           "The cells read or write lakehouse tables or files.", "Attach a lakehouse with Add data items."))
    if not _code_cells(cells):
        out.append(finding("info", "empty", "The notebook has no code"))
    plan = run_plan(cells, lakehouse, spark=True)
    spark = next((n for n, c in _code_cells(cells) if uses_spark([c])), None)
    if spark:
        out.append(finding("info", "spark_local", "Spark cells run on this PC's Spark",
                           f"Cell {spark} uses Spark. A clean run runs it with this PC's PySpark-compatible engine "
                           "(DuckDB), reading and writing through the same sandbox as other cells. Fabric's Spark can "
                           "behave differently; Side by side compares the two.", "", spark, cells))
    return {"findings": _sorted(out), "counts": counts(out), "run": plan,
            "tables": {"reads": reads, "writes": writes}, "limits": LIMITS}


def run_plan(cells: list[dict[str, Any]], lakehouse: dict[str, Any] | None, spark: bool = False) -> dict[str, Any]:
    """What stops a clean run (a write that would escape its sandbox) and what it can't contain
    (network calls, files on this PC, other notebooks), which the person confirms first.

    ``spark`` True lets Spark cells run: on this PC ``spark`` (spark_local) reads tables through
    lakehouse.open_table and writes them through lakehouse.write_table, which the proof runtime pins
    and redirects like any other cell's. With ``spark`` False (local pipeline steps) Spark cells stop the run."""
    pr = proof.check(cells, lakehouse)
    step = notebook_cells.step_cell(cells)  # a clean run skips it
    blockers = [b for b in pr["blockers"] if b.get("cell") and b["cell"] != step and _escapes(b["text"])]
    if not spark:
        first = next((n for n, c in enumerate(cells, 1) if n != step and c.get("type") != "markdown"
                      and uses_spark([c])), None)
        if first is not None:
            blockers.insert(0, {"cell": first, "cell_id": cells[first - 1].get("cell_id"),
                                "text": "uses Spark, which this run doesn't take yet. Use the lakehouse module "
                                        "(read_table, write_table, sql)."})
    uncontained = [{"cell": w["cell"], "cell_id": w.get("cell_id"), "line": w.get("line"), "text": _effect(w["text"])[1]}
                   for w in pr["warnings"] if w["cell"] != step and not w["text"].startswith(("uses the clock", "may not run on"))]
    # "may not run on this PC" (compute_check's Unknown) isn't something the run lets out: running it is how to find out.
    return {"ok": not blockers, "blockers": blockers, "uncontained": uncontained,
            "sandbox": bool(lakehouse)}


def _escapes(text: str) -> bool:
    # proof.check's blockers that are about the run itself, not about comparing with Fabric.
    return "outside the proof's folder" in text or "can't pin or contain" in text


def run_findings(cells: list[dict[str, Any]], run: dict[str, Any], static: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Findings from a clean run: the cell that failed, merged with a code check that explains it."""
    out: list[dict[str, Any]] = []
    failed = run.get("failed_cell")
    if failed:
        err = run.get("error") or {}
        ename, evalue = err.get("ename") or "Error", err.get("evalue") or ""
        name = (_NAME_ERROR.search(evalue) or [None, None])[1] if ename == "NameError" else None
        known = next((f for f in static if f["cell"] == failed and name and f["code"] in ("defined_later", "undefined")
                      and name in f["text"]), None)
        if known is not None:
            known["found_by"] = sorted(set(known["found_by"]) | {"run"})
            if known["severity"] != "error":
                known["severity"] = "error"
        else:
            stopped = run.get("stopped_by")
            out.append(finding("error", "run_failed", f"Cell {failed} failed in the clean run",
                               f"{ename}: {evalue}".strip(": ") + (f" Stopped by {stopped}." if stopped else ""),
                               "Fix the cell, then validate again.", failed, cells, found_by="run"))
    return out


def verdict(findings: list[dict[str, Any]], run: dict[str, Any] | None) -> str:
    """ready, ready_with_warnings, not_ready; or checked (code checks only, no clean run yet)."""
    c = counts(findings)
    if c["error"]:
        return "not_ready"
    if run is None or run.get("status") != "ok":
        return "checked"
    return "ready_with_warnings" if c["warning"] else "ready"


def counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    return {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}


def _sorted(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    return sorted(findings, key=lambda f: (rank[f["severity"]], f["cell"] or 0))


def _names(names: list[str], most: int = 6) -> str:
    return ", ".join(names[:most]) + (f" and {len(names) - most} more" if len(names) > most else "")


_EFFECTS = [
    ("calls a network service", "calls a network service", "A clean run really makes the call."),
    ("writes or changes files on the machine", "writes files on this PC", "A clean run really writes or changes them."),
    ("runs another notebook", "runs another notebook",
     "A clean run runs it too, in the same fresh kernel, with its table writes in the same sandbox."),
    ("uses the clock", "reads the clock or random values", "Two runs can write different rows."),
]


def _effect(text: str) -> tuple[str, str]:
    """A title and text for one of proof.check's warnings, said for a clean run instead of a proof."""
    for prefix, title, said in _EFFECTS:
        if text.startswith(prefix):
            return title, said
    return text.split(";", 1)[0], ""


def to_markdown(record: dict[str, Any]) -> str:
    """A readable report of a finished validation, for Save report."""
    titles = {"ready": "Ready", "ready_with_warnings": "Ready, with warnings", "not_ready": "Not ready",
              "checked": "Code checks only", None: "Not finished"}
    lines = [f"# Validate: {record['name']}", "",
             f"**{titles.get(record.get('verdict'), record.get('verdict'))}**. {record.get('summary', '')}", "",
             f"- Started: {record.get('started_at')}", f"- Finished: {record.get('finished_at')}",
             f"- Code revision: {record.get('code', {}).get('revision', '')[:16]}", ""]
    for sev, head in (("error", "Errors"), ("warning", "Warnings"), ("info", "Info")):
        rows = [f for f in record.get("findings", []) if f["severity"] == sev]
        if rows:
            lines.append(f"## {head}")
            lines.append("")
            for f in rows:
                fix = f" {f['fix']}" if f.get("fix") else ""
                lines.append(f"- **{f['title']}.** {f['text']}{fix}")
            lines.append("")
    if record.get("cells"):
        lines += ["## Clean run", "", "| Cell | Result | Seconds |", "| --- | --- | --- |"]
        lines += [f"| {c['cell']} | {c['status']} | {c.get('seconds') if c.get('seconds') is not None else ''} |"
                  for c in record["cells"]]
        lines.append("")
    lines += ["## Limits", ""] + [f"- {t}" for t in record.get("limits", LIMITS)]
    return "\n".join(lines) + "\n"
