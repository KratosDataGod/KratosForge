"""Estate X-ray: one pass over every lakehouse you can reach, and what in it needs attention.

It reads only what OneLake and Fabric already know: the folders under Tables, each Delta
table's log (files, sizes, last write, last OPTIMIZE and VACUUM), workspace roles, and the
cost of runs this app started. Each finding says what is wrong, why it matters and what fixes it.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from . import access
from .redact import describe

_log = logging.getLogger(__name__)

MAX_TABLES = 500          # a first look, not an inventory: the biggest estates are read in part
STALE_DAYS = 30
BIG_SMALL_FILES = 200     # this many small files is slow for every reader
VACUUM_VERSIONS = 30      # this many versions since the last VACUUM (or ever) is worth one
SEVERITY = {"high": 0, "medium": 1, "low": 2}


def _days_since(iso: str | None, now: dt.datetime) -> int | None:
    if not iso:
        return None
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return (now - t).days


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def table_findings(t: dict[str, Any], h: dict[str, Any], now: dt.datetime) -> list[dict[str, Any]]:
    """What one table's Delta log says needs doing."""
    where = {k: t[k] for k in ("workspace_id", "workspace_name", "lakehouse_id", "lakehouse_name", "schema", "table")}
    name = f"{t['schema']}.{t['table']}" if t.get("schema") else t["table"]
    out = []
    if not h.get("exists"):
        return [where | {"kind": "unidentified", "severity": "medium", "title": f"{name} isn't a Delta table",
                         "detail": "It sits under Tables, so Fabric lists it as Unidentified and no engine can read it as a table. "
                                   "Move it to Files, or rewrite it as Delta.", "action": "open"}]
    small = [a for a in h["advice"] if "small files" in a]
    if small:
        out.append(where | {
            "kind": "small_files", "severity": "high" if h["files"] >= BIG_SMALL_FILES else "medium",
            "title": f"{name} has {h['files']:,} small files",
            "detail": f"Averaging {_size(h['avg_file_bytes'])} each. Every query opens them one by one; "
                      f"Optimize compacts them and applies V-Order.",
            "action": "optimize", "files": h["files"], "bytes": h["bytes"]})
    idle = _days_since(h.get("last_write"), now)
    if idle is not None and idle >= STALE_DAYS:
        out.append(where | {"kind": "stale", "severity": "low", "title": f"{name} hasn't changed in {idle} days",
                            "detail": "If something should still load it, that load has stopped. "
                                      "If nothing should, it may be safe to retire.",
                            "action": "open", "days": idle})
    behind = h.get("versions_since_vacuum", 0)
    if behind >= VACUUM_VERSIONS:
        never = not h.get("last_vacuum")
        out.append(where | {"kind": "vacuum", "severity": "low", "versions": behind,
                            "title": f"{name} has never been vacuumed ({behind} versions)" if never
                                     else f"{name} has {behind} versions since its last vacuum",
                            "detail": "Files from old versions are still stored and paid for. Vacuum deletes the ones "
                                      "older than the history this table keeps, so restore still works inside it.",
                            "action": "vacuum"})
    return out


def access_findings(workspaces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Guests and apps with Admin, from every workspace's roles (one finding per person)."""
    review = access.review([{"key": w["id"], "label": w["name"], "assignments": w["assignments"]}
                            for w in workspaces if w.get("assignments")])
    names = {w["id"]: w["name"] for w in workspaces}
    out = []
    for p in review["people"]:
        roles = p["roles"]
        where = ", ".join(f"{names[k]} ({v['role']})" for k, v in roles.items())
        if p["guest"]:
            out.append({"kind": "access", "severity": "medium", "title": f"Guest {p['name']} has access to {len(roles)} workspace{'s' if len(roles) != 1 else ''}",
                        "detail": f"An account from another organization: {where}.", "action": "access",
                        "workspace_id": next(iter(roles)), "person": p["name"]})
        admin = [k for k, v in roles.items() if v["role"] == "Admin"]
        if p["type"] == "ServicePrincipal" and admin:
            out.append({"kind": "access", "severity": "medium", "title": f"App {p['name']} is Admin in {len(admin)} workspace{'s' if len(admin) != 1 else ''}",
                        "detail": f"Admin can delete the workspace and change who has access. {where}.",
                        "action": "access", "workspace_id": admin[0], "person": p["name"]})
    return out


def cost_findings(report: dict[str, Any], local_notebooks: set[str]) -> list[dict[str, Any]]:
    """Fabric notebooks that cost the most: on this PC they would cost nothing."""
    out = []
    for item in report.get("by_item", []):  # costs.report rows
        kind, name = item["kind"], item["name"]
        if kind != "Notebook run" or item["cost"] < 0.01:
            continue
        out.append({"kind": "cost", "severity": "medium" if item["cost"] >= 10 else "low",
                    "title": f"{name} cost about ${item['cost']:,.2f} in Fabric in {report['days']} days (estimated)",
                    "detail": f"{item['fabric_runs']} runs, about {item['cu_hours']:,.1f} CU hours, estimated from run time "
                              "(Monitor > Cost shows how). Set it to run on this PC and those runs use no Fabric compute.",
                    "action": "run_local" if name in local_notebooks else "cost", "notebook": name, "cost": item["cost"]})
    return sorted(out, key=lambda f: -f["cost"])[:5]


def rank(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weight = {"small_files": 0, "unidentified": 1, "access": 2, "cost": 3, "stale": 4, "vacuum": 5, "empty": 6, "adopt": 7}
    return sorted(findings, key=lambda f: (SEVERITY[f["severity"]], weight.get(f["kind"], 9), -f.get("files", 0),
                                           f["title"].lower()))


class Scanner:
    """The walk over the estate. Every Fabric and OneLake call comes in from outside, so tests
    can run it over local folders.

    ``fabric``: list_workspaces, list_lakehouses, list_role_assignments.  ``files(ws, lh, dir)``:
    a folder's children ({"name", "is_dir"}).  ``health(ws, lh, schema, table)``: maintenance.health."""

    def __init__(self, fabric: Any, files: Callable[[str, str, str], list[dict[str, Any]]],
                 health: Callable[[str, str, str | None, str], dict[str, Any]],
                 progress: Callable[[dict[str, Any]], None] = lambda _p: None, workers: int = 8,
                 gate: threading.Event | None = None):
        self.fabric, self.files, self.health, self.progress, self.workers = fabric, files, health, progress, workers
        # Cleared to pause: every worker waits here before its next call to Fabric or OneLake.
        if gate is None:
            gate = threading.Event()
            gate.set()
        self.gate = gate

    def _tables(self, lh: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        """Every folder under Tables that is or should be a table, and whether Files has anything."""
        base = {k: lh[k] for k in ("workspace_id", "workspace_name", "lakehouse_id", "lakehouse_name")}
        out = []
        for top in [e for e in self.files(lh["workspace_id"], lh["lakehouse_id"], "Tables") if e["is_dir"]]:
            inner = self.files(lh["workspace_id"], lh["lakehouse_id"], f"Tables/{top['name']}")
            if any(e["name"] == "_delta_log" for e in inner):
                out.append(base | {"schema": None, "table": top["name"]})  # a lakehouse without schemas
                continue
            out += [base | {"schema": top["name"], "table": e["name"]} for e in inner if e["is_dir"]]
        try:
            has_files = bool(self.files(lh["workspace_id"], lh["lakehouse_id"], "Files"))
        except Exception as exc:  # noqa: BLE001 - unknown: assume it has files, so no "no files" finding is made up
            _log.warning("Couldn't list Files in %s / %s: %s", lh.get("workspace_name"), lh.get("lakehouse_name"), describe(exc))
            has_files = True
        return out, has_files

    def scan(self, now: dt.datetime | None = None) -> dict[str, Any]:
        now = now or dt.datetime.now(dt.timezone.utc)
        workspaces = [{"id": w["id"], "name": w["displayName"]} for w in self.fabric.list_workspaces()]
        self.progress({"phase": "Finding lakehouses", "done": 0, "total": len(workspaces)})
        skipped: list[str] = []

        def lakehouses(ws: dict[str, str]) -> list[dict[str, Any]]:
            self.gate.wait()
            try:
                ws["assignments"] = self.fabric.list_role_assignments(ws["id"])
            except Exception:
                ws["assignments"] = []  # not an admin there: roles are hidden
            try:
                return [{"workspace_id": ws["id"], "workspace_name": ws["name"], "lakehouse_id": lh["id"],
                         "lakehouse_name": lh["displayName"]} for lh in self.fabric.list_lakehouses(ws["id"])]
            except Exception as exc:  # noqa: BLE001 - a paused capacity, or no access: listed as skipped
                _log.warning("X-ray skipped workspace %s: %s", ws["name"], describe(exc))
                skipped.append(ws["name"])
                return []

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            lhs = [lh for chunk in pool.map(lakehouses, workspaces) for lh in chunk]
            self.progress({"phase": "Listing tables", "done": 0, "total": len(lhs)})
            listed = list(pool.map(lambda lh: self._safe_tables(lh, skipped), lhs))
            tables = [t for ts, _ in listed for t in ts]
            partial = len(tables) > MAX_TABLES
            tables = tables[:MAX_TABLES]
            done = [0]

            def read(t: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
                self.gate.wait()
                try:
                    h = self.health(t["workspace_id"], t["lakehouse_id"], t["schema"], t["table"])
                except Exception as exc:
                    h = {"exists": True, "advice": [], "files": 0, "bytes": 0, "error": str(exc)}
                done[0] += 1
                if done[0] % 10 == 0:
                    self.progress({"phase": "Reading table logs", "done": done[0], "total": len(tables)})
                return t, h

            self.progress({"phase": "Reading table logs", "done": 0, "total": len(tables)})
            healths = list(pool.map(read, tables))

        findings: list[dict[str, Any]] = []
        per_lh: dict[str, dict[str, Any]] = {lh["lakehouse_id"]: lh | {"tables": 0, "bytes": 0, "files": 0, "issues": 0}
                                             for lh in lhs}
        for t, h in healths:
            found = table_findings(t, h, now)
            findings += found
            row = per_lh[t["lakehouse_id"]]
            row["tables"] += 1
            row["bytes"] += h.get("bytes") or 0
            row["files"] += h.get("files") or 0
            row["issues"] += len(found)
        for lh, (ts, has_files) in zip(lhs, listed):
            if not ts and not has_files:
                findings.append({k: lh[k] for k in ("workspace_id", "workspace_name", "lakehouse_id", "lakehouse_name")} | {
                    "kind": "empty", "severity": "low", "title": f"{lh['lakehouse_name']} is empty",
                    "detail": f"No tables and no files in {lh['workspace_name']}. Delete it if nothing will use it.",
                    "action": "open"})
        findings += access_findings(workspaces)
        return {"scanned": now.isoformat(timespec="seconds"), "workspaces": len(workspaces), "lakehouses": len(lhs),
                "tables": len(tables), "partial": partial, "skipped": sorted(set(skipped)),
                "bytes": sum(h.get("bytes") or 0 for _, h in healths),
                "lakehouse_rows": sorted(per_lh.values(), key=lambda r: (-r["issues"], r["lakehouse_name"].lower())),
                "findings": rank(findings)}

    def _safe_tables(self, lh: dict[str, Any], skipped: list[str]) -> tuple[list[dict[str, Any]], bool]:
        self.gate.wait()
        try:
            return self._tables(lh)
        except Exception as exc:  # noqa: BLE001 - the scan goes on; the report lists it as skipped
            _log.warning("X-ray skipped %s / %s: %s", lh["workspace_name"], lh["lakehouse_name"], describe(exc))
            skipped.append(f"{lh['workspace_name']} / {lh['lakehouse_name']}")
            return [], True


def counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        out[f["kind"]] = out.get(f["kind"], 0) + 1
    return out


RULE_SET = "xray-1"
_FIXES = {"optimize": ("optimize", "Compact the small files and apply V-Order (X-ray > Optimize)"),
          "vacuum": ("vacuum", "Vacuum the files older than the history the table keeps (X-ray > Vacuum)")}
_EVIDENCE = ("files", "bytes", "days", "versions")


def optimize_records(result: dict[str, Any]) -> dict[str, Any]:
    """A finished scan as one Optimize analysis run (Shared.record_analysis): the lakehouses it
    looked at, its lakehouse and table findings, and a recommendation for each one X-ray can fix.
    Access findings name people and cost findings are estimates for this PC, so neither is shared."""
    lakehouses = {r["lakehouse_id"]: r for r in result.get("lakehouse_rows", [])}
    findings, recommendations = [], []
    for f in result.get("findings", []):
        if f.get("kind") in ("access", "cost") or not f.get("lakehouse_id"):
            continue
        target = f"{f['schema']}.{f['table']}" if f.get("schema") else f.get("table") or ""
        where = {"rule_id": f"xray.{f['kind']}", "workspace_id": f["workspace_id"], "item_id": f["lakehouse_id"],
                 "target": target}
        evidence = {k: f[k] for k in _EVIDENCE if f.get(k) is not None}
        findings.append(where | {"rule_version": RULE_SET, "severity": f["severity"], "title": f["title"],
                                 "detail": f.get("detail"), "evidence": json.dumps(evidence) if evidence else None})
        if f.get("action") in _FIXES:
            kind, reason = _FIXES[f["action"]]
            recommendations.append(where | {"kind": kind, "reason": reason, "confidence": "high"})
    artifacts = [{"workspace_id": r["workspace_id"], "item_id": r["lakehouse_id"], "item_type": "Lakehouse",
                  "display_name": r["lakehouse_name"]} for r in lakehouses.values()]
    run = {"run_id": f"xray-{result['scanned']}", "finished_at": result["scanned"],
           "status": "partial" if result.get("partial") else "complete", "scope": "estate", "rule_set_version": RULE_SET}
    return {"run": run, "artifacts": artifacts, "findings": findings, "recommendations": recommendations}
