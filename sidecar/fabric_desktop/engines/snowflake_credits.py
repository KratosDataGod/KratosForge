"""Snowflake warehouse credits (SF-006, Preview; tested against fakes, not a live account).

Reads credits used per warehouse per day from the SNOWFLAKE database's ACCOUNT_USAGE share, through
Snowflake's SQL API with engines/snowflake_remote.py's client:

    SELECT <UTC day>, warehouse_name, SUM(credits_used_compute), SUM(credits_used_cloud_services),
           SUM(credits_used)
    FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
    WHERE <UTC day> >= <today in UTC> - (days - 1)  GROUP BY 1, 2  ORDER BY 1 DESC, 2  LIMIT <limit + 1>

The view has one row per warehouse per hour. Here they are added up per UTC day, for 1 to MAX_DAYS
days counting today, and at most MAX_ROWS day rows come back; one more is asked for so a cut list
says so. Only whole numbers go into the statement.

What Snowflake documents for the view (docs.snowflake.com, WAREHOUSE_METERING_HISTORY view, read
2026-11-21), which the result's note repeats:

- Latency is up to 3 hours, and up to 6 hours for CREDITS_USED_CLOUD_SERVICES, so the latest hours
  may be missing or low.
- CREDITS_USED is compute plus cloud services, before the daily cloud services adjustment, so it may
  be more than the credits billed (METERING_DAILY_HISTORY has those, and isn't read here).

**Credits only (ADR-006).** Credits are reported as Snowflake credits. They are never turned into
money here, never added to DBUs or Fabric capacity units, and never called savings.

**It uses credits.** Reading the view is a query, so it runs on a warehouse, which resumes if it is
suspended. So, exactly as for query history (snowflake_history.py), nothing is sent without a
``RemoteAuthorization`` for the SQL warehouse engine and ``account/warehouse`` (ADR-003). ``confirm``
is the step before it, like the Databricks DBU read's: it says what would run and sends nothing.

**Access.** The role needs IMPORTED PRIVILEGES on the SNOWFLAKE database, or the SNOWFLAKE.USAGE_VIEWER
database role. A role without either is refused by name with that next step (``AccountUsageDenied``),
and the grant is never attempted or guessed beyond those two.
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from .. import redact
from .authorization import require
from .contract import Unsupported
from .snowflake_auth import provider_for
from .snowflake_objects import _text, read_rows
from .snowflake_remote import Client, SnowflakeError
from .snowflake_warehouse import SNOWFLAKE_SQL, target_for

SCHEMA = "kratosforge.snowflake-credits/1"
SOURCE = "SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY"
UNIT = "snowflake_credits"
DAYS = 7
MAX_DAYS = 31
ROWS = 500
MAX_ROWS = 5000
LATENCY_HOURS = 3                 # documented for the view
CLOUD_SERVICES_LATENCY_HOURS = 6  # documented for CREDITS_USED_CLOUD_SERVICES
STATEMENT_SECONDS = 120
_PLACES = 6
# Snowflake's codes for "does not exist or not authorized" and "insufficient privileges".
_DENIED_CODES = ("002003", "003001")
_DENIED_NAMES = ("snowflake", "account_usage", "warehouse_metering_history")

_DAY = "CONVERT_TIMEZONE('UTC', start_time)::DATE"
_SQL = (f"SELECT TO_VARCHAR({_DAY}, 'YYYY-MM-DD') AS usage_day, warehouse_name, "
        "SUM(credits_used_compute) AS credits_compute, SUM(credits_used_cloud_services) AS credits_cloud_services, "
        f"SUM(credits_used) AS credits_used FROM {SOURCE} "
        f"WHERE {_DAY} >= DATEADD('day', -{{back}}, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE) "
        "GROUP BY 1, 2 ORDER BY 1 DESC, 2 LIMIT {ask}")


class AccountUsageDenied(SnowflakeError):
    """The role can't read SNOWFLAKE.ACCOUNT_USAGE. ``grants`` names what would let it."""

    grants = ("IMPORTED PRIVILEGES on the SNOWFLAKE database", "the SNOWFLAKE.USAGE_VIEWER database role")


def _bounded(value: int, low: int, high: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{what} is {low:,} to {high:,}. Ask for a number in that range.")
    return value


def statement(days: int = DAYS, limit: int = ROWS) -> str:
    """The fixed query for ``days`` UTC days (today counts as one) and ``limit`` rows."""
    days = _bounded(days, 1, MAX_DAYS, "The number of days")
    limit = _bounded(limit, 1, MAX_ROWS, "The number of rows")
    return _SQL.format(back=days - 1, ask=limit + 1)


def _warehouse(connection: dict[str, Any], warehouse: str) -> str:
    name = str(warehouse or connection.get("warehouse") or "")
    if not name:
        raise Unsupported("Reading credits runs on a warehouse, and this connection has none. "
                          "Pick a warehouse for this Snowflake connection in Settings > Connections.")
    return name


def _ran_on(warehouse: str) -> str:
    return f"It runs one query on warehouse {_text(warehouse)}, which resumes it if it is suspended and uses Snowflake credits."


def confirm(connection: dict[str, Any], *, warehouse: str = "", days: int = DAYS, limit: int = ROWS) -> dict[str, Any]:
    """What a credit read would run, before anyone confirms it. Nothing is sent."""
    sql = statement(days, limit)
    name = _warehouse(connection, warehouse)
    return {"needs_confirm": True, "engine": SNOWFLAKE_SQL.id, "target": target_for(connection, name),
            "warehouse": name, "days": days, "limit": limit, "source": SOURCE, "statement": sql,
            "note": _ran_on(name) + " Confirm to read warehouse credits."}


def _credits(value: Any) -> Decimal | None:
    """A credit figure from Snowflake, or None when it isn't a finite, non-negative number."""
    if value is None or value == "":
        return Decimal(0)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


def _out(number: Decimal) -> float:
    return float(round(number, _PLACES))


def _rows(found: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Day rows as {date, warehouse, credits, credits_compute, credits_cloud_services}, and how many
    were dropped because a figure wasn't a usage number (never guessed)."""
    out, dropped = [], 0
    for r in found:
        used, compute, cloud = (_credits(r.get(k)) for k in ("credits_used", "credits_compute", "credits_cloud_services"))
        if used is None or compute is None or cloud is None:
            dropped += 1
            continue
        out.append({"date": _text(r.get("usage_day"), 10), "warehouse": _text(r.get("warehouse_name")),
                    "credits": used, "credits_compute": compute, "credits_cloud_services": cloud})
    return out, dropped


def _denied(exc: SnowflakeError, role: str) -> SnowflakeError:
    """``exc`` as AccountUsageDenied when it says the role can't read ACCOUNT_USAGE, else as it was."""
    text = str(exc).lower()
    refused = exc.code in _DENIED_CODES or "not authorized" in text or "insufficient privileges" in text
    if not refused or not any(n in text for n in _DENIED_NAMES):
        return exc
    who = f"Role {_text(role, 120)}" if role else "Your default role"
    target = f"role {_text(role, 120)}" if role else "your default role"
    return AccountUsageDenied(
        f"{who} can't read SNOWFLAKE.ACCOUNT_USAGE, so warehouse credits weren't read. Ask a Snowflake admin "
        f"to grant IMPORTED PRIVILEGES on the SNOWFLAKE database, or the SNOWFLAKE.USAGE_VIEWER database role, "
        f"to {target}, then read credits again.", exc.status, exc.code, exc.sql_state)


def warehouse_credits(connection: dict[str, Any], authorization: Any, *, warehouse: str = "", days: int = DAYS,
                      limit: int = ROWS, client: Client | None = None, http: Any = None,
                      sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Credits used per warehouse per UTC day for the last ``days`` days (today included), newest first,
    at most ``limit`` rows. Runs on ``warehouse`` (or the connection's), so it needs an authorization for it."""
    sql = statement(days, limit)
    name = _warehouse(connection, warehouse)
    require(SNOWFLAKE_SQL, target_for(connection, name), authorization)
    role = str(connection.get("role") or "")
    client = client or Client(provider_for(connection), http=http, sleep=sleep)
    try:
        answer = client.statement(sql, role=role, warehouse=name, timeout=STATEMENT_SECONDS,
                                  max_wait=STATEMENT_SECONDS + 30)
        found = read_rows(client, answer, limit + 1)
    except SnowflakeError as exc:
        raise _denied(exc, role) from None
    rows, dropped = _rows(found[:limit])
    totals = {k: _out(sum((r[k] for r in rows), Decimal(0)))
              for k in ("credits", "credits_compute", "credits_cloud_services")}
    more = len(found) > limit
    note = ("Credits used, as WAREHOUSE_METERING_HISTORY reports them, before Snowflake's daily cloud services "
            "adjustment, so they may be more than the credits billed. "
            f"The view lags by up to {LATENCY_HOURS} hours, and its cloud services credits by up to "
            f"{CLOUD_SERVICES_LATENCY_HOURS} hours, so the latest hours may be missing or low. Days are UTC days. "
            + _ran_on(name))
    if more:
        note += f" Only the newest {limit:,} rows are shown, so the totals cover those rows. Ask for fewer days to see all of them."
    if dropped:
        note += f" {dropped:,} rows had a figure that isn't a credit amount and were left out. Check them in Snowsight."
    return {"schema": SCHEMA, "account": str(connection.get("account") or ""), "warehouse": name, "role": role,
            "days": days, "limit": limit, "unit": UNIT, "source": SOURCE, "latency_hours": LATENCY_HOURS,
            "cloud_services_latency_hours": CLOUD_SERVICES_LATENCY_HOURS,
            "rows": [r | {k: _out(r[k]) for k in ("credits", "credits_compute", "credits_cloud_services")} for r in rows],
            "more": more, "dropped": dropped, "totals": totals, "note": redact.one_line(note, 1200)}
