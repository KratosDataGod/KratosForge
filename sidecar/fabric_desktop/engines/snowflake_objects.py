"""Snowflake object listing (SF-004, Preview; tested against fakes, not a live account).

Lists what the connection's role may see, one capped page at a time, through Snowflake's SQL API with
engines/snowflake_remote.py's client. Every statement is a SHOW command, which Snowflake answers from its
cloud services layer without a running warehouse, so none is sent and none resumes:

    SHOW DATABASES                           STARTS WITH, LIMIT n FROM 'cursor'
    SHOW SCHEMAS IN DATABASE "db"            the same
    SHOW TABLES IN SCHEMA "db"."schema"      the same
    SHOW VIEWS IN SCHEMA "db"."schema"       the same
    SHOW COLUMNS IN TABLE "db"."schema"."t"  (IN VIEW for a view) a chosen table's or view's columns

Paging: SHOW's ``LIMIT n FROM 'name'`` is Snowflake's cursor. A page asks for a few rows more than it
returns, so it knows whether there is a next page, and its ``next`` is the last name it returned. A row
named exactly as the cursor is dropped, whether or not Snowflake includes it. SHOW COLUMNS has no LIMIT,
so a table's columns are read once (Snowflake returns at most 10,000) and paged on this PC by offset.

Names: database, schema and table names are matched as SHOW ... LIKE finds them, then quoted exactly as
Snowflake lists them (``_ident``). SHOW's LIKE has no escape character, so ``_`` and ``%`` in a name
still match any character; the rows are filtered to the exact name afterwards (``_find``), ignoring case
only when exactly one row matches. A prefix filter uses STARTS WITH, which has no wildcards and is case
sensitive. Every string goes through ``_literal``.

Read-only, with the connection's role. Nothing here needs a RemoteAuthorization: no warehouse is used.
Errors keep Snowflake's code and never quote a credential (snowflake_remote.Client).
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from .. import redact
from .snowflake_auth import provider_for
from .snowflake_remote import Client, SnowflakeError, _find, _ident, _literal, rows

SCHEMA = "kratosforge.snowflake-objects/1"
PAGE = 200            # rows in a page unless asked otherwise
MAX_PAGE = 1000       # the most rows one page returns
SHOW_LIMIT = 10_000   # Snowflake's own cap on SHOW's LIMIT
MAX_PARTITIONS = 100  # result partitions read for one statement; SHOW answers are small
_TEXT_MAX = 300       # characters of one name, kind or comment


def _text(value: Any, limit: int = _TEXT_MAX) -> str:
    """A value from Snowflake as one line of plain text: control characters folded to spaces, cut to ``limit``."""
    line = " ".join("".join(c if c.isprintable() else " " for c in str(value if value is not None else "")).split())
    return line if len(line) <= limit else line[:limit - 1].rstrip() + "…"


def _named(name: str) -> str:
    """A name the caller gave, as it goes into a message: one line, credential-shaped text removed."""
    return redact.one_line(name, 120)


def _int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE:
        raise ValueError(f"A page holds 1 to {MAX_PAGE:,} rows. Ask for a number in that range.")
    return limit


def read_rows(client: Client, answer: dict[str, Any], cap: int) -> list[dict[str, Any]]:
    """Every row of a finished statement's answer, up to ``cap``, reading later result partitions only
    as needed, as dicts keyed by lower-case column name (snowflake_remote.rows)."""
    meta = answer.get("resultSetMetaData") or {}
    info = meta.get("partitionInfo")
    parts = info if isinstance(info, list) else []
    if len(parts) > MAX_PARTITIONS:
        raise SnowflakeError(f"Snowflake's answer has more than {MAX_PARTITIONS} partitions, so none of it was kept. "
                             "Narrow the list with a prefix and try again.")
    data = list(answer.get("data") or [])
    handle = str(answer.get("statementHandle") or "")
    for i in range(1, len(parts)):
        if len(data) >= cap:
            break
        page = client.partition(handle, i).get("data")
        if not isinstance(page, list):
            raise SnowflakeError("Snowflake sent a result partition with no rows where they belong. Try again.")
        data += page
    return rows({"resultSetMetaData": meta, "data": data[:cap]})


class Browser:
    """One Snowflake connection's objects, as its role sees them. ``client`` replaces the HTTP client in tests."""

    def __init__(self, connection: dict[str, Any], client: Client | None = None, *, http: Any = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.connection = connection
        self.role = str(connection.get("role") or "")
        self.client = client or Client(provider_for(connection), http=http, sleep=sleep)

    def _show(self, sql: str, cap: int) -> list[dict[str, Any]]:
        """One SHOW, as the connection's role, with no warehouse."""
        return read_rows(self.client, self.client.statement(sql, role=self.role), cap)

    def _as_role(self) -> str:
        return f"role {self.role}" if self.role else "your default role"

    # ------------------------------------------------------------------ names

    def database(self, name: str) -> str:
        """``name`` as Snowflake lists it, or SnowflakeError when the role can't see it."""
        found = _find(self._show(f"SHOW DATABASES LIKE {_literal(name)} LIMIT {SHOW_LIMIT}", SHOW_LIMIT), name)
        if not found:
            raise SnowflakeError(f"Database {_named(name)} doesn't exist, or {self._as_role()} can't see it. "
                                 "Check the name, or ask for USAGE on the database for your role.")
        return str(found["name"])

    def schema(self, database: str, name: str) -> tuple[str, str]:
        db = self.database(database)
        found = _find(self._show(f"SHOW SCHEMAS LIKE {_literal(name)} IN DATABASE {_ident(db)} LIMIT {SHOW_LIMIT}",
                                 SHOW_LIMIT), name)
        if not found:
            raise SnowflakeError(f"Schema {_named(name)} doesn't exist in {_text(db)}, or {self._as_role()} can't see it. "
                                 "Check the name, or ask for USAGE on the schema for your role.")
        return db, str(found["name"])

    # ------------------------------------------------------------------ pages

    def _page(self, kind: str, show: str, scope: str, prefix: str, after: str, limit: int,
              row: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        limit = _limit(limit)
        ask = limit + 2  # one for the cursor's own row, if Snowflake includes it, and one to tell there's more
        sql = show + (f" IN {scope}" if scope else "") + (f" STARTS WITH {_literal(prefix)}" if prefix else "") + \
            f" LIMIT {ask}" + (f" FROM {_literal(after)}" if after else "")
        found = [r for r in self._show(sql, ask) if not after or r.get("name") != after]
        items = [row(r) for r in found[:limit]]
        more = len(found) > limit
        return {"schema": SCHEMA, "kind": kind, "role": self.role, "items": items,
                "next": str(found[limit - 1].get("name") or "") if more else "", "limit": limit}

    def databases(self, prefix: str = "", after: str = "", limit: int = PAGE) -> dict[str, Any]:
        """A page of the databases the role sees. ``after`` is the previous page's ``next``."""
        return self._page("databases", "SHOW DATABASES", "", prefix, after, limit, lambda r: {
            "name": _text(r.get("name")), "kind": _text(r.get("kind")), "owner": _text(r.get("owner")),
            "comment": _text(r.get("comment")), "created_on": _text(r.get("created_on"))})

    def schemas(self, database: str, prefix: str = "", after: str = "", limit: int = PAGE) -> dict[str, Any]:
        db = self.database(database)
        page = self._page("schemas", "SHOW SCHEMAS", f"DATABASE {_ident(db)}", prefix, after, limit, lambda r: {
            "database": _text(r.get("database_name") or db), "name": _text(r.get("name")),
            "owner": _text(r.get("owner")), "comment": _text(r.get("comment"))})
        return page | {"database": db}

    def tables(self, database: str, schema: str, prefix: str = "", after: str = "", limit: int = PAGE) -> dict[str, Any]:
        db, sc = self.schema(database, schema)
        page = self._page("tables", "SHOW TABLES", f"SCHEMA {_ident(db)}.{_ident(sc)}", prefix, after, limit, lambda r: {
            "database": _text(r.get("database_name") or db), "schema": _text(r.get("schema_name") or sc),
            "name": _text(r.get("name")), "kind": _text(r.get("kind")), "rows": _int(r.get("rows")),
            "bytes": _int(r.get("bytes")), "owner": _text(r.get("owner")), "comment": _text(r.get("comment"))})
        return page | {"database": db, "schema_name": sc}

    def views(self, database: str, schema: str, prefix: str = "", after: str = "", limit: int = PAGE) -> dict[str, Any]:
        db, sc = self.schema(database, schema)
        page = self._page("views", "SHOW VIEWS", f"SCHEMA {_ident(db)}.{_ident(sc)}", prefix, after, limit, lambda r: {
            "database": _text(r.get("database_name") or db), "schema": _text(r.get("schema_name") or sc),
            "name": _text(r.get("name")), "secure": str(r.get("is_secure") or "").lower() == "true",
            "materialized": str(r.get("is_materialized") or "").lower() == "true",
            "owner": _text(r.get("owner")), "comment": _text(r.get("comment"))})
        return page | {"database": db, "schema_name": sc}

    def columns(self, database: str, schema: str, table: str, view: bool = False, offset: int = 0,
                limit: int = PAGE) -> dict[str, Any]:
        """A page of a table's (or, with ``view``, a view's) columns, in the order Snowflake lists them."""
        limit = _limit(limit)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("A page starts at 0 or the previous page's next. Ask again from there.")
        db, sc = self.schema(database, schema)
        what = "VIEWS" if view else "TABLES"
        found = _find(self._show(f"SHOW {what} LIKE {_literal(table)} IN SCHEMA {_ident(db)}.{_ident(sc)} "
                                 f"LIMIT {SHOW_LIMIT}", SHOW_LIMIT), table)
        noun = "View" if view else "Table"
        if not found:
            raise SnowflakeError(f"{noun} {_named(table)} doesn't exist in {_text(db)}.{_text(sc)}, or {self._as_role()} "
                                 f"can't see it. Check the name, or ask for SELECT on the {noun.lower()} for your role.")
        name = str(found["name"])
        listed = self._show(f"SHOW COLUMNS IN {'VIEW' if view else 'TABLE'} {_ident(db)}.{_ident(sc)}.{_ident(name)}",
                            SHOW_LIMIT)
        items = [_column(r) for r in listed[offset:offset + limit]]
        more = offset + limit < len(listed)
        return {"schema": SCHEMA, "kind": "columns", "role": self.role, "database": db, "schema_name": sc,
                "table": name, "items": items, "next": offset + limit if more else None, "limit": limit,
                "total": len(listed)}


def _column(r: dict[str, Any]) -> dict[str, Any]:
    """A SHOW COLUMNS row. ``data_type`` is Snowflake's JSON (type, precision, scale, length, nullable)."""
    try:
        kind = json.loads(r.get("data_type") or "{}")
    except (TypeError, ValueError):
        kind = {}
    kind = kind if isinstance(kind, dict) else {}
    nullable = kind.get("nullable")
    if not isinstance(nullable, bool):
        nullable = str(r.get("null?") or "").lower() in ("true", "y")
    return {"name": _text(r.get("column_name")), "type": _text(kind.get("type") or r.get("data_type")),
            "precision": _int(kind.get("precision")), "scale": _int(kind.get("scale")),
            "length": _int(kind.get("length")), "nullable": nullable, "default": _text(r.get("default")),
            "kind": _text(r.get("kind")), "comment": _text(r.get("comment"))}
