"""Snowflake credits of the queries that carry a workload's query tag (Preview; tested against fakes,
not a live account).

Reading SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY runs one SELECT on a warehouse the person picks. A
suspended warehouse resumes for it and uses credits while it runs, so the read needs a confirmed
``RemoteAuthorization`` for that account and warehouse (engines/authorization.py), like the
Databricks billing read. The query is fixed text: only a whole number of days and the query tags go
in, and a tag is checked to be plain text (letters, digits, spaces and ``: . _ - /``) first.

No user name, role or query text is read: only each query's id, tag, warehouse, its size and type,
when it started, how long it ran and the bytes it scanned.

Credits are worked out, so every figure is **Estimated**: the time each query ran on the warehouse
(``execution_time``) times the credits per hour Snowflake publishes for a standard warehouse of that
size. WAREHOUSE_METERING_HISTORY has what a warehouse was billed, but for the whole warehouse per
hour, so it can't be split into single runs. A query on a warehouse type or size not listed here, or
with no warehouse, has no credit figure (None, with the reason), never 0. Credits stay credits:
never added to Fabric CU or Databricks DBUs, and never turned into money here.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .. import redact
from ..jsonfile import read_json, write_json
from .contract import CellRequest, SessionConfig
from .databricks_discovery import _text

SCHEMA = "snowflake-query-credits@1"
ROW_LIMIT = 500
DAYS = (7, 30)
MAX_TAGS = 20
_TAG = re.compile(r"^[A-Za-z0-9 :._/-]{1,200}$")
# Credits per hour of a standard warehouse, as Snowflake publishes them, by QUERY_HISTORY's warehouse_size.
CREDITS_PER_HOUR = {"X-SMALL": 1, "SMALL": 2, "MEDIUM": 4, "LARGE": 8, "X-LARGE": 16, "2X-LARGE": 32,
                    "3X-LARGE": 64, "4X-LARGE": 128, "5X-LARGE": 256, "6X-LARGE": 512}
ASSUMPTIONS = [
    ("A standard warehouse uses the credits per hour Snowflake publishes for its size (X-Small 1, Small 2, "
     "Medium 4, and so on)."),
    ("Those rates are for Gen1 standard warehouses. A Gen2 standard warehouse also reports STANDARD but bills at "
     "a higher rate, so on one this figure may be low."),
    ("Each time the warehouse resumes, at least 60 seconds is billed. That minimum isn't added here, so a run "
     "that resumed the warehouse may have used more."),
    "Nothing else ran on the warehouse at the same time. If it did, this figure is too high.",
    "Cloud services credits aren't counted.",
    "Each query that carries the tag is counted as one run.",
]

SQL = """SELECT
  query_id,
  query_tag,
  warehouse_name,
  warehouse_size,
  warehouse_type,
  start_time,
  execution_time,
  bytes_scanned
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE query_tag IN ({tags})
  AND start_time >= DATEADD(day, -{days}, CURRENT_TIMESTAMP())
  AND execution_status = 'SUCCESS'
ORDER BY start_time DESC
LIMIT {limit}"""


def tags(values: Any) -> list[str]:
    """Query tags as they go into the query: plain text, at most MAX_TAGS, each once."""
    out = []
    for v in values or []:
        text = str(v).strip() if isinstance(v, str) else ""
        if not _TAG.fullmatch(text):
            raise ValueError(f"{v!r} can't be used as a query tag here. Use letters, digits, spaces and : . _ - / "
                             "only, up to 200 characters.")
        if text not in out:
            out.append(text)
    if len(out) > MAX_TAGS:
        raise ValueError(f"Read at most {MAX_TAGS} query tags at a time.")
    return out


def query(days: int, tag_values: Any) -> str:
    if days not in DAYS:
        raise ValueError(f"Read query history for {', '.join(map(str, DAYS))} days.")
    found = tags(tag_values)
    if not found:
        raise ValueError("Link a Snowflake query tag to the workload first.")
    return SQL.format(tags=", ".join(f"'{t}'" for t in found), days=int(days), limit=ROW_LIMIT)


def _number(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and n >= 0 else None


def _when(value: Any) -> str | None:
    if isinstance(value, dt.datetime):
        t = value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
        return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _text(value) or None


def credits(size: Any, kind: Any, seconds: float | None) -> tuple[float | None, float | None, str]:
    """(credits, credits per hour, reason when not known) for one query."""
    if seconds is None:
        return None, None, "Its run time wasn't recorded."
    if not size:
        return None, None, "It didn't run on a warehouse, or the warehouse size wasn't recorded."
    if str(kind or "STANDARD").upper() != "STANDARD":
        return None, None, f"The credit rate of a {str(kind).lower()} warehouse isn't known here."
    rate = CREDITS_PER_HOUR.get(str(size).upper().replace(" ", ""))
    if rate is None:
        return None, None, f"The credit rate of a {size} warehouse isn't known here."
    return round(seconds / 3600 * rate, 6), float(rate), ""


def rows_from(table: Any) -> list[dict[str, Any]]:
    """The query's rows as one dict per query, with its worked-out credits or the reason there are none."""
    out = []
    for raw in table.to_pylist() if table is not None else []:
        row = {str(k).lower(): v for k, v in raw.items()}
        ms_ = _number(row.get("execution_time"))
        seconds = None if ms_ is None else round(ms_ / 1000, 3)
        size = _text(row.get("warehouse_size"))
        found, rate, why = credits(size, row.get("warehouse_type"), seconds)
        out.append({"query_id": _text(row.get("query_id")), "tag": _text(row.get("query_tag")),
                    "warehouse": _text(row.get("warehouse_name")), "size": size,
                    "type": _text(row.get("warehouse_type")), "start": _when(row.get("start_time")),
                    "seconds": seconds, "credits_per_hour": rate, "credits": found, "reason": why,
                    "bytes_scanned": _number(row.get("bytes_scanned"))})
    return out


def read(adapter: Any, connection: dict[str, Any], warehouse: str, days: int, tag_values: Any,
         authorization: Any) -> dict[str, Any]:
    """Run the query on ``warehouse`` and close the session. ``authorization`` is required: the
    adapter refuses without one for that account and warehouse."""
    sql = query(days, tag_values)
    session = adapter.open_session(SessionConfig("snowflake-query-credits", "",
                                                 {"connection": connection, "warehouse": warehouse}), authorization)
    try:
        result = session.execute(CellRequest(sql, "sql", "snowflake", preview_rows=0, result_rows=ROW_LIMIT))
    finally:
        session.close()
    if result.status != "ok":
        errors = [o.get("evalue") or o.get("ename") or "" for o in result.outputs if o.get("type") == "error"]
        return {"ok": False, "error": redact.secrets(_text(" ".join(e for e in errors if e) or
                                                           "The query history read didn't finish.", 600))}
    now = dt.datetime.now(dt.timezone.utc)
    return {"ok": True, "schema": SCHEMA, "days": days, "warehouse": warehouse, "tags": tags(tag_values),
            "seconds": round(result.seconds, 1), "read_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "period": {"start": (now - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "end": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
            "assumptions": list(ASSUMPTIONS), "queries": rows_from(result.table)}


def _file(root: Path, account: str) -> Path:
    """One file per account. The prefix keeps Windows device names (CON, NUL) out, and the short hash keeps
    accounts that read the same once sanitised ("a.b", "a_b") apart."""
    name = "".join(c if c.isalnum() else "_" for c in account)[:80]
    digest = hashlib.sha256(account.encode("utf-8")).hexdigest()[:12]
    return root / f"account-{name}-{digest}.json"


def save(root: Path, account: str, result: dict[str, Any]) -> None:
    """Keep the last read on this PC, per account, so the comparison shows it without reading again."""
    root.mkdir(parents=True, exist_ok=True)
    write_json(_file(root, account), result | {"account": account})


def last(root: Path, account: str) -> dict[str, Any] | None:
    data = read_json(_file(root, account), None)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    return json.loads(json.dumps(redact.walk(data)))
