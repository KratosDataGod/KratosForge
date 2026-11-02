"""Learn from execution (GD-04): notes under a Query profile, each one about something the profile
itself measured, with the rule that explains it.

``notes(profile)`` reads the steps spark_local.query_profile returns and nothing else: no query
text, no guesses. A note appears only when the numbers show it:

    SQ002  a scan that read many rows and kept few of them, through a filter that wraps a column in
           a function (``"year"(order_date) = 2026``), so the engine couldn't skip data by the
           column's min and max. Rows read and kept, and the scan's share of the time, are measured.
    SQ006  a cross join (or a join with no condition) that made more rows than both its inputs.
    SQ006  a join that made many times more rows than its larger input.

A profile that shows none of these gets no notes, which is most of them.
"""

from __future__ import annotations

import re
from typing import Any

from . import guides

MIN_ROWS = 100_000           # below this, skipping data saves too little to mention
KEEP_AT_MOST = 0.25          # a scan "kept few" rows when its filter kept a quarter or less
JOIN_GROWTH = 10             # a join "multiplied" rows at ten times its larger input
_SCANS = {"SEQ_SCAN", "TABLE_SCAN", "READ_PARQUET", "PARQUET_SCAN", "DELTA_SCAN", "READ_CSV", "READ_CSV_AUTO"}
_CROSS = {"CROSS_PRODUCT", "BLOCKWISE_NL_JOIN"}
_JOINS = {"HASH_JOIN", "NESTED_LOOP_JOIN", "PIECEWISE_MERGE_JOIN", "IE_JOIN", "ASOF_JOIN"}
# A comparison whose column side is a function of one column: "year"(order_date) = 2026, lower(name) <> 'x'
_WRAPPED = re.compile(r'"?([A-Za-z_][\w]*)"?\(\s*"?([A-Za-z_][\w.]*)"?\s*\)\s*(=|<>|!=|<=|>=|<|>)')


def notes(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """[{rule_id, step, text, measured: {...}, principle, url}] for the steps that show something; [] otherwise."""
    steps = profile.get("steps") if isinstance(profile, dict) else None
    if not isinstance(steps, list):
        return []
    out: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        name = str(step.get("name") or "").strip().upper()
        note = (_scan(i, step) if name in _SCANS else
                _join(i, step, _children(steps, i), name in _CROSS) if name in _CROSS | _JOINS else None)
        if note:
            note["principle"] = guides.load().get(note["rule_id"], {}).get("principle", "")
            note["url"] = f"sql-best-practices.md#{note['rule_id'].lower()}"
            out.append(note)
    return out


def _children(steps: list[dict[str, Any]], i: int) -> list[dict[str, Any]]:
    """The steps directly under step i: the next steps one level deeper, until one at i's level."""
    depth = steps[i].get("depth", 0)
    kids = []
    for s in steps[i + 1:]:
        d = s.get("depth", 0)
        if d <= depth:
            break
        if d == depth + 1:
            kids.append(s)
    return kids


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%" if x >= 0.01 else "under 1%"


def _scan(i: int, step: dict[str, Any]) -> dict[str, Any] | None:
    read, kept = int(step.get("rows_scanned") or 0), int(step.get("rows") or 0)
    filters = str((step.get("details") or {}).get("Filters") or "")
    wrapped = _WRAPPED.search(filters)
    if read < MIN_ROWS or kept > read * KEEP_AT_MOST or not wrapped:
        return None
    table = str((step.get("details") or {}).get("Table") or "the table").split(".")[-1]
    share = float(step.get("share") or 0)
    return {"rule_id": "SQ002", "step": i,
            "text": f"The scan of {table} read {read:,} rows and kept {kept:,} ({_pct(kept / read)}), "
                    f"{_pct(share)} of the query's time. Its filter wraps {wrapped.group(2)} in {wrapped.group(1)}(), "
                    f"so the engine couldn't skip data by {wrapped.group(2)}'s values.",
            "measured": {"rows_read": read, "rows_kept": kept, "share": share, "filter": filters}}


def _join(i: int, step: dict[str, Any], kids: list[dict[str, Any]], cross: bool) -> dict[str, Any] | None:
    made = int(step.get("rows") or 0)
    sides = [int(k.get("rows") or 0) for k in kids]
    if len(sides) != 2 or made < MIN_ROWS:
        return None
    share = float(step.get("share") or 0)
    if cross and made > max(sides):
        text = (f"A cross join matched every row with every row: {sides[0]:,} × {sides[1]:,} made {made:,} rows, "
                f"{_pct(share)} of the query's time.")
    elif not cross and made >= JOIN_GROWTH * max(sides):
        text = (f"A join made {made:,} rows from {sides[0]:,} and {sides[1]:,}, {_pct(share)} of the query's time. "
                "More than one row matched on both sides; check the join's keys if that isn't intended.")
    else:
        return None
    return {"rule_id": "SQ006", "step": i, "text": text,
            "measured": {"rows_made": made, "inputs": sides, "share": share}}
