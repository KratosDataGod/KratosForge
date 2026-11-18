"""Data health: is a table fresh, and does its latest load look like its usual ones, read from its Delta log.

Three monitors per table, each from the table's own Delta log on this PC (no Fabric job, and nothing
in the table changes):

- Freshness: how long ago data last changed, against the entity's ``[freshness] within_hours``.
  Commits that only compact, vacuum or change settings don't count as a change.
- Volume: rows written by each load (a WRITE, MERGE, COPY INTO or streaming batch), and whether
  the latest is far from the usual. "Usual" is the median of the loads before it (the last
  ``BASELINE`` of them) and the spread is their median absolute distance from that median. A load
  is unusual when it is further from the median than ``SPREADS`` spreads, or ``FLOOR`` of the
  median when the loads barely vary. The rule is returned with every result so the screen can show it.
- Schema: the columns added, removed or retyped since the load before the latest (or, for a
  table with one load, since the version before it), so a column added between loads shows too.

A number the log doesn't hold is ``None`` with the reason, never estimated. A load whose rows
weren't recorded is left out of the usual and counted as unread; with fewer than ``MINIMUM``
readable loads there is no usual, and the volume check says so instead of alerting.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from statistics import median
from typing import Any, Callable

BASELINE = 20  # loads the usual is taken from
MINIMUM = 10  # readable loads needed before a load can be called unusual
SPREADS = 3.0  # how many spreads from the median is unusual
FLOOR = 0.10  # ... or this share of the median, when the spread is smaller than that
HISTORY = 200  # Delta log entries read per check
ALERT_KINDS = ("freshness", "volume", "schema")

_log = logging.getLogger(__name__)
_RUN = re.compile(r"r-\d{8}-\d{6}-[0-9a-f]{12}")  # a run id this app stamps on its commits (engine._RUN_ID)

# Commits that change no data (same as maintenance's: settings, constraints, column DDL, VACUUM, OPTIMIZE).
_NO_DATA = re.compile(r"TBLPROPERT|CONSTRAINT|^(ADD|CHANGE|RENAME|DROP) COLUMNS?\b|^UPGRADE PROTOCOL|^VACUUM|^OPTIMIZE$"
                      r"|^(SET|UNSET) TBLPROPERTIES|^CREATE TABLE$", re.I)
# Loads: commits that write new rows. Their rows written are the volume.
_WRITES = ("WRITE", "CREATE TABLE AS SELECT", "CREATE OR REPLACE TABLE AS SELECT", "REPLACE TABLE AS SELECT",
           "STREAMING UPDATE", "COPY INTO", "MERGE")
# Rows written, by the names Fabric's Spark and delta-rs (this app) give them.
_ROWS = ("numOutputRows", "num_added_rows")
_MERGE_ROWS = (("numTargetRowsInserted", "numTargetRowsUpdated"), ("num_target_rows_inserted", "num_target_rows_updated"))


def rule() -> dict[str, Any]:
    """The volume rule, as the screen shows it beside every alert."""
    return {"baseline": BASELINE, "minimum": MINIMUM, "spreads": SPREADS, "floor": FLOOR,
            "text": (f"Usual is the median rows written by the last {BASELINE} loads; spread is their median distance "
                     f"from it. A load is unusual more than {SPREADS:g} spreads (at least {FLOOR:.0%} of the median) "
                     f"away. Needs {MINIMUM} loads with rows recorded.")}


def _metrics(entry: dict[str, Any]) -> dict[str, Any]:
    m = entry.get("operationMetrics")
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except ValueError:
            return {}
    return m if isinstance(m, dict) else {}


def _count(value: Any) -> int | None:
    """A row count as the log holds it (Spark writes them as strings), or None when it isn't one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def rows_written(entry: dict[str, Any]) -> int | None:
    """Rows a load wrote, from its commit's metrics; None when the commit didn't record them."""
    m = _metrics(entry)
    if str(entry.get("operation") or "").upper() == "MERGE":
        for names in _MERGE_ROWS:
            counts = [_count(m.get(n)) for n in names]
            if all(c is not None for c in counts):
                return sum(c for c in counts if c is not None)
        return None
    for name in _ROWS:
        if (c := _count(m.get(name))) is not None:
            return c
    return None


def _when(entry: dict[str, Any]) -> dt.datetime | None:
    t = entry.get("timestamp")
    if isinstance(t, bool):
        return None
    if isinstance(t, (int, float)):
        return dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc)
    if isinstance(t, dt.datetime):
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    return None


def _version(entry: dict[str, Any]) -> bool:
    v = entry.get("version")
    return isinstance(v, int) and not isinstance(v, bool)


def changes_data(entry: dict[str, Any]) -> bool:
    return not _NO_DATA.search(str(entry.get("operation") or ""))


def is_load(entry: dict[str, Any]) -> bool:
    return str(entry.get("operation") or "").upper() in _WRITES


def _iso(t: dt.datetime | None) -> str | None:
    return t.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z") if t else None


def loads(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each load in the log, oldest first: version, when, operation, mode and rows written (None when unrecorded)."""
    out = []
    for e in history:
        if not is_load(e) or not _version(e):
            continue
        params = e.get("operationParameters")
        params = params if isinstance(params, dict) else {}
        run = str(e.get("fabric_desktop_run_id") or "")
        out.append({"version": e["version"], "at": _iso(_when(e)), "operation": str(e.get("operation")),
                    "mode": params.get("mode"), "rows": rows_written(e), "run_id": run if _RUN.fullmatch(run) else None})
    return sorted(out, key=lambda x: x["version"])


def usual(rows: list[int]) -> dict[str, Any]:
    """The median and spread of the readable loads, or why there's none yet."""
    if len(rows) < MINIMUM:
        return {"enough": False, "have": len(rows), "need": MINIMUM}
    mid = median(rows)
    spread = median(abs(r - mid) for r in rows)
    return {"enough": True, "have": len(rows), "need": MINIMUM, "median": mid, "spread": spread,
            "band": max(SPREADS * spread, FLOOR * mid)}


def volume(history: list[dict[str, Any]]) -> dict[str, Any]:
    """The latest load against the usual of the loads before it."""
    all_loads = loads(history)
    out: dict[str, Any] = {"loads": all_loads[-(BASELINE + 1):], "rule": rule()}
    if not all_loads:
        return out | {"state": "no_loads", "latest": None, "usual": usual([]), "unread": 0}
    latest, before = all_loads[-1], all_loads[:-1][-BASELINE:]
    known = [x["rows"] for x in before if x["rows"] is not None]
    base = usual(known)
    out |= {"latest": latest, "usual": base, "unread": len(before) - len(known)}
    if latest["rows"] is None:
        return out | {"state": "unread", "why": "The latest load didn't record how many rows it wrote."}
    if not base["enough"]:
        return out | {"state": "not_enough_history"}
    distance = latest["rows"] - base["median"]
    unusual = abs(distance) > base["band"]
    return out | {"state": ("high" if distance > 0 else "low") if unusual else "usual", "distance": distance}


def freshness(history: list[dict[str, Any]], within_hours: float | None, now: dt.datetime) -> dict[str, Any]:
    """How long ago data last changed, against the promise (if the entity makes one)."""
    versioned = [e for e in history if _version(e)]
    last = max((e for e in versioned if changes_data(e)), key=lambda e: e["version"], default=None)
    when = _when(last) if last else None
    out: dict[str, Any] = {"within_hours": within_hours, "last_version": last["version"] if last else None,
                           "last_change": _iso(when), "operation": last.get("operation") if last else None}
    if when is None:
        why = f"None of the last {HISTORY} commits in the log changed data." if last is None else "The commit that last changed data has no time."
        return out | {"state": "unread", "age_hours": None, "why": why}
    age = max(0.0, (now - when).total_seconds() / 3600)
    out["age_hours"] = round(age, 2)
    if not within_hours:
        return out | {"state": "no_promise"}
    return out | {"state": "late" if age > float(within_hours) else "fresh"}


def schema_change(before: list[dict[str, Any]] | None, after: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Columns added, removed or retyped between two schemas (each a list of {name, type})."""
    if before is None or after is None:
        return {"state": "unread"}
    b = {c["name"]: c.get("type") for c in before}
    a = {c["name"]: c.get("type") for c in after}
    added = [n for n in a if n not in b]
    removed = [n for n in b if n not in a]
    retyped = [{"name": n, "from": b[n], "to": a[n]} for n in a if n in b and b[n] != a[n]]
    state = "changed" if added or removed or retyped else "same"
    return {"state": state, "added": added, "removed": removed, "retyped": retyped}


def alerts(result: dict[str, Any]) -> list[dict[str, Any]]:
    """What the checks found that someone should look at, each naming its evidence."""
    out = []
    f, v, s = result["freshness"], result["volume"], result["schema"]
    if f["state"] == "late":
        out.append({"kind": "freshness", "title": f"Not updated for {f['age_hours']:g} h (promise {f['within_hours']:g} h)",
                    "version": f["last_version"]})
    if v["state"] in ("high", "low"):
        u = v["usual"]
        out.append({"kind": "volume", "title": f"Latest load wrote {v['latest']['rows']:,} rows; usual is "
                    f"{u['median']:,.0f} ± {u['spread']:,.0f}", "version": v["latest"]["version"], "rule": v["rule"]["text"]})
    if s["state"] == "changed":
        parts = ([f"{len(s['added'])} column{'s' * (len(s['added']) != 1)} added"] if s["added"] else []) + \
                ([f"{len(s['removed'])} removed"] if s["removed"] else []) + \
                ([f"{len(s['retyped'])} retyped"] if s["retyped"] else [])
        out.append({"kind": "schema", "title": "Schema changed: " + ", ".join(parts), "version": s.get("version")})
    return out


def _columns(schema: Any) -> list[dict[str, Any]]:
    data = json.loads(schema.to_json())
    return [{"name": f["name"], "type": json.dumps(f["type"]) if not isinstance(f["type"], str) else f["type"]}
            for f in data.get("fields") or []]


def check(open_at: Callable[[int | None], Any], within_hours: float | None = None,
          now: dt.datetime | None = None) -> dict[str, Any]:
    """Freshness, volume and schema of one table. ``open_at(version)`` opens its Delta table (the
    latest with None). A part that can't be read says so; the others still report."""
    now = now or dt.datetime.now(dt.timezone.utc)
    table = open_at(None)
    history = table.history(HISTORY)
    result: dict[str, Any] = {"version": table.version(), "checked_at": _iso(now),
                              "freshness": freshness(history, within_hours, now), "volume": volume(history)}
    seen = result["volume"]["loads"]
    latest = result["volume"].get("latest")
    if latest is None or latest["version"] == 0:
        result["schema"] = {"state": "no_earlier_version"}
    else:
        since = seen[-2]["version"] if len(seen) > 1 else latest["version"] - 1
        try:
            schema = schema_change(_columns(open_at(since).schema()), _columns(open_at(latest["version"]).schema()))
        except Exception as exc:  # noqa: BLE001 - a version whose log entry is gone, or any read failure: say so
            # The delta-rs error usually names the table's OneLake path: it goes to the log, not the result.
            _log.warning("Couldn't read the schema at version %s: %s", since, exc)
            schema = {"state": "unread", "why": f"Couldn't read the schema at version {since}; its log entry may have "
                      "been removed."}
        result["schema"] = schema | {"version": latest["version"], "since_version": since}
    result["alerts"] = alerts(result)
    return result
