"""Performance controls on table layout (FEA-204), and the evidence they read.

Every control here decides from the read the Estate X-ray already makes (xray.Scanner): the folders
under each lakehouse's Tables, and each Delta table's log, read with maintenance.health and
delta_profile.features_used. That is OneLake's DFS API with the signed-in person's own access; no new
Fabric endpoint, and no data file is opened (only the Delta log):

- ``lakehouse.tables`` (measured): per table, its number of data files, their total size, the versions
  since its last VACUUM, and the Delta features it uses. The Delta log is the table's own record of
  what was written, so these numbers are measured, not estimated.

A lakehouse whose Tables folder couldn't be listed gives no record, so its controls are not
evaluated. A table whose log couldn't be read, or that was past the read budget, keeps the
lakehouse's controls from passing: they fail when a table that was read fails, and are otherwise
not evaluated. Folders under Tables that aren't Delta tables are left out (X-ray reports them).

The thresholds are KratosForge's, the same ones X-ray and table health use (maintenance): 20 or more
files averaging under 32 MB is worth compacting, and 30 or more versions since the last VACUUM is
worth one. Microsoft's guidance asks for compaction and regular VACUUM but gives neither number.
Which readers can read a Delta feature is delta_profile.MATRIX, from Microsoft's Delta Lake
interoperability page.

Not checked here, because nothing the app reads says it:

- V-Order on the tables Direct Lake reads. Which tables a Direct Lake model reads is in the semantic
  model's definition, which the assessment doesn't read, and the Delta log only hints at V-Order (the
  files' Parquet footers say for sure, one read per file).
- Full refreshes and duplicate work are FEA-DE-001, FEA-PERF-001 and FEA-PERF-002 (catalog).
"""

from __future__ import annotations

from typing import Any

from .. import delta_profile as dp
from .. import maintenance
from . import controls as ct
from . import evidence as ev

DELTA_LOG = "Delta logs under Tables in OneLake (the Estate X-ray's read: maintenance.health, delta_profile)"
SMALL_FILE_BYTES = maintenance.SMALL_FILE_BYTES
SMALL_FILE_COUNT = maintenance.SMALL_FILE_COUNT
VACUUM_VERSIONS = maintenance.VERSIONS_BEFORE_VACUUM
# The readers every lakehouse table has: Fabric creates a SQL analytics endpoint for each lakehouse,
# and Direct Lake is what the modern Delta profile is chosen for (delta_profile.DEFAULT_READERS).
READERS = dp.DEFAULT_READERS


# ---- evidence


def table_facts(name: str, health: dict[str, Any] | None, features: list[str] | None,
                error: str | None = None) -> dict[str, Any]:
    """One table as the controls see it: numbers from its Delta log, or why it wasn't read."""
    if error or health is None:
        return {"name": name, "read": False, "problem": error or "Its Delta log wasn't read."}
    if not health.get("exists"):
        return {"name": name, "read": True, "delta": False}
    return {"name": name, "read": True, "delta": True, "files": int(health.get("files") or 0),
            "bytes": int(health.get("bytes") or 0), "avg_file_bytes": int(health.get("avg_file_bytes") or 0),
            "versions_since_vacuum": int(health.get("versions_since_vacuum") or 0),
            "last_vacuum": health.get("last_vacuum"), "features": sorted(features or [])}


def table_layout(ws_id: str, lakehouses: list[dict[str, Any]], read: dict[str, dict[str, Any] | None], *,
                 collected_at: str) -> list[dict[str, Any]]:
    """One measured ``lakehouse.tables`` record per lakehouse whose Tables folder was listed.

    ``read[lakehouse id]`` is ``{"tables": [table_facts ...], "past_budget": n, "staging": bool}``,
    or ``None`` (or absent) when the folder couldn't be listed: no record, so not evaluated."""
    out = []
    for lh in lakehouses:
        got = read.get(str(lh.get("id")))
        if got is None:
            continue
        tables = sorted(got.get("tables") or [], key=lambda t: str(t.get("name")))
        subj = ev.subject("item", str(lh["id"]), lh.get("displayName"), workspace_id=ws_id, item_type="Lakehouse")
        out.append(ev.record("lakehouse.tables", subj, source=DELTA_LOG, confidence="measured",
                             collected_at=collected_at,
                             payload={"tables": tables, "past_budget": int(got.get("past_budget") or 0),
                                      "staging": bool(got.get("staging"))}))
    return out


# ---- controls

_COMPACT_DOC = {"title": "Delta Lake table optimization and V-Order",
                "url": "https://learn.microsoft.com/fabric/data-engineering/delta-optimization-and-v-order"}
_MAINT_DOC = {"title": "Use table maintenance feature to manage delta tables in Fabric",
              "url": "https://learn.microsoft.com/fabric/data-engineering/lakehouse-table-maintenance"}
_INTEROP_DOC = {"title": "Delta Lake table format interoperability",
                "url": "https://learn.microsoft.com/fabric/fundamentals/delta-lake-interoperability"}
_SQL_LIMITS_DOC = {"title": "Limitations of Microsoft Fabric Data Warehouse",
                   "url": "https://learn.microsoft.com/fabric/data-warehouse/limitations"}


def _layout(ctx: ct.Context) -> tuple[str, dict[str, Any]]:
    r = ctx.one("lakehouse.tables")
    assert r is not None  # declared evidence is present before a control runs
    return r["id"], r["payload"]


def _delta(p: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in p.get("tables") or [] if t.get("read") and t.get("delta")]


def _has_tables(ctx: ct.Context) -> bool | None:
    """Not applicable to Dataflow Gen2's own staging lakehouse, or to a lakehouse with no tables."""
    p = _layout(ctx)[1]
    if p.get("staging"):
        return False
    unread = any(not t.get("read") for t in p.get("tables") or []) or p.get("past_budget")
    return bool(_delta(p) or unread)


def _list(names: list[str], limit: int = 5) -> str:
    shown = names[:limit]
    more = len(names) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


def _unread(p: dict[str, Any], what: str) -> dict[str, Any] | None:
    """Not evaluated when a table's log wasn't read: the problem could be in it."""
    names = [str(t["name"]) for t in p.get("tables") or [] if not t.get("read")]
    past = int(p.get("past_budget") or 0)
    if not names and not past:
        return None
    parts = []
    if names:
        parts.append(f"{len(names)} table{'s' if len(names) != 1 else ''} couldn't be read ({_list(names)})")
    if past:
        parts.append(f"{past} table{'s' if past != 1 else ''} past the assessment's read budget weren't read")
    return ct.not_evaluated(f"No table that was read has {what}, but " + ", and ".join(parts) + ".",
                            needs="the Delta logs of " + (_list(names) if names else f"the other {past} tables"))


def _small_files(ctx: ct.Context) -> dict[str, Any]:
    """FEA-PERF-003, severity medium: every read of a table opens each of its files, so many small
    files make every query, refresh and Direct Lake load slower and use more capacity than the same
    data in fewer files. The data stays right and nothing fails, so it isn't high; it costs every
    reader every time, so it is more than low. Measured from the Delta log."""
    rid, p = _layout(ctx)
    tables = _delta(p)
    hits = [t for t in tables if t["files"] >= SMALL_FILE_COUNT and t["avg_file_bytes"] < SMALL_FILE_BYTES]
    if hits:
        hits.sort(key=lambda t: -t["files"])
        shown = [f"{t['name']} ({t['files']:,} files averaging {_mb(t['avg_file_bytes'])})" for t in hits]
        return ct.failed([rid], f"{len(hits)} table{'s have' if len(hits) != 1 else ' has'} many small files: "
                                f"{_list(shown)}.",
                         detail={"tables": len(hits), "files": sum(t["files"] for t in hits)})
    return _unread(p, "many small files") or ct.passed(
        [rid], f"None of the {len(tables)} table{'s' if len(tables) != 1 else ''} has {SMALL_FILE_COUNT} or more files "
               f"averaging under {SMALL_FILE_BYTES // 1024 // 1024} MB.")


def _vacuum(ctx: ct.Context) -> dict[str, Any]:
    """FEA-PERF-004, severity low: files that old versions replaced stay in OneLake until VACUUM,
    taking storage and making the table's folders slower to list and copy. Reads of the current
    version don't open them and nothing breaks, so it is low. Measured from the Delta log: the
    versions since its last VACUUM (all of them, when none is in the last 200 commits)."""
    rid, p = _layout(ctx)
    tables = _delta(p)
    hits = [t for t in tables if t["versions_since_vacuum"] >= VACUUM_VERSIONS]
    if hits:
        hits.sort(key=lambda t: -t["versions_since_vacuum"])
        shown = [f"{t['name']} ({t['versions_since_vacuum']:,} versions"
                 + ("" if t.get("last_vacuum") else ", never vacuumed") + ")" for t in hits]
        return ct.failed([rid], f"{len(hits)} table{'s have' if len(hits) != 1 else ' has'} {VACUUM_VERSIONS} or more "
                                f"versions since the last VACUUM: {_list(shown)}.", detail={"tables": len(hits)})
    return _unread(p, "a VACUUM backlog") or ct.passed(
        [rid], f"Every one of the {len(tables)} table{'s' if len(tables) != 1 else ''} has fewer than "
               f"{VACUUM_VERSIONS} versions since its last VACUUM.")


def _cells(t: dict[str, Any], state: str) -> list[str]:
    return [f for f in t.get("features") or [] if f in dp.MATRIX and any(dp.MATRIX[f][r] == state for r in READERS)]


def _features(ctx: ct.Context) -> dict[str, Any]:
    """FEA-PERF-005, severity medium: a table that uses a Delta feature Fabric's SQL analytics endpoint
    or Direct Lake can't read is missing, or fails, for every SQL query, report and model that reads
    it there, while Spark still reads it. The data is intact and Spark works, and fixing it means
    rewriting the table, so medium rather than high. Verified from the table's own settings and
    protocol in its Delta log; which reader reads which feature is from Microsoft's Delta Lake
    interoperability page (delta_profile.MATRIX)."""
    rid, p = _layout(ctx)
    tables = _delta(p)
    readers = " or ".join(dp.READERS[r] for r in READERS)
    hits = [(t, _cells(t, dp.CANT)) for t in tables]
    hits = [(t, f) for t, f in hits if f]
    if hits:
        shown = [f"{t['name']} ({', '.join(f)})" for t, f in hits]
        return ct.failed([rid], f"{len(hits)} table{'s use' if len(hits) != 1 else ' uses'} a Delta feature {readers} "
                                f"can't read: {_list(shown)}.", detail={"tables": len(hits)})
    unsure = [(t, _cells(t, dp.UNKNOWN)) for t in tables]
    unsure = [(t, f) for t, f in unsure if f]
    if unsure:
        shown = [f"{t['name']} ({', '.join(f)})" for t, f in unsure]
        return ct.not_evaluated(f"No Microsoft source says whether {readers} can read the Delta features of "
                                f"{_list(shown)}.", needs="a Microsoft source on those features")
    return _unread(p, f"a Delta feature {readers} can't read") or ct.passed(
        [rid], f"None of the {len(tables)} table{'s' if len(tables) != 1 else ''} uses a Delta feature {readers} "
               "can't read.")


def _t(why: str, solution: str, steps: list[str], benefit: str, effort: str, risk: str, validation: str,
       rollback: str) -> dict[str, Any]:
    return {"why": why, "solution": solution, "steps": steps, "benefit": benefit, "effort": effort, "risk": risk,
            "validation": validation, "rollback": rollback}


_KF_THRESHOLD = "The threshold is KratosForge's, the one X-ray uses, because Microsoft gives no number."

CONTROLS = (
    ct.Control(
        id="FEA-PERF-003", version="1.0.0", dimension="performance", title="Tables with many small files",
        severity="medium", requirement="kratosforge_recommendation", scope="item", item_types=("Lakehouse",),
        evidence_kinds=("lakehouse.tables",), evaluate=_small_files, applies=_has_tables,
        references=(_COMPACT_DOC, _MAINT_DOC),
        description=f"A table in the lakehouse has {SMALL_FILE_COUNT} or more data files averaging under "
                    f"{SMALL_FILE_BYTES // 1024 // 1024} MB, measured from its Delta log. " + _KF_THRESHOLD,
        remediation=_t("Every read opens each of a table's files. Many small files make queries, refreshes and "
                       "Direct Lake loads slower and use more capacity than the same rows in fewer, larger files.",
                       "Compact the table with OPTIMIZE (Fabric's table maintenance, or X-ray's Optimize), and write "
                       "fewer, larger batches, or turn on optimize write for the job that writes it.",
                       ["Open the evidence: the tables, their file counts and average file size.",
                        "Run table maintenance (OPTIMIZE) on each, outside busy hours.",
                        "Look at the job that writes the table: many small appends make small files again.",
                        "Assess again."],
                       "Faster reads and less capacity per read. How much is estimated, and Unknown until a query "
                       "or refresh is timed before and after.", "low", "low",
                       "The table's file count drops and its rows are the same; the control passes.",
                       "OPTIMIZE adds a version and changes no rows. Restore the earlier version if you need its "
                       "file layout back.")),
    ct.Control(
        id="FEA-PERF-004", version="1.0.0", dimension="performance", title="VACUUM backlog",
        severity="low", requirement="kratosforge_recommendation", scope="item", item_types=("Lakehouse",),
        evidence_kinds=("lakehouse.tables",), evaluate=_vacuum, applies=_has_tables, references=(_MAINT_DOC,),
        description=f"A table in the lakehouse has {VACUUM_VERSIONS} or more versions since its last VACUUM, or has "
                    "never been vacuumed, measured from its Delta log. " + _KF_THRESHOLD,
        remediation=_t("Files that later versions replaced stay in OneLake until VACUUM removes them. They take "
                       "storage and make the table's folders slower to list, copy and back up.",
                       "Vacuum the table regularly with a retention of at least 7 days, Fabric's default, so time "
                       "travel and running readers keep the files they need.",
                       ["Open the evidence: the tables and their versions since the last VACUUM.",
                        "Check how many days of history each table must keep for restore.",
                        "Run table maintenance with VACUUM and that retention. X-ray's Vacuum shows what it removes.",
                        "Schedule it, for example weekly.", "Assess again."],
                       "Storage held by old files is freed. How much is estimated, and Unknown until VACUUM's dry run "
                       "lists them. It is storage freed, not money saved.", "low", "medium",
                       "Restore inside the kept history still works, and the control passes.",
                       "VACUUM can't be undone: files past the retention are gone. Keep a retention long enough "
                       "for any restore you might need.")),
    ct.Control(
        id="FEA-PERF-005", version="1.0.0", dimension="performance",
        title="Table uses a Delta feature the SQL endpoint or Direct Lake can't read", severity="medium",
        requirement="kratosforge_recommendation", scope="item", item_types=("Lakehouse",),
        evidence_kinds=("lakehouse.tables",), evaluate=_features, applies=_has_tables,
        references=(_INTEROP_DOC, _SQL_LIMITS_DOC),
        description="A table's Delta settings or protocol turn on a feature that Fabric's SQL analytics endpoint or "
                    "Power BI Direct Lake can't read, such as column mapping by ID. Not evaluated when no source "
                    "says whether they read it (V2 checkpoints). Python notebooks, pipelines and eventstreams "
                    "can't read deletion vectors or column mapping either, but whether they read this table "
                    "isn't known, so that isn't checked.",
        remediation=_t("Fabric creates a SQL analytics endpoint for every lakehouse, and reports and models read "
                       "tables through it or through Direct Lake. A table they can't read is missing or fails for "
                       "them, while Spark still reads it.",
                       "Rewrite the table without that feature (for column mapping by ID, with column mapping by "
                       "name, or none), and set the job that writes it not to turn the feature on again.",
                       ["Open the evidence: the tables and the features they use.",
                        "Find the job that creates or writes each table, and the setting that turns the feature on.",
                        "Write a copy of the table from Spark without that feature, and compare row counts.",
                        "Swap readers to the copy, or replace the table in a quiet window.", "Assess again."],
                       "The table reads in the SQL endpoint and Direct Lake again. The gain is availability, not "
                       "capacity freed.", "medium", "medium",
                       "A query on the SQL analytics endpoint returns the table's rows, and the control passes.",
                       "Keep the original table until the copy is confirmed, and point readers back at it.")),
)
