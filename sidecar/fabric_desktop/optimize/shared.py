"""Reading and writing Optimize's shared tables, safely with many installations at once.

Every row gets a ``record_id`` worked out from what it records, so writing the same thing twice
(a retry, a second sync, two PCs that found the same problem) adds one row. Writes go in batches
of at most ``BATCH`` rows, one insert-only MERGE commit each, retried with backoff after a lost
commit race. Nothing here updates or deletes a row.

- **Findings.** A problem has a ``fingerprint`` (rule, artifact, target), the same on every PC.
  Each observation is a row; reading them back gives one finding per fingerprint, with when it
  was first and last seen and by how many runs.
- **Runs.** An analysis run's row is written last. Readers count only findings from runs that
  have a row, so a run that failed part way shows nothing until it is recorded again with the
  same run id, which writes only the rows still missing.
- **Recommendations.** One per fingerprint and kind, opened once whoever sees it first. Its state
  changes are events; the latest event wins (each change is stamped later than the state it
  changed), ties broken by record id so every PC agrees.
- **Workspaces.** Reads take the workspaces the person can open and leave out everything about
  items elsewhere. This keeps the app from showing what the person couldn't see in Fabric; it is
  not a security boundary, because anyone who can read the lakehouse can read every row.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any, Iterable

import polars as pl

from . import schema, setup
from .installation import Installation

BATCH = 5000

OPEN, ACCEPTED, APPLIED, DISMISSED, VERIFIED, SUPERSEDED = (
    "open", "accepted", "applied", "dismissed", "verified", "superseded")
# Which state a recommendation may move to from each state. "open" again means it came back
# (dismissed by mistake, or a fix that didn't hold).
TRANSITIONS: dict[str | None, set[str]] = {
    None: {OPEN},
    OPEN: {ACCEPTED, APPLIED, DISMISSED, SUPERSEDED},
    ACCEPTED: {OPEN, APPLIED, DISMISSED, SUPERSEDED},
    APPLIED: {OPEN, VERIFIED, SUPERSEDED},
    DISMISSED: {OPEN, SUPERSEDED},
    VERIFIED: {OPEN, SUPERSEDED},
    SUPERSEDED: set(),
}
SEVERITIES = ("high", "medium", "low", "info")


def record_id(*parts: Any) -> str:
    """A stable id for a row: the same parts give the same id on every PC."""
    return hashlib.sha256("\x1f".join("" if p is None else str(p) for p in parts).encode()).hexdigest()[:32]


def artifact_key(workspace_id: str, item_id: str) -> str:
    """How Optimize names a Fabric item: by its ids, never its display name (names change and repeat)."""
    if not (workspace_id or "").strip() or not (item_id or "").strip():
        raise ValueError("A Fabric item needs its workspace id and item id; a display name isn't enough.")
    return f"{workspace_id.strip()}/{item_id.strip()}"


def workspace_of(key: str) -> str:
    return key.split("/", 1)[0]


def fingerprint(rule_id: str, key: str, target: str = "") -> str:
    """The same problem on the same artifact has the same fingerprint, whoever finds it."""
    if not (rule_id or "").strip():
        raise ValueError("A finding needs the id of the rule that found it.")
    return record_id("finding", rule_id, key, target or "")


def timestamp(value: Any = None) -> str:
    """A time as these tables store it: UTC, ISO 8601 to the microsecond, so times from every PC
    compare correctly as text. None means now."""
    if value is None:
        at = dt.datetime.now(dt.timezone.utc)
    else:
        at = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value))
        if at.tzinfo is None:
            raise ValueError(f"A time needs its time zone: {value}")
    return at.astimezone(dt.timezone.utc).isoformat(timespec="microseconds")


def _after(previous: str | None) -> str:
    """Now, and always later than ``previous``: a change made on a PC whose clock is behind still
    sorts after the state the person saw when they made it."""
    at = dt.datetime.now(dt.timezone.utc)
    if previous:
        before = dt.datetime.fromisoformat(previous)
        if at <= before:
            at = before + dt.timedelta(microseconds=1)
    return timestamp(at)


def recommendation_id(fp: str, kind: str) -> str:
    return record_id("recommendation", fp, kind)


class Shared:
    """The active optimization lakehouse, as this installation reads and writes it.

    ``access`` is what connecting found ("write", "read" or "none"). Writes are refused when it
    isn't "write", and when another installation has since upgraded the lakehouse to a schema
    this app doesn't know.
    """

    def __init__(self, lake: Any, installation: Installation, access: str = "write", batch: int = BATCH):
        self.lake = lake
        self.installation = installation
        self.access = access
        self.batch = max(1, int(batch))

    # Writing

    def _check_writable(self) -> None:
        if self.access != "write":
            raise PermissionError("This lakehouse is open read-only for you, so Optimize can't add results to it. "
                                  "To add them, ask for Contributor on its workspace.")
        version = max(setup.applied_versions(self.lake), default=0)
        if version > schema.latest():
            raise PermissionError(setup.MESSAGES[setup.NEWER])

    def _write(self, name: str, rows: list[dict[str, Any]]) -> int:
        """Insert ``rows`` in batches; returns how many rows were sent. A batch that fails after its
        retries stops the write and raises: the batches before it stay, and writing the same rows
        again adds only the ones still missing."""
        # The MERGE skips rows already in the table, not repeats within what it is given.
        rows = list({r["record_id"]: r for r in reversed(rows)}.values())[::-1]
        for i in range(0, len(rows), self.batch):
            setup.write(self.lake, self.installation, name, rows[i:i + self.batch])
        return len(rows)

    def record_analysis(self, run: dict[str, Any], artifacts: Iterable[dict[str, Any]] = (),
                        findings: Iterable[dict[str, Any]] = (),
                        recommendations: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
        """Record one analysis run: the artifacts it looked at, what it found and what it recommends.

        ``run`` has ``run_id`` (unique per run on this PC), ``started_at``, ``status``, ``scope`` and
        ``rule_set_version``. Artifacts and findings name items by ``workspace_id`` and ``item_id``.
        A finding has ``rule_id`` and optionally ``target`` (a column, a file, a step), ``severity``,
        ``title``, ``detail`` and ``evidence``. A recommendation names its finding the same way (or
        by ``fingerprint``) and has a ``kind`` plus optional ``reason``, ``impact``, ``effort`` and
        ``confidence``. Recording the same run again adds only what is missing.
        """
        self._check_writable()
        run_id = str(run.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("An analysis run needs a run_id.")
        run_key = record_id("run", self.installation.id, run_id)
        observed = timestamp(run.get("finished_at"))
        arts = []
        for a in artifacts:
            key = artifact_key(a.get("workspace_id", ""), a.get("item_id", ""))
            arts.append({"record_id": record_id("artifact", key, run_key), "artifact_key": key,
                         "workspace_id": workspace_of(key), "item_id": key.split("/", 1)[1],
                         "item_type": a.get("item_type"), "display_name": a.get("display_name"),
                         "run_id": run_key, "observed_at": observed})
        found = []
        for f in findings:
            key = artifact_key(f.get("workspace_id", ""), f.get("item_id", ""))
            fp = fingerprint(f.get("rule_id", ""), key, f.get("target", ""))
            severity = (f.get("severity") or "info").lower()
            if severity not in SEVERITIES:
                raise ValueError(f"Severity must be one of {', '.join(SEVERITIES)}, not {severity}.")
            found.append({"record_id": record_id("observation", fp, run_key), "fingerprint": fp,
                          "rule_id": f["rule_id"], "rule_version": f.get("rule_version"), "artifact_key": key,
                          "target": f.get("target") or "", "severity": severity, "title": f.get("title"),
                          "detail": f.get("detail"), "evidence": f.get("evidence"), "run_id": run_key,
                          "observed_at": observed})
        fps = {r["fingerprint"] for r in found}
        recs = []
        for r in recommendations:
            fp = r.get("fingerprint") or fingerprint(
                r.get("rule_id", ""), artifact_key(r.get("workspace_id", ""), r.get("item_id", "")), r.get("target", ""))
            if fp not in fps:
                raise ValueError("A recommendation must be for a finding recorded in the same run.")
            kind = (r.get("kind") or "").strip()
            if not kind:
                raise ValueError("A recommendation needs a kind.")
            rid = recommendation_id(fp, kind)
            # One "opened" event per recommendation, whichever PC sees it first; seeing it again,
            # even after it was dismissed, adds nothing.
            recs.append({"record_id": record_id("recommendation-opened", rid), "recommendation_id": rid,
                         "fingerprint": fp, "kind": kind, "state": OPEN, "reason": r.get("reason"),
                         "impact": r.get("impact"), "effort": r.get("effort"), "confidence": r.get("confidence"),
                         "at": observed})
        self._write("artifacts", arts)
        self._write("findings", found)
        self._write("recommendations", recs)
        # Last, so readers never count a run whose rows aren't all there.
        self._write("analysis_runs", [{
            "record_id": run_key, "run_id": run_key,
            "started_at": timestamp(run["started_at"]) if run.get("started_at") else None, "finished_at": observed,
            "status": run.get("status") or "complete", "scope": run.get("scope"),
            "rule_set_version": run.get("rule_set_version"), "artifacts_scanned": len(arts), "findings": len(found),
            "error": run.get("error")}])
        return {"run_id": run_key, "artifacts": len(arts), "findings": len(found), "recommendations": len(recs)}

    def record_local_executions(self, rows: Iterable[dict[str, Any]]) -> int:
        """Add runs on this PC (optimize/local_runs.py rows); each ``execution_id`` is added once,
        however often the same runs are shared again. Returns how many rows were sent."""
        self._check_writable()
        rows = [r | {"record_id": r["execution_id"]} for r in rows]
        return self._write("local_execution", rows) if rows else 0

    def local_execution_ids(self) -> set[str]:
        """This installation's runs already in the lakehouse."""
        df = self.rows("local_execution")
        if not len(df):
            return set()
        return set(df.filter(pl.col("installation_id") == self.installation.id)["execution_id"].to_list())

    def set_recommendation_state(self, recommendation_id_: str, state: str, reason: str = "",
                                 workspaces: Iterable[str] | None = None) -> dict[str, Any]:
        """Move a recommendation to ``state`` (accepted, applied, dismissed, verified, superseded, or
        open again), as a new event. Refused when the move isn't allowed from its current state."""
        self._check_writable()
        current = next((r for r in self.recommendations(workspaces, include_closed=True)
                        if r["recommendation_id"] == recommendation_id_), None)
        if current is None:
            raise ValueError("That recommendation isn't in this lakehouse, or is for an item you can't open.")
        if state not in TRANSITIONS[current["state"]]:
            raise ValueError(f"A recommendation that is {current['state']} can't be marked {state}.")
        at = _after(current["changed_at"])
        event = {"record_id": record_id("recommendation-state", recommendation_id_, state, at, self.installation.id),
                 "recommendation_id": recommendation_id_, "fingerprint": current["fingerprint"],
                 "kind": current["kind"], "state": state, "reason": (reason or "")[:500] or None, "impact": current["impact"],
                 "effort": current["effort"], "confidence": current["confidence"], "at": at}
        self._write("recommendations", [event])
        self._write("audit", [{"record_id": record_id("audit", event["record_id"]), "at": at,
                               "action": "recommendation_state", "target": recommendation_id_, "outcome": state,
                               "message": f"from {current['state']}"}])
        return {**current, "state": state, "state_reason": event["reason"], "previous_state": current["state"],
                "changed_at": at, "changed_by": self.installation.id}

    # Reading

    def rows(self, name: str) -> pl.DataFrame:
        """Every row of ``fdopt_<name>``, each record once (defensively: the MERGE already dedups)."""
        cols = schema.expected()[schema.table(name)]
        try:
            df = self.lake.rows(schema.table(name))
        except Exception as exc:
            if "not found" in str(exc).lower() or "TableNotFound" in type(exc).__name__:
                df = None
            else:
                raise
        if df is None:
            return pl.DataFrame(schema=cols)
        return df.unique(subset=[schema.KEY], keep="first", maintain_order=True)

    def _finished_runs(self) -> set[str]:
        return set(self.rows("analysis_runs")["run_id"].drop_nulls().to_list())

    @staticmethod
    def _visible(df: pl.DataFrame, column: str, workspaces: Iterable[str] | None) -> pl.DataFrame:
        if workspaces is None:
            return df
        allowed = list({w for w in workspaces if w})
        return df.filter(pl.col(column).str.split("/").list.first().is_in(allowed))

    def _observations(self, workspaces: Iterable[str] | None) -> pl.DataFrame:
        """Finding rows from recorded runs, for items in ``workspaces``."""
        df = self.rows("findings")
        df = df.filter(pl.col("run_id").is_in(list(self._finished_runs())))
        return self._visible(df, "artifact_key", workspaces)

    def findings(self, workspaces: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """One row per problem: its latest observation, when it was first and last seen, in how many
        runs and by how many installations. Only items in ``workspaces`` (None: all)."""
        df = self._observations(workspaces)
        if not len(df):
            return []
        df = df.sort(["observed_at", "record_id"])
        out = df.group_by("fingerprint", maintain_order=True).agg(
            pl.col("rule_id", "rule_version", "artifact_key", "target", "severity", "title", "detail",
                   "evidence").last(),
            pl.col("observed_at").min().alias("first_seen"),
            pl.col("observed_at").max().alias("last_seen"),
            pl.col("run_id").n_unique().alias("occurrences"),
            pl.col("installation_id").n_unique().alias("installations"),
        )
        rank = {s: i for i, s in enumerate(SEVERITIES)}
        rows = sorted(out.to_dicts(), key=lambda r: r["last_seen"] or "", reverse=True)
        return sorted(rows, key=lambda r: rank.get(r["severity"], len(rank)))  # most severe, then most recent

    def recommendations(self, workspaces: Iterable[str] | None = None,
                        include_closed: bool = False) -> list[dict[str, Any]]:
        """Each recommendation at its latest state, with its finding's artifact: ``reason``, ``impact``,
        ``effort`` and ``confidence`` are from when it was opened, ``state_reason`` is what the last
        change said. Dismissed, verified and superseded ones only with ``include_closed``."""
        events = self.rows("recommendations")
        if not len(events):
            return []
        where = self._observations(workspaces).select("fingerprint", "artifact_key").unique(subset=["fingerprint"])
        events = events.join(where, on="fingerprint", how="inner")
        if not len(events):
            return []
        events = events.sort(["at", "record_id"])
        opened = events.group_by("recommendation_id", maintain_order=True).agg(
            pl.col("reason", "impact", "effort", "confidence").first(), pl.col("at").min().alias("opened_at"))
        latest = events.group_by("recommendation_id", maintain_order=True).agg(
            pl.col("fingerprint", "kind", "state", "artifact_key").last(),
            pl.col("reason").last().alias("state_reason"),
            pl.col("at").last().alias("changed_at"),
            pl.col("installation_id").last().alias("changed_by"),
            pl.len().alias("events"),
        ).join(opened, on="recommendation_id", how="left")
        latest = latest.with_columns(pl.when(pl.col("events") > 1).then(pl.col("state_reason")).alias("state_reason"))
        if not include_closed:
            latest = latest.filter(~pl.col("state").is_in([DISMISSED, VERIFIED, SUPERSEDED]))
        return latest.sort(["changed_at", "recommendation_id"], descending=[True, False]).to_dicts()
