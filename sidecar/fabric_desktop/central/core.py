"""The platform's database logic: surrogate keys and the catalog, in SQL.

One module, two places it runs:

    Fabric     inside UDF_Platform (User Data Functions), against SQL_Platform, a SQL database in
               Fabric. The function app's source is this file plus thin wrappers (udf.py).
    This PC    against a SQLite file, for a platform that hasn't been created in Fabric yet,
               and for the tests.

So the rules are written once: every key is issued here, every catalog edit is saved here.
Keep this file standard-library only: User Data Functions run it with nothing else installed.

Tables (T-SQL schema.table; SQLite schema__table):

    keys.<name>          <name>_sk INT identity, <name>_bk unique business key, create_date
    config.setting       schema version
    catalog.domain       domains and subdomains (parent_id), set by the central team
    catalog.project      metadata projects: domain, subdomain, team
    catalog.product      data products: access, version, status
    catalog.port         where each product port lives, per environment
    catalog.link         which project reads which port
    catalog.enrichment   description, tags, classification, glossary terms, steward of a table or column
    catalog.glossary     business terms
    catalog.audit        who changed what in the catalog
    catalog.admin        platform admins: the central team, who create domains, key tables and sources
    catalog.key_table    the platform's key tables: business key, description, who made it
    catalog.source       every source system: the id business keys use, code, owner, type, expected
                         arrival in LH_Landing, connections per env (one list for every project)
    catalog.reader       which project reads which landing zone source
    catalog.landing_zone the central landing workspace's zones (Preview): Shared, a domain's, or a secure
                         one for PII or PHI, each its own lakehouse; a source's zone column names its zone
    catalog.request      requests from domain teams: a key table, a source, access to a product, a shortcut,
                         a workspace, access to a workspace, a domain, or an exception to a policy
    catalog.grant        projects let in to a domain or private product by its owner
    catalog.shortcut     every OneLake shortcut last seen in a scan: its target and health
    catalog.shortcut_scope  lakehouses scanned for shortcuts, so a first scan isn't read as "all added"
    catalog.shortcut_change every shortcut added, repointed, removed, broken or fixed, and by whom
    catalog.shortcut_risk   risks a platform admin accepted on a shortcut (Dev reading Prod), and why
    catalog.endorsement  each data product item's endorsement (Certified, Promoted, Master data) as Fabric's
                         admin tenant scan last read it, so everyone sees it (Preview)
    catalog.product_term business terms on a data product: a name and a link to the term in Microsoft
                         Purview, typed by the product's owner; nothing reads Purview (Preview)
    catalog.policy*      governance policies, their versions, assignments and history (policy.py)

Who may change what (the caller is always who Fabric says it is, see udf.py):

    platform admins      domains, key tables, source systems, landing zones, glossary, admins, shortcut rules,
                         accepted shortcut risks, policy exceptions, endorsement scans, every product's
                         business terms, and every request
    domain owners        glossary terms of their domain
    product owners       access requests to their product, and their product's business terms
    everyone             reading, issuing keys from existing key tables, catalog text, products, links, requests
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = 5
UNKNOWN_KEY = -1
UNKNOWN_BK = "UNKNOWN"
MAX_BK = 400
ACCESS = ("public", "domain", "private")
STATUS = ("draft", "published", "deprecated")
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
REQUEST_KINDS = ("key_table", "source", "access", "shortcut", "workspace", "workspace_access", "domain", "exception", "data")
# Kinds whose approval the app carries out in Fabric first, as the person approving (central/requests_service.py).
FABRIC_KINDS = ("shortcut", "workspace", "workspace_access")
WORKSPACE_ROLES = ("Viewer", "Contributor", "Member", "Admin")
REQUEST_STATUS = ("waiting", "approved", "declined", "withdrawn")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_GUID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
_WS_BAD = re.compile(r'[\\/:*?"<>|#%\x00-\x1f]')


# Tables other modules add to the platform (policy.py's governance policies), created with the rest.
SCHEMA_HOOKS: list[Any] = []
# Request kinds another module checks and carries out (policy.py's exceptions):
# {kind: {"check": fn(db, target, detail, reason, by) -> (target, detail), "approve": fn(db, req, by)}}
REQUEST_HOOKS: dict[str, dict[str, Any]] = {}


class PlatformError(ValueError):
    """A request the platform refuses, in words a person can act on."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def key_name(name: str) -> str:
    name = str(name or "").strip().lower()
    if not _NAME.match(name):
        raise PlatformError(f"{name!r} isn't a key table name: lowercase letters, digits or _, starting with a letter")
    return name


def domain_id(value: str) -> str:
    value = str(value or "").strip().lower().replace(" ", "-")
    if not _ID.match(value):
        raise PlatformError(f"{value!r} isn't a domain id: lowercase letters, digits, - or _")
    return value


class Db:
    """A DB-API connection and its dialect: "tsql" (SQL database in Fabric) or "sqlite"."""

    def __init__(self, conn: Any, dialect: str):
        if dialect not in ("tsql", "sqlite"):
            raise ValueError("dialect is tsql or sqlite")
        self.conn, self.dialect = conn, dialect

    @property
    def tsql(self) -> bool:
        return self.dialect == "tsql"

    def t(self, schema: str, table: str) -> str:
        return f"[{schema}].[{table}]" if self.tsql else f'"{schema}__{table}"'

    def run(self, sql: str, params: tuple | list = ()) -> Any:
        cur = self.conn.cursor()
        cur.execute(sql, tuple(params))
        return cur

    def rows(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        cur = self.run(sql, params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def exists(self, schema: str, table: str) -> bool:
        if self.tsql:
            return bool(self.rows("SELECT 1 AS x FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id "
                                  "WHERE s.name = ? AND t.name = ?", (schema, table)))
        return bool(self.rows("SELECT 1 AS x FROM sqlite_master WHERE type = 'table' AND name = ?",
                              (f"{schema}__{table}",)))

    def commit(self) -> None:
        self.conn.commit()


# --------------------------------------------------------------------------- schema

_TEXT = {"tsql": "NVARCHAR(400)", "sqlite": "TEXT"}
_LONG = {"tsql": "NVARCHAR(MAX)", "sqlite": "TEXT"}

_CATALOG: dict[str, list[tuple[str, str]]] = {
    "domain": [("id", "key"), ("name", "text"), ("parent_id", "text"), ("description", "long"), ("owners", "text"),
               ("fabric_domain", "text"), ("updated_at", "text"), ("updated_by", "text"), ("fabric_owners", "long")],
    "project": [("name", "key"), ("domain_id", "text"), ("subdomain_id", "text"), ("team", "text"),
                ("description", "long"), ("updated_at", "text"), ("updated_by", "text")],
    "product": [("id", "key"), ("project", "text"), ("name", "text"), ("domain_id", "text"), ("description", "long"),
                ("owner", "text"), ("access", "text"), ("version", "text"), ("status", "text"),
                ("deprecated_on", "text"), ("updated_at", "text"), ("updated_by", "text")],
    "port": [("id", "key"), ("product_id", "text"), ("port", "text"), ("entity", "text"), ("env", "text"),
             ("workspace_id", "text"), ("workspace_name", "text"), ("lakehouse_id", "text"), ("lakehouse_name", "text"),
             ("schema_name", "text"), ("table_name", "text"), ("columns", "long"), ("updated_at", "text")],
    "link": [("id", "key"), ("consumer_project", "text"), ("alias", "text"), ("product_id", "text"),
             ("port", "text"), ("reads", "long"), ("updated_at", "text"), ("updated_by", "text")],
    "enrichment": [("target", "key"), ("description", "long"), ("tags", "long"), ("classification", "text"),
                   ("terms", "long"), ("steward", "text"), ("certified", "text"), ("updated_at", "text"),
                   ("updated_by", "text")],
    "glossary": [("term", "key"), ("definition", "long"), ("domain_id", "text"), ("owner", "text"),
                 ("updated_at", "text"), ("updated_by", "text")],
    "admin": [("user_name", "key"), ("added_at", "text"), ("added_by", "text")],
    "key_table": [("name", "key"), ("business_key", "text"), ("description", "long"), ("domain_id", "text"),
                  ("created_at", "text"), ("created_by", "text")],
    "source": [("name", "key"), ("title", "text"), ("description", "long"), ("owner", "text"),
               ("expected_hours", "text"), ("connections", "long"), ("updated_at", "text"), ("updated_by", "text"),
               ("source_id", "text"), ("code", "text"), ("system_type", "text"), ("active", "text"), ("zone", "text")],
    "reader": [("id", "key"), ("project", "text"), ("source", "text"), ("entities", "long"), ("updated_at", "text")],
    "request": [("id", "key"), ("kind", "text"), ("target", "text"), ("detail", "long"), ("project", "text"),
                ("reason", "long"), ("requested_by", "text"), ("requested_at", "text"), ("status", "text"),
                ("decided_by", "text"), ("decided_at", "text"), ("note", "long")],
    "grant": [("id", "key"), ("product_id", "text"), ("project", "text"), ("request_id", "text"),
              ("granted_by", "text"), ("granted_at", "text")],
    "shortcut": [("id", "key"), ("workspace_id", "text"), ("workspace_name", "text"), ("lakehouse_id", "text"),
                 ("lakehouse_name", "text"), ("path", "text"), ("name", "text"), ("target", "long"),
                 ("health", "text"), ("health_detail", "long"), ("checked_at", "text"), ("first_seen", "text"),
                 ("last_seen", "text"), ("target_workspace_name", "text"), ("target_lakehouse_name", "text")],
    "landing_zone": [("id", "key"), ("name", "text"), ("domain_id", "text"), ("workspace_id", "text"),
                     ("workspace_name", "text"), ("lakehouse_id", "text"), ("lakehouse_name", "text"), ("secure", "text"),
                     ("readers", "long"), ("label", "text"), ("devtest", "text"), ("updated_at", "text"), ("updated_by", "text"),
                     ("switched_at", "text"), ("switched_by", "text")],
    "shortcut_scope": [("lakehouse_id", "key"), ("first_scan", "text"), ("last_scan", "text")],
    "shortcut_change": [("id", "key"), ("at", "text"), ("kind", "text"), ("shortcut_id", "text"),
                        ("workspace_name", "text"), ("lakehouse_name", "text"), ("name", "text"),
                        ("before_def", "long"), ("after_def", "long"), ("by_user", "text"), ("how", "text"),
                        ("note", "long")],
    "shortcut_risk": [("id", "key"), ("shortcut_id", "text"), ("kind", "text"), ("reason", "long"),
                      ("target", "long"), ("accepted_by", "text"), ("accepted_at", "text")],
    "endorsement": [("item_id", "key"), ("workspace_id", "text"), ("item_type", "text"), ("item_name", "text"),
                    ("endorsement", "text"), ("certified_by", "text"), ("scanned_at", "text"), ("scanned_by", "text")],
    "product_term": [("id", "key"), ("product_id", "text"), ("term", "text"), ("url", "long"), ("added_by", "text"),
                     ("added_at", "text")],
    # When someone last used the platform from the app, to tell an owner who has gone quiet.
    "person": [("user_name", "key"), ("last_seen", "text")],
    "follow": [("id", "key"), ("user_name", "text"), ("product_id", "text"), ("followed_at", "text")],
    # What each environment last published of a product, so a promote's changes are told once per environment.
    "product_env": [("id", "key"), ("product_id", "text"), ("env", "text"), ("version", "text"), ("status", "text"),
                    ("deprecated_on", "text"), ("published_at", "text")],
}


def _column(db: Db, name: str, kind: str) -> str:
    if kind == "key":
        return f"{name} {_TEXT[db.dialect]} NOT NULL PRIMARY KEY"
    return f"{name} {(_LONG if kind == 'long' else _TEXT)[db.dialect]} NULL"


def ensure_schema(db: Db) -> dict[str, Any]:
    """Create every catalog and config table that is missing. Safe to run again."""
    created = []
    if db.tsql:
        for schema in ("keys", "config", "catalog"):
            db.run(f"IF SCHEMA_ID('{schema}') IS NULL EXEC('CREATE SCHEMA [{schema}]')")
    if not db.exists("config", "setting"):
        db.run(f"CREATE TABLE {db.t('config', 'setting')} ({_column(db, 'name', 'key')}, value {_LONG[db.dialect]} NULL)")
        created.append("config.setting")
    for table, cols in _CATALOG.items():
        if not db.exists("catalog", table):
            db.run(f"CREATE TABLE {db.t('catalog', table)} ({', '.join(_column(db, n, k) for n, k in cols)})")
            created.append(f"catalog.{table}")
    if not db.exists("catalog", "audit"):
        ident = "INT IDENTITY(1,1) PRIMARY KEY" if db.tsql else "INTEGER PRIMARY KEY AUTOINCREMENT"
        db.run(f"CREATE TABLE {db.t('catalog', 'audit')} (id {ident}, at {_TEXT[db.dialect]}, by_user {_TEXT[db.dialect]}, "
               f"action {_TEXT[db.dialect]}, target {_TEXT[db.dialect]}, detail {_LONG[db.dialect]})")
        created.append("catalog.audit")
    created += ensure_source_ids(db)
    created += ensure_domain_columns(db)
    created += ensure_landing_zones(db)
    created += ensure_shortcut_risks(db)
    created += ensure_trust_badges(db)
    created += ensure_people(db)
    created += ensure_follows(db)
    for hook in SCHEMA_HOOKS:
        created += hook(db)
    _upsert(db, "config", "setting", "name", {"name": "schema_version", "value": str(SCHEMA_VERSION)})
    db.commit()
    return {"created": created, "schema_version": SCHEMA_VERSION}


def _upsert(db: Db, schema: str, table: str, key: str, row: dict[str, Any]) -> None:
    cols = list(row)
    found = db.rows(f"SELECT {key} FROM {db.t(schema, table)} WHERE {key} = ?", (row[key],))
    if found:
        sets = ", ".join(f"{c} = ?" for c in cols if c != key)
        db.run(f"UPDATE {db.t(schema, table)} SET {sets} WHERE {key} = ?",
               [row[c] for c in cols if c != key] + [row[key]])
    else:
        db.run(f"INSERT INTO {db.t(schema, table)} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
               [row[c] for c in cols])


def _audit(db: Db, by: str, action: str, target: str, detail: Any = None) -> None:
    db.run(f"INSERT INTO {db.t('catalog', 'audit')} (at, by_user, action, target, detail) VALUES (?, ?, ?, ?, ?)",
           (_now(), by or "", action, target, json.dumps(detail, default=str) if detail is not None else None))


# --------------------------------------------------------------------------- surrogate keys


def _make_key_table(db: Db, name: str) -> bool:
    """keys.<name> with its Unknown member (-1). True if it was created now. Callers check who may."""
    name = key_name(name)
    if db.exists("keys", name):
        return False
    t = db.t("keys", name)
    if db.tsql:
        db.run(f"CREATE TABLE {t} ({name}_sk INT IDENTITY(1,1) NOT NULL PRIMARY KEY, "
               f"{name}_bk NVARCHAR({MAX_BK}) NOT NULL, create_date DATETIME2 NOT NULL, "
               f"CONSTRAINT [uq_{name}_bk] UNIQUE ({name}_bk))")
        db.run(f"SET IDENTITY_INSERT {t} ON; "
               f"INSERT INTO {t} ({name}_sk, {name}_bk, create_date) VALUES (?, ?, '1900-01-01'); "
               f"SET IDENTITY_INSERT {t} OFF;", (UNKNOWN_KEY, UNKNOWN_BK))
    else:
        db.run(f"CREATE TABLE {t} ({name}_sk INTEGER PRIMARY KEY AUTOINCREMENT, {name}_bk TEXT NOT NULL UNIQUE, "
               f"create_date TEXT NOT NULL)")
        db.run(f"INSERT INTO {t} ({name}_sk, {name}_bk, create_date) VALUES (?, ?, ?)",
               (UNKNOWN_KEY, UNKNOWN_BK, "1900-01-01T00:00:00+00:00"))
    db.commit()
    return True


def ensure_key_table(db: Db, name: str, by: str = "", business_key: str = "", description: str = "",
                     domain_id: str = "") -> bool:
    """Create a key table. Only platform admins create one; anyone else asks with request().
    True if it was created now, False if it was already there (which anyone may ask)."""
    name = key_name(name)
    if db.exists("keys", name):
        if _row(db, "key_table", "name", name) is None:  # made before key tables were registered
            _register_key_table(db, name, "", "", "", "")
            db.commit()
        return False
    require_admin(db, by, f"create key table {name}")
    created = _make_key_table(db, name)
    _register_key_table(db, name, business_key, description, domain_id, by)
    _audit(db, by, "create key table", name, {"business_key": business_key})
    db.commit()
    return created


def _register_key_table(db: Db, name: str, business_key: str, description: str, domain_id: str, by: str) -> None:
    _upsert(db, "catalog", "key_table", "name", {
        "name": name, "business_key": str(business_key or "").strip(), "description": str(description or ""),
        "domain_id": str(domain_id or "") or None, "created_at": _now(), "created_by": by})


def _missing_key_table(name: str) -> PlatformError:
    return PlatformError(f"keys.{name} isn't one of the platform's key tables. Ask a platform admin for it: "
                         "Platform > Set up > Business keys > Request a key table")


def _clean_bks(business_keys: list[Any]) -> list[str]:
    out = set()
    for v in business_keys or []:
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        if len(s) > MAX_BK:
            raise PlatformError(f"business key {s[:40]}... is longer than {MAX_BK} characters")
        out.add(s)
    return sorted(out)


# A keys database that has stopped issuing (keys moving to, or moved to, the one keys database
# for every environment) keeps the reason here; every run that asks for keys is told it.
KEYS_CLOSED = "keys_closed"
# Set on the shared keys database while a move fills it and nothing uses it yet.
KEYS_STAGING = "keys_staging"
# Set on the shared keys database once every environment uses it: from then on it's never staged or cleared.
KEYS_LIVE = "keys_live"


def _setting(db: Db, name: str) -> str:
    if not db.exists("config", "setting"):
        return ""
    found = db.rows(f"SELECT value FROM {db.t('config', 'setting')} WHERE name = ?", (name,))
    return str(found[0]["value"] or "") if found else ""


def _require_open(db: Db) -> None:
    if why := _setting(db, KEYS_CLOSED):
        raise PlatformError(why)


def close_keys(db: Db, reason: str = "", by: str = "") -> dict[str, Any]:
    """Stop issuing and importing keys here, telling every run ``reason``; an empty reason opens them again."""
    require_admin(db, by, "close or reopen key issuing")
    _upsert(db, "config", "setting", "name", {"name": KEYS_CLOSED, "value": str(reason or "")})
    _audit(db, by, "close keys" if reason else "reopen keys", "keys", {"reason": reason})
    db.commit()
    return {"closed": str(reason or "")}


def keys_state(db: Db) -> dict[str, Any]:
    """Whether this keys database is live (Dev, Test and Prod use it), being filled by a move, or closed."""
    return {"live": _setting(db, KEYS_LIVE) == "1", "staging": _setting(db, KEYS_STAGING) == "1",
            "closed": _setting(db, KEYS_CLOSED)}


def stage_keys(db: Db, staging: bool, live: bool = False, by: str = "") -> dict[str, Any]:
    """Mark the shared keys database as being filled by a move (only then can clear_keys empty it),
    or, with ``live``, as the one every environment uses now. A live database is never staged again."""
    require_admin(db, by, "move keys")
    if _setting(db, KEYS_LIVE) == "1" and (staging or not live):
        raise PlatformError("This keys database is already in use by Dev, Test and Prod. Join it instead of moving keys into it.")
    _upsert(db, "config", "setting", "name", {"name": KEYS_STAGING, "value": "1" if staging and not live else ""})
    if live:
        _upsert(db, "config", "setting", "name", {"name": KEYS_LIVE, "value": "1"})
        _audit(db, by, "keys live", "keys")
    db.commit()
    return keys_state(db)


def clear_keys(db: Db, by: str = "") -> dict[str, Any]:
    """Drop every key table a move put here before it switched, so the move can start again.
    Refused on a keys database that isn't being filled by a move: those keys are in use."""
    require_admin(db, by, "clear the keys a move copied")
    if _setting(db, KEYS_LIVE) == "1" or _setting(db, KEYS_STAGING) != "1":
        raise PlatformError("Only a keys database a move is still filling can be cleared: these keys are in use.")
    names = [t["name"] for t in list_key_tables(db)]
    for name in names:
        if db.exists("keys", name):
            db.run(f"DROP TABLE {db.t('keys', name)}")
        db.run(f"DELETE FROM {db.t('catalog', 'key_table')} WHERE name = ?", (name,))
    _audit(db, by, "clear moved keys", "keys", {"tables": names})
    db.commit()
    return {"cleared": names}


def issue_keys(db: Db, name: str, business_keys: list[Any]) -> dict[str, Any]:
    """The surrogate key of every business key given, issuing the next keys for new ones.

    New keys are inserted in business key order inside one transaction, under a lock on the
    range they would go in, so two runs asking at the same time never get two keys for one
    business key. Returns {"keys": {bk: sk}, "inserted": n}."""
    name = key_name(name)
    bks = _clean_bks(business_keys)
    _require_open(db)
    if not db.exists("keys", name):
        raise _missing_key_table(name)
    if not bks:
        return {"keys": {}, "inserted": 0}
    t = db.t("keys", name)
    if db.tsql:
        cur = db.run(
            "SET NOCOUNT ON; "
            f"DECLARE @in TABLE (bk NVARCHAR({MAX_BK}) NOT NULL PRIMARY KEY); "
            f"INSERT INTO @in (bk) SELECT DISTINCT [value] FROM OPENJSON(?) WHERE [value] IS NOT NULL; "
            "BEGIN TRANSACTION; "
            f"INSERT INTO {t} ({name}_bk, create_date) SELECT i.bk, SYSUTCDATETIME() FROM @in i "
            f"WHERE NOT EXISTS (SELECT 1 FROM {t} k WITH (UPDLOCK, HOLDLOCK) WHERE k.{name}_bk = i.bk) ORDER BY i.bk; "
            "DECLARE @inserted INT = @@ROWCOUNT; "
            "COMMIT TRANSACTION; "
            f"SELECT k.{name}_bk AS bk, k.{name}_sk AS sk, @inserted AS inserted FROM {t} k JOIN @in i ON i.bk = k.{name}_bk;",
            (json.dumps(bks),))
        found = cur.fetchall()
        db.commit()
        return {"keys": {r[0]: int(r[1]) for r in found}, "inserted": int(found[0][2]) if found else 0}
    def found() -> dict[str, int]:
        out: dict[str, int] = {}
        for i in range(0, len(bks), 500):
            chunk = bks[i:i + 500]
            for r in db.rows(f"SELECT {name}_bk AS bk, {name}_sk AS sk FROM {t} WHERE {name}_bk IN "
                             f"({', '.join('?' for _ in chunk)})", chunk):
                out[r["bk"]] = int(r["sk"])
        return out

    # Only the new ones are inserted: in SQLite even an ignored insert uses up a key number.
    db.run("BEGIN IMMEDIATE")
    have = found()
    new = [(b, _now()) for b in bks if b not in have]
    db.conn.executemany(f"INSERT INTO {t} ({name}_bk, create_date) VALUES (?, ?)", new)
    out = found() if new else have
    db.commit()
    return {"keys": out, "inserted": len(new)}


# SQL Server takes at most 2,100 parameters in one statement, and three go in per key.
TSQL_ROWS_PER_INSERT = 500


def import_keys(db: Db, name: str, rows: list[list[Any]], by: str = "") -> dict[str, Any]:
    """Copy existing keys in with the same numbers ([[sk, bk, create_date], ...]): moving from LH_Keys.

    A business key already here must already have the same key; anything else is refused, so a
    migration never renumbers a key. The identity continues after the highest key. A key table
    that isn't there yet is created, which only a platform admin may do."""
    name = key_name(name)
    _require_open(db)
    ensure_key_table(db, name, by)
    t = db.t("keys", name)
    have = {r["bk"]: int(r["sk"]) for r in db.rows(f"SELECT {name}_bk AS bk, {name}_sk AS sk FROM {t}")}
    taken = set(have.values())
    new, clashes = [], []
    for sk, bk, created in rows:
        sk, bk = int(sk), str(bk)
        if bk in have:
            if have[bk] != sk:
                clashes.append(f"{bk} is {have[bk]} here but {sk} in the copy")
            continue
        if sk in taken:
            clashes.append(f"key {sk} is already used here by another business key")
            continue
        new.append((sk, bk, str(created or _now())))
        taken.add(sk)
    if clashes:
        raise PlatformError(f"keys.{name}: can't copy without renumbering: " + "; ".join(clashes[:5]))
    if new:
        if db.tsql:
            for i in range(0, len(new), TSQL_ROWS_PER_INSERT):
                chunk = new[i:i + TSQL_ROWS_PER_INSERT]
                values = ", ".join("(?, ?, ?)" for _ in chunk)
                db.run(f"SET IDENTITY_INSERT {t} ON; INSERT INTO {t} ({name}_sk, {name}_bk, create_date) VALUES {values}; "
                       f"SET IDENTITY_INSERT {t} OFF;", [v for row in chunk for v in row])
            top = max(max(taken), 0)
            db.run(f"DBCC CHECKIDENT ('keys.{name}', RESEED, {top})")
        else:
            db.conn.executemany(f"INSERT INTO {t} ({name}_sk, {name}_bk, create_date) VALUES (?, ?, ?)", new)
    db.commit()
    return {"imported": len(new), "already_there": len(rows) - len(new)}


def key_stats(db: Db, name: str) -> dict[str, Any]:
    name = key_name(name)
    if not db.exists("keys", name):
        return {"created": False, "keys": 0, "next": 1}
    r = db.rows(f"SELECT COUNT(*) AS n, MAX({name}_sk) AS top FROM {db.t('keys', name)} WHERE {name}_sk <> ?",
                (UNKNOWN_KEY,))[0]
    return {"created": True, "keys": int(r["n"] or 0), "next": int(r["top"] or 0) + 1}


def lookup_key(db: Db, name: str, value: str) -> dict[str, Any] | None:
    name = key_name(name)
    if not db.exists("keys", name):
        return None
    rows = db.rows(f"SELECT {name}_sk AS sk, {name}_bk AS bk, create_date FROM {db.t('keys', name)} WHERE {name}_bk = ?",
                   (str(value).strip(),))
    return {"sk": int(rows[0]["sk"]), "bk": rows[0]["bk"], "create_date": str(rows[0]["create_date"])} if rows else None


def export_keys(db: Db, name: str, after: int = UNKNOWN_KEY - 1, limit: int = 50000) -> list[list[Any]]:
    """Keys above ``after``, in key order, for checking a copy row by row."""
    name = key_name(name)
    top = f"TOP ({int(limit)}) " if db.tsql else ""
    tail = "" if db.tsql else f" LIMIT {int(limit)}"
    return [[int(r["sk"]), r["bk"], str(r["create_date"])] for r in db.rows(
        f"SELECT {top}{name}_sk AS sk, {name}_bk AS bk, create_date FROM {db.t('keys', name)} "
        f"WHERE {name}_sk > ? ORDER BY {name}_sk{tail}", (int(after),))]


# --------------------------------------------------------------------------- catalog


def _row(db: Db, table: str, key: str, value: str) -> dict[str, Any] | None:
    found = db.rows(f"SELECT * FROM {db.t('catalog', table)} WHERE {key} = ?", (value,))
    return found[0] if found else None


def list_domains(db: Db) -> list[dict[str, Any]]:
    return sorted(db.rows(f"SELECT * FROM {db.t('catalog', 'domain')}"), key=lambda d: (d.get("parent_id") or "", d["name"] or ""))


def save_domain(db: Db, domain: dict[str, Any], by: str = "") -> dict[str, Any]:
    """Add or change a domain. Platform admins only."""
    did = domain_id(domain.get("id") or domain.get("name", ""))
    require_admin(db, by, "create or change domains")
    name = str(domain.get("name") or "").strip()
    if not name:
        raise PlatformError("A domain needs a name")
    parent = str(domain.get("parent_id") or "").strip() or None
    if parent:
        parent = domain_id(parent)
        p = _row(db, "domain", "id", parent)
        if p is None:
            raise PlatformError(f"parent domain {parent} doesn't exist")
        if p.get("parent_id"):
            raise PlatformError("Subdomains go one level deep: pick a top-level domain as the parent")
        if parent == did:
            raise PlatformError("A domain can't be its own parent")
    current = _row(db, "domain", "id", did)
    linked = (current or {}).get("fabric_domain") or ""
    if linked and (name != current["name"] or (parent or None) != (current.get("parent_id") or None)):
        raise PlatformError(f"{current['name']} comes from Fabric: rename or move it in Fabric (Admin portal > "
                            "Domains), then choose Import from Fabric")
    row = {"id": did, "name": name, "parent_id": parent, "description": str(domain.get("description") or ""),
           "owners": str(domain.get("owners") or ""), "fabric_domain": linked or str(domain.get("fabric_domain") or ""),
           "updated_at": _now(), "updated_by": by}
    _upsert(db, "catalog", "domain", "id", row)
    _audit(db, by, "save domain", did, row)
    db.commit()
    return row


def _slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_.-]+", "-", str(name or "").strip().lower()).strip("-._")[:60] or "domain"
    did, n = base, 2
    while did in taken:
        did, n = f"{base}-{n}", n + 1
    return did


def _fabric_admins(d: dict[str, Any]) -> str | None:
    """A Fabric domain's admins as stored: a JSON list of {name, type}, names only (no email), sorted."""
    people = sorted({(str(a.get("name") or "").strip(), str(a.get("type") or "User"))
                     for a in d.get("admins") or [] if str(a.get("name") or "").strip()})
    return json.dumps([{"name": n, "type": t} for n, t in people]) if people else None


def ensure_domain_columns(db: Db) -> list[str]:
    """Give catalog.domain the columns added after it was made (fabric_owners). Safe to run again."""
    added = []
    if "fabric_owners" not in _columns(db, "domain"):
        db.run(f"ALTER TABLE {db.t('catalog', 'domain')} ADD {'' if db.tsql else 'COLUMN '}{_column(db, 'fabric_owners', 'long')}")
        added.append("catalog.domain.fabric_owners")
    return added


def import_fabric_domains(db: Db, domains: list[dict[str, Any]], by: str = "") -> dict[str, Any]:
    """Make the catalog's domains follow Fabric's domains and subdomains (``GET /v1/admin/domains``).

    A Fabric domain matches the catalog domain already linked to it (``fabric_domain``), else one
    with the same name that isn't linked yet, else it's added. Linked domains take Fabric's name and
    parent; their owners and description stay as the platform set them. A Fabric domain that carries
    ``admins`` (its Fabric domain admins, read during the import) stores their names and kinds in
    ``fabric_owners``: shown as "From Fabric", never used to grant anything. One without ``admins``
    keeps what an earlier import read. Domains that are no longer
    in Fabric are reported, never deleted: projects may still use them. Platform admins only.
    """
    require_admin(db, by, "import domains from Fabric")
    ensure_domain_columns(db)  # a platform made before fabric_owners
    existing = list_domains(db)
    by_fabric = {d["fabric_domain"]: d for d in existing if d.get("fabric_domain")}
    by_name = {str(d["name"] or "").strip().lower(): d for d in existing if not d.get("fabric_domain")}
    taken = {d["id"] for d in existing}
    fabric = {str(d.get("id") or ""): d for d in domains if d.get("id")}
    # parents first, so a subdomain's parent is already in the catalog
    order = sorted(fabric.values(), key=lambda d: (bool(d.get("parentDomainId")), str(d.get("displayName") or "")))
    ids: dict[str, str] = {}
    out: dict[str, list[str]] = {"added": [], "updated": [], "linked": [], "unchanged": [], "skipped": [], "gone": []}
    for d in order:
        fid, name = str(d["id"]), str(d.get("displayName") or "").strip() or str(d["id"])
        parent_fid = str(d.get("parentDomainId") or "")
        if parent_fid and (parent_fid not in ids or fabric.get(parent_fid, {}).get("parentDomainId")):
            out["skipped"].append(name)  # deeper than one level, or its parent is missing: Fabric doesn't allow either
            continue
        parent = ids.get(parent_fid) if parent_fid else None
        row = by_fabric.get(fid)
        kind = "updated"
        if row is None:
            row = by_name.pop(name.lower(), None)
            kind = "linked" if row else "added"
        if row is None:
            did = _slug(name, taken)
            taken.add(did)
            row = {"id": did, "description": str(d.get("description") or ""), "owners": ""}
        elif kind == "updated" and row["name"] == name and (row.get("parent_id") or None) == parent:
            kind = "unchanged"
        admins = _fabric_admins(d) if "admins" in d else row.get("fabric_owners")
        if kind == "unchanged" and (admins or None) != (row.get("fabric_owners") or None):
            kind = "updated"
        ids[fid] = row["id"]
        if kind != "unchanged":
            new = {"id": row["id"], "name": name, "parent_id": parent,
                   "description": str(row.get("description") or d.get("description") or ""),
                   "owners": str(row.get("owners") or ""), "fabric_domain": fid, "fabric_owners": admins or None,
                   "updated_at": _now(), "updated_by": by}
            _upsert(db, "catalog", "domain", "id", new)
        out[kind].append(row["id"])
    out["gone"] = [d["id"] for f, d in by_fabric.items() if f not in fabric]
    _audit(db, by, "import domains from Fabric", f"{len(fabric)} domains",
           {k: v for k, v in out.items() if v and k != "unchanged"})
    db.commit()
    return out


def delete_domain(db: Db, did: str, by: str = "") -> None:
    did = domain_id(did)
    require_admin(db, by, "delete domains")
    children = db.rows(f"SELECT id FROM {db.t('catalog', 'domain')} WHERE parent_id = ?", (did,))
    users = db.rows(f"SELECT name FROM {db.t('catalog', 'project')} WHERE domain_id = ? OR subdomain_id = ?", (did, did))
    if children or users:
        what = [f"subdomain {c['id']}" for c in children] + [f"project {u['name']}" for u in users]
        raise PlatformError(f"{did} is still used by " + ", ".join(what))
    db.run(f"DELETE FROM {db.t('catalog', 'domain')} WHERE id = ?", (did,))
    _audit(db, by, "delete domain", did)
    db.commit()


def register_project(db: Db, project: dict[str, Any], by: str = "") -> dict[str, Any]:
    """Record a metadata project and its domain. The domain must be one the central team made."""
    name = str(project.get("name") or "").strip()
    if not name:
        raise PlatformError("The project needs a name")
    dom = str(project.get("domain") or "").strip()
    sub = str(project.get("subdomain") or "").strip()
    if dom and _row(db, "domain", "id", dom) is None:
        raise PlatformError(f"domain {dom} isn't one of your organization's domains")
    if sub:
        s = _row(db, "domain", "id", sub)
        if s is None or s.get("parent_id") != dom:
            raise PlatformError(f"{sub} isn't a subdomain of {dom}")
    row = {"name": name, "domain_id": dom or None, "subdomain_id": sub or None, "team": str(project.get("team") or ""),
           "description": str(project.get("description") or ""), "updated_at": _now(), "updated_by": by}
    _upsert(db, "catalog", "project", "name", row)
    if "landing" in project:
        _save_readers(db, name, project.get("landing") or {})
    db.commit()
    return row


def list_projects(db: Db) -> list[dict[str, Any]]:
    return db.rows(f"SELECT * FROM {db.t('catalog', 'project')}")


def register_product(db: Db, product: dict[str, Any], ports: list[dict[str, Any]], env: str, by: str = "") -> dict[str, Any]:
    """Publish a data product and where its ports live in one environment (done on promote)."""
    project = str(product.get("project") or "").strip()
    name = str(product.get("name") or "").strip()
    if not project or not name:
        raise PlatformError("A product needs its project and name")
    if product.get("access", "private") not in ACCESS:
        raise PlatformError(f"access must be one of {', '.join(ACCESS)}")
    if product.get("status", "draft") not in STATUS:
        raise PlatformError(f"status must be one of {', '.join(STATUS)}")
    pid = f"{project}/{name}"
    ensure_follows(db)
    before = _row(db, "product_env", "id", f"{pid}:{env}")
    old_ports = {r["port"]: json.loads(r["columns"] or "{}")
                 for r in db.rows(f"SELECT port, columns FROM {db.t('catalog', 'port')} WHERE product_id = ? AND env = ?",
                                  (pid, env))}
    row = {"id": pid, "project": project, "name": name, "domain_id": product.get("domain") or None,
           "description": str(product.get("description") or ""), "owner": str(product.get("owner") or ""),
           "access": product.get("access", "private"), "version": str(product.get("version") or "1"),
           "status": product.get("status", "draft"), "deprecated_on": product.get("deprecated_on") or None,
           "updated_at": _now(), "updated_by": by}
    _upsert(db, "catalog", "product", "id", row)
    for p in ports:
        port = {"id": f"{pid}:{p['port']}:{env}", "product_id": pid, "port": p["port"], "entity": p.get("entity", ""),
                "env": env, "workspace_id": p.get("workspace_id", ""), "workspace_name": p.get("workspace_name", ""),
                "lakehouse_id": p.get("lakehouse_id", ""), "lakehouse_name": p.get("lakehouse_name", ""),
                "schema_name": p.get("schema", "dbo"), "table_name": p.get("table", ""),
                "columns": json.dumps(p.get("columns", {})), "updated_at": _now()}
        _upsert(db, "catalog", "port", "id", port)
    changes = product_changes(before, row, old_ports, {p["port"]: p.get("columns", {}) for p in ports})
    _upsert(db, "catalog", "product_env", "id", {"id": f"{pid}:{env}", "product_id": pid, "env": env, "version": row["version"],
                                                 "status": row["status"], "deprecated_on": row["deprecated_on"] or "",
                                                 "published_at": _now()})
    _audit(db, by, "publish product", pid, {"env": env, "ports": [p["port"] for p in ports],
                                            **({"changes": changes} if changes else {})})
    db.commit()
    out = dict(row, changes=changes)
    if changes:
        out["followers"] = product_followers(db, pid)
    return out


def product_changes(before: dict[str, Any] | None, after: dict[str, Any], old_ports: dict[str, dict[str, Any]],
                    new_ports: dict[str, dict[str, Any]]) -> list[str]:
    """What a publish changed in one environment that a product's readers should hear about, in words:
    deprecated, a new version, and a port's columns gone, added or retyped. ``before`` is what that
    environment last published; the first publish there (or before this was kept) has nothing to compare with.
    A port left out of a publish isn't news here: its old row stays, and promote's breaking-change check
    already stops a port that readers use from going."""
    if before is None:
        return []
    out = []
    if after["status"] == "deprecated" and before.get("status") != "deprecated":
        stops = after.get("deprecated_on")
        out.append(f"Deprecated, stops {stops}" if stops else "Deprecated")
    if str(before.get("version") or "") != after["version"]:
        out.append(f"Version {before.get('version') or '?'} to {after['version']}")
    for port in sorted(set(old_ports) & set(new_ports)):
        old, new = old_ports[port] or {}, new_ports[port] or {}
        gone = sorted(set(old) - set(new))
        added = sorted(set(new) - set(old))
        retyped = sorted(c for c in set(old) & set(new) if str(old[c]) != str(new[c]))
        parts = [f"{', '.join(gone)} removed" if gone else "", f"{', '.join(added)} added" if added else "",
                 f"{', '.join(retyped)} retyped" if retyped else ""]
        if any(parts):
            out.append(f"Port {port}: {'; '.join(x for x in parts if x)}")
    return out


# --------------------------------------------------------------------------- following a product
# A person follows a product to hear when it changes (deprecated, a new version, columns changed).
# Whoever links a project to a product follows it too, so readers hear without having to ask.

def ensure_follows(db: Db) -> list[str]:
    """catalog.follow and catalog.product_env, added after the mesh: additive, safe to run again; the
    follow operations and publishing call it."""
    added = []
    for table in ("follow", "product_env"):
        if not db.exists("catalog", table):
            db.run(f"CREATE TABLE {db.t('catalog', table)} ({', '.join(_column(db, n, k) for n, k in _CATALOG[table])})")
            added.append(f"catalog.{table}")
    if added:
        db.commit()
    return added


def follow_product(db: Db, product_id: str, by: str = "") -> list[str]:
    """Follow a product as the caller. Returns the products the caller follows."""
    ensure_follows(db)
    who = _who(by)
    if not who:
        raise PlatformError("Sign in to follow a product")
    if _row(db, "product", "id", product_id) is None:
        raise PlatformError(f"{product_id} isn't a published data product")
    _upsert(db, "catalog", "follow", "id", {"id": f"{who}|{product_id}", "user_name": who, "product_id": product_id,
                                            "followed_at": _now()})
    db.commit()
    return list_follows(db, by)


def unfollow_product(db: Db, product_id: str, by: str = "") -> list[str]:
    ensure_follows(db)
    db.run(f"DELETE FROM {db.t('catalog', 'follow')} WHERE id = ?", (f"{_who(by)}|{product_id}",))
    db.commit()
    return list_follows(db, by)


def list_follows(db: Db, by: str = "") -> list[str]:
    """The products the caller follows."""
    ensure_follows(db)
    return sorted(r["product_id"] for r in db.rows(f"SELECT product_id FROM {db.t('catalog', 'follow')} WHERE user_name = ?",
                                                     (_who(by),)))


def product_followers(db: Db, product_id: str) -> list[str]:
    """Everyone who hears about a product's changes: its followers and whoever linked a project to it."""
    ensure_follows(db)
    people = {r["user_name"] for r in db.rows(f"SELECT user_name FROM {db.t('catalog', 'follow')} WHERE product_id = ?",
                                              (product_id,))}
    people |= {_who(r["updated_by"]) for r in db.rows(f"SELECT updated_by FROM {db.t('catalog', 'link')} WHERE product_id = ?",
                                                      (product_id,)) if r["updated_by"]}
    return sorted(p for p in people if p)


def list_products(db: Db) -> list[dict[str, Any]]:
    products = db.rows(f"SELECT * FROM {db.t('catalog', 'product')}")
    ports = db.rows(f"SELECT * FROM {db.t('catalog', 'port')}")
    links = db.rows(f"SELECT * FROM {db.t('catalog', 'link')}")
    grants = db.rows(f"SELECT * FROM {db.t('catalog', 'grant')}")
    for p in products:
        p["granted"] = sorted(g["project"] for g in grants if g["product_id"] == p["id"])
        p["ports"] = [dict(x, columns=json.loads(x["columns"] or "{}")) for x in ports if x["product_id"] == p["id"]]
        p["consumers"] = [dict(l, reads=json.loads(l["reads"] or "{}")) for l in links if l["product_id"] == p["id"]]
    return sorted(products, key=lambda p: p["id"])


def resolve_port(db: Db, product_id: str, port: str, env: str) -> dict[str, Any] | None:
    found = _row(db, "port", "id", f"{product_id}:{port}:{env}")
    if found:
        found["columns"] = json.loads(found["columns"] or "{}")
    return found


def register_link(db: Db, consumer: str, alias: str, product_id: str, port: str, reads: dict[str, str],
                  by: str = "") -> dict[str, Any]:
    """A consumer project's link to a product port. Refused if the product isn't open to it."""
    product = _row(db, "product", "id", product_id)
    if product is None:
        raise PlatformError(f"{product_id} isn't a published data product")
    if product["status"] == "draft":
        raise PlatformError(f"{product_id} is still a draft; its owner publishes it on promote")
    granted = _row(db, "grant", "id", f"{product_id}|{consumer}") is not None
    if product["access"] == "private" and product["project"] != consumer and not granted:
        raise PlatformError(f"{product_id} is private to {product['project']}. Ask its owner: Request access")
    if product["access"] == "domain" and not granted:
        mine = _row(db, "project", "name", consumer) or {}
        if mine.get("domain_id") != product.get("domain_id"):
            raise PlatformError(f"{product_id} is open to the {product.get('domain_id')} domain only. "
                                "Ask its owner: Request access")
    row = {"id": f"{consumer}:{alias}", "consumer_project": consumer, "alias": alias, "product_id": product_id,
           "port": port, "reads": json.dumps(reads), "updated_at": _now(), "updated_by": by}
    _upsert(db, "catalog", "link", "id", row)
    _audit(db, by, "link", row["id"], {"product": product_id, "port": port, "reads": reads})
    db.commit()
    return row


def save_enrichment(db: Db, target: str, fields: dict[str, Any], by: str = "") -> dict[str, Any]:
    """Description, tags, classification, glossary terms, steward or certification of a table or
    column. ``target`` is "<project>:<entity>" or "<project>:<entity>#<column>". Only the fields
    given change."""
    target = str(target or "").strip()
    if ":" not in target:
        raise PlatformError("target is <project>:<entity> or <project>:<entity>#<column>")
    current = _row(db, "enrichment", "target", target) or {"target": target}
    if "classification" in fields and fields["classification"] not in ("", None, *CLASSIFICATIONS):
        raise PlatformError(f"classification must be one of {', '.join(CLASSIFICATIONS)}")
    row = dict(current)
    for f in ("description", "classification", "steward", "certified"):
        if f in fields:
            row[f] = str(fields[f] or "")
    for f in ("tags", "terms"):
        if f in fields:
            items = fields[f] if isinstance(fields[f], list) else [x.strip() for x in str(fields[f] or "").split(",")]
            row[f] = json.dumps(sorted({str(x).strip() for x in items if str(x).strip()}))
    row["updated_at"], row["updated_by"] = _now(), by
    _upsert(db, "catalog", "enrichment", "target", row)
    _audit(db, by, "enrich", target, {k: fields[k] for k in fields})
    db.commit()
    return _enrichment_out(row)


def _enrichment_out(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for f in ("tags", "terms"):
        out[f] = json.loads(row.get(f) or "[]")
    return out


def enrichment(db: Db, project: str) -> dict[str, dict[str, Any]]:
    """Every enrichment saved for a project's tables and columns, by target."""
    rows = db.rows(f"SELECT * FROM {db.t('catalog', 'enrichment')} WHERE target LIKE ?", (f"{project}:%",))
    return {r["target"]: _enrichment_out(r) for r in rows}


def list_glossary(db: Db) -> list[dict[str, Any]]:
    return sorted(db.rows(f"SELECT * FROM {db.t('catalog', 'glossary')}"), key=lambda g: g["term"].lower())


def save_term(db: Db, term: dict[str, Any], by: str = "") -> dict[str, Any]:
    name = str(term.get("term") or "").strip()
    if not name:
        raise PlatformError("A glossary term needs a name")
    if not is_admin(db, by):
        before = _row(db, "glossary", "term", name)
        domains = {term.get("domain_id") or None} | ({before.get("domain_id")} if before else set())
        owners = [_names((_row(db, "domain", "id", d) or {}).get("owners")) if d else set() for d in domains]
        if not all(_who(by) in o for o in owners):
            raise PlatformError("Only platform admins, or the owners of the term's domain, can change the glossary")
    row = {"term": name, "definition": str(term.get("definition") or ""), "domain_id": term.get("domain_id") or None,
           "owner": str(term.get("owner") or ""), "updated_at": _now(), "updated_by": by}
    _upsert(db, "catalog", "glossary", "term", row)
    _audit(db, by, "save term", name)
    db.commit()
    return row


def audit(db: Db, limit: int = 100) -> list[dict[str, Any]]:
    top = f"TOP ({int(limit)}) " if db.tsql else ""
    tail = "" if db.tsql else f" LIMIT {int(limit)}"
    return db.rows(f"SELECT {top}* FROM {db.t('catalog', 'audit')} ORDER BY id DESC{tail}")


WORKLOAD_ACTIONS = ("run", "run_in_fabric", "run_item", "run_schedule", "cancel", "assign", "link", "unlink")
WORKLOAD_STATES = ("submitted", "succeeded", "failed", "cancelled", "changed")
_WORKLOAD_FIELDS = ("id", "name", "env", "where", "target", "identity", "before", "after", "detail", "at")


def log_workload_action(db: Db, workload: str, action: str, status: str, entry: dict[str, Any] | None = None,
                        by: str = "") -> dict[str, Any]:
    """What someone did from a workload page in the app, and how it ended, on the platform's audit.
    Who did it is the caller (in Fabric, Fabric's own context); the rest is what the app reports,
    each value cut to 500 characters. ``entry.id`` is the app's own record, so a line the app sent
    twice can be told apart."""
    if action not in WORKLOAD_ACTIONS:
        raise PlatformError(f"action is one of {', '.join(WORKLOAD_ACTIONS)}")
    if status not in WORKLOAD_STATES:
        raise PlatformError(f"status is one of {', '.join(WORKLOAD_STATES)}")
    workload = str(workload or "").strip()
    if not workload:
        raise PlatformError("say which workload")
    detail = {k: str(v)[:500] for k, v in (entry or {}).items() if k in _WORKLOAD_FIELDS and v not in (None, "")}
    _audit(db, by, f"workload {action} {status}", f"workload:{workload[:300]}", detail or None)
    db.commit()
    return {"recorded": True}


# --------------------------------------------------------------------------- who may change what


def _who(by: str) -> str:
    return str(by or "").strip().lower()


def _names(value: Any) -> set[str]:
    """People in an owners field: emails or names separated by commas or semicolons."""
    return {x.strip().lower() for x in re.split(r"[,;]", str(value or "")) if x.strip()}


def list_admins(db: Db) -> list[dict[str, Any]]:
    return sorted(db.rows(f"SELECT * FROM {db.t('catalog', 'admin')}"), key=lambda a: a["user_name"])


def is_admin(db: Db, by: str) -> bool:
    who = _who(by)
    if not who:
        # Only the SQLite platform on someone's own PC is called without a name (signed out):
        # it's theirs alone. In Fabric every call carries the caller.
        return not db.tsql
    return _row(db, "admin", "user_name", who) is not None


def require_admin(db: Db, by: str, action: str) -> None:
    if is_admin(db, by):
        return
    if not list_admins(db):
        raise PlatformError(f"Can't {action}: the platform has no admins yet. The person who set it up claims "
                            "admin in Platform > Settings.")
    raise PlatformError(f"Only platform admins can {action}. Send a request in Platform > Requests, "
                        "or ask a platform admin.")


def claim_admin(db: Db, by: str = "") -> dict[str, Any]:
    """Become the first platform admin. Allowed only while the platform has none."""
    who = _who(by)
    if not who:
        raise PlatformError("Sign in to become a platform admin")
    if list_admins(db):
        if is_admin(db, who):
            return whoami(db, who)
        raise PlatformError("This platform already has admins. Ask one of them to add you.")
    _upsert(db, "catalog", "admin", "user_name", {"user_name": who, "added_at": _now(), "added_by": who})
    _audit(db, who, "claim admin", who)
    db.commit()
    return whoami(db, who)


def add_admin(db: Db, user: str, by: str = "") -> list[dict[str, Any]]:
    require_admin(db, by, "add platform admins")
    who = _who(user)
    if not who:
        raise PlatformError("Give the new admin's sign-in name, like ana@contoso.com")
    _upsert(db, "catalog", "admin", "user_name", {"user_name": who, "added_at": _now(), "added_by": _who(by)})
    _audit(db, by, "add admin", who)
    db.commit()
    return list_admins(db)


def remove_admin(db: Db, user: str, by: str = "") -> list[dict[str, Any]]:
    require_admin(db, by, "remove platform admins")
    who = _who(user)
    admins = list_admins(db)
    if [a["user_name"] for a in admins] == [who]:
        raise PlatformError("A platform keeps at least one admin. Add another before removing this one.")
    db.run(f"DELETE FROM {db.t('catalog', 'admin')} WHERE user_name = ?", (who,))
    _audit(db, by, "remove admin", who)
    db.commit()
    return list_admins(db)


def whoami(db: Db, by: str = "") -> dict[str, Any]:
    """The caller as the platform sees them: what they may do, and the domains they own."""
    who = _who(by)
    admins = list_admins(db)
    owns = [d["id"] for d in db.rows(f"SELECT id, owners FROM {db.t('catalog', 'domain')}") if who and who in _names(d["owners"])]
    return {"user": who, "admin": is_admin(db, who), "admins": len(admins), "can_claim": not admins and bool(who),
            "domains_owned": owns}


# --------------------------------------------------------------------------- key tables and sources


def list_key_tables(db: Db) -> list[dict[str, Any]]:
    """Every key table in the platform, with how many keys it holds."""
    registered = {r["name"]: r for r in db.rows(f"SELECT * FROM {db.t('catalog', 'key_table')}")}
    if db.tsql:
        physical = [r["name"] for r in db.rows("SELECT t.name FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id "
                                               "WHERE s.name = 'keys'")]
    else:
        physical = [r["name"][len("keys__"):] for r in db.rows("SELECT name FROM sqlite_master WHERE type = 'table' "
                                                              "AND name LIKE 'keys\\_\\_%' ESCAPE '\\'")]
    out = []
    for name in sorted(set(physical) | set(registered)):
        if not _NAME.match(name):
            continue
        row = dict(registered.get(name) or {"name": name, "business_key": "", "description": "", "domain_id": None,
                                            "created_at": "", "created_by": ""})
        out.append(row | key_stats(db, name))
    return out


# Columns catalog.source gained in schema 5, when it became the one list of source systems.
_SOURCE_ADDED = ("source_id", "code", "system_type", "active")


def _columns(db: Db, table: str) -> set[str]:
    if db.tsql:
        return {r["name"] for r in db.rows(f"SELECT name FROM sys.columns WHERE object_id = OBJECT_ID('catalog.{table}')")}
    return {r["name"] for r in db.rows(f"PRAGMA table_info({db.t('catalog', table)})")}


def ensure_source_ids(db: Db) -> list[str]:
    """Give catalog.source the columns of schema 5 and every source an id. A platform made before
    keeps its sources: each gets the next free id, in name order, and a code from its name."""
    added = []
    have = _columns(db, "source")
    for col in _SOURCE_ADDED:
        if col not in have:
            db.run(f"ALTER TABLE {db.t('catalog', 'source')} ADD {'' if db.tsql else 'COLUMN '}{_column(db, col, 'text')}")
            added.append(f"catalog.source.{col}")
    rows = db.rows(f"SELECT name, source_id, code, active FROM {db.t('catalog', 'source')}")
    used = [int(r["source_id"]) for r in rows if str(r["source_id"] or "").isdigit()]
    nxt = max(used, default=0) + 1
    for r in sorted(rows, key=lambda r: r["name"]):
        if not str(r["source_id"] or "").isdigit():
            db.run(f"UPDATE {db.t('catalog', 'source')} SET source_id = ? WHERE name = ?", (str(nxt), r["name"]))
            nxt += 1
        if not r["code"]:
            db.run(f"UPDATE {db.t('catalog', 'source')} SET code = ? WHERE name = ?", (r["name"].upper(), r["name"]))
        if r["active"] in (None, ""):
            db.run(f"UPDATE {db.t('catalog', 'source')} SET active = '1' WHERE name = ?", (r["name"],))
    db.commit()
    return added


def _with_source_ids(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The schema 5 fields as ensure_source_ids would set them, worked out without writing: a read
    never migrates the catalog (that's Finish or repair, or the next change to a source)."""
    used = [int(s["source_id"]) for s in sources if str(s.get("source_id") or "").isdigit()]
    nxt = max(used, default=0) + 1
    for s in sorted(sources, key=lambda s: s["name"]):
        if not str(s.get("source_id") or "").isdigit():
            s["source_id"], nxt = str(nxt), nxt + 1
        s["code"] = s.get("code") or s["name"].upper()
        s["system_type"] = s.get("system_type") or ""
        if s.get("active") in (None, ""):
            s["active"] = "1"
    return sources


def list_sources(db: Db) -> list[dict[str, Any]]:
    sources = _with_source_ids(db.rows(f"SELECT * FROM {db.t('catalog', 'source')}"))
    readers = db.rows(f"SELECT * FROM {db.t('catalog', 'reader')}")
    for src in sources:
        src["connections"] = json.loads(src["connections"] or "{}")
        src["source_id"] = int(src["source_id"])
        src["active"] = src["active"] != "0"
        src["zone"] = src.get("zone") or ""  # empty: the Shared zone
        src["readers"] = [{"project": r["project"], "entities": json.loads(r["entities"] or "[]")}
                          for r in readers if r["source"] == src["name"]]
    return sorted(sources, key=lambda x: x["name"])


def _source_row(source: dict[str, Any], by: str) -> dict[str, Any]:
    code = str(source.get("code") or "").strip()
    name = key_name(source.get("name") or code)
    code = code or name.upper()
    if not _CODE.match(code) or code.lower() != name:
        raise PlatformError(f"{code!r}: a source's code is its folder name in any case, like {name.upper()}")
    hours = str(source.get("expected_hours") or "").strip()
    if hours and not hours.replace(".", "", 1).isdigit():
        raise PlatformError("Expected every ... hours is a number, like 24")
    conns = source.get("connections") or {}
    if not isinstance(conns, dict):
        raise PlatformError("connections is one set of fields per environment")
    for env, fields in conns.items():
        for k, v in (fields or {}).items():
            if any(w in k.lower() for w in ("password", "secret_value", "token")) and v:
                raise PlatformError(f"{env}.{k}: keep secrets in Key Vault and give the secret's name only")
    return {"name": name, "title": str(source.get("title") or name), "description": str(source.get("description") or ""),
            "owner": str(source.get("owner") or ""), "expected_hours": hours, "connections": json.dumps(conns),
            "updated_at": _now(), "updated_by": by, "code": code, "system_type": str(source.get("system_type") or ""),
            "active": "0" if source.get("active") is False else "1",
            # None keeps the zone a source already has (callers that don't know zones, like adopt_sources).
            "zone": None if source.get("zone") is None else _source_zone(source.get("zone"))}


def _put_source(db: Db, row: dict[str, Any], source_id: Any = None) -> None:
    """Add or change one source. An existing source keeps its id; a new one takes ``source_id``
    (a project's own id, when sources move to the platform) or the next free one."""
    ensure_source_ids(db)
    ensure_landing_zones(db)
    before = _row(db, "source", "name", row["name"])
    if row.get("zone") is None:
        row = row | {"zone": (before or {}).get("zone") or ""}
    if row["zone"] and not _row(db, "landing_zone", "id", row["zone"]):
        raise PlatformError(f"{row['zone']} isn't a landing zone: pick one from Platform > Set up > Landing zone")
    if before:
        row = row | {"source_id": before["source_id"]}
    else:
        ids = {int(r["source_id"]): r["name"] for r in db.rows(f"SELECT name, source_id FROM {db.t('catalog', 'source')}")}
        if source_id not in (None, ""):
            try:
                sid = int(source_id)
            except (TypeError, ValueError):
                raise PlatformError("A source system id is a whole number") from None
            if sid <= 0:
                raise PlatformError("A source system id is a whole number above 0")
            if sid in ids:
                raise PlatformError(f"id {sid} is already {ids[sid]} in the platform")
        else:
            sid = max(ids, default=0) + 1
        row = row | {"source_id": str(sid)}
    _upsert(db, "catalog", "source", "name", row)


def save_source(db: Db, source: dict[str, Any], by: str = "") -> list[dict[str, Any]]:
    """Add or change a source system, landed in LH_Landing (Files/<name>/<entity>/) and read by
    projects with the same id everywhere. ``zone`` is its landing zone's id; empty is the Shared
    zone. Platform admins only."""
    row = _source_row(source, by)
    require_admin(db, by, "add or change source systems")
    _put_source(db, row)
    _audit(db, by, "save source", row["name"])
    db.commit()
    return list_sources(db)


def adopt_sources(db: Db, systems: list[dict[str, Any]], project: str = "", by: str = "") -> dict[str, Any]:
    """Move a project's own source systems into the platform's list, keeping their ids (like Move
    keys). A source the platform already has with the same id is left as it is; one whose code or
    id the platform uses for something else is reported, never changed. Platform admins only."""
    require_admin(db, by, "move source systems to the platform")
    ensure_source_ids(db)
    moved, already, conflicts = [], [], []
    for s in systems or []:
        code = str(s.get("code") or "").strip()
        try:
            row = _source_row({"name": code.lower(), "code": code, "title": s.get("name") or code,
                               "description": s.get("description"), "owner": s.get("owner"),
                               "system_type": s.get("system_type"), "active": s.get("active", True)}, by)
            sid = int(s.get("id") or 0)
        except (PlatformError, TypeError, ValueError) as exc:
            conflicts.append(f"{code or '?'}: {exc}")
            continue
        have = _row(db, "source", "name", row["name"])
        if have:
            if int(have["source_id"]) == sid:
                already.append(code)
            else:
                conflicts.append(f"{code} is id {have['source_id']} in the platform but {sid} in this project")
            continue
        try:
            _put_source(db, row, sid)
        except PlatformError as exc:
            conflicts.append(f"{code}: {exc}")
            continue
        moved.append(code)
    if moved:
        _audit(db, by, "move sources", project or "a project", {"moved": moved})
    db.commit()
    return {"moved": moved, "already": already, "conflicts": conflicts, "sources": list_sources(db)}


def delete_source(db: Db, name: str, by: str = "") -> list[dict[str, Any]]:
    name = key_name(name)
    require_admin(db, by, "remove source systems")
    readers = db.rows(f"SELECT project FROM {db.t('catalog', 'reader')} WHERE source = ?", (name,))
    if readers:
        raise PlatformError(f"{name} is still read by {', '.join(sorted({r['project'] for r in readers}))}")
    db.run(f"DELETE FROM {db.t('catalog', 'source')} WHERE name = ?", (name,))
    _audit(db, by, "delete source", name)
    db.commit()
    return list_sources(db)


# --------------------------------------------------------------------------- landing zones (Preview)

# One landing workspace read by Dev, Test and Prod, split into zones. Each zone is its own lakehouse:
# Shared (LH_Landing) for every domain, a domain's own (LH_Landing_HR), or a secure one for PII or PHI
# (LH_Landing_HR_Secure) that only its readers may read. A zone's devtest says whether Dev and Test
# read it ("read") or only Prod does ("none", the default for a new zone). The app creates each
# lakehouse in Fabric (central/setup.py); this records it. Access roles and labels are set in Fabric.
# Shared sources keep reading each environment's own LH_Landing until a platform admin switches reads
# to the landing workspace (switch_landing_reads: switched_at on the Shared zone), so the move never
# breaks a Prod run before its files are there. Every other zone is new, so it's read from the start.
SHARED_ZONE = "shared"
DEVTEST = ("none", "read")


def zone_key(name: Any) -> str:
    """A zone's id from its name: HR-Secure is hr_secure."""
    zid = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    if not zid or not zid[0].isalpha() or len(zid) > 40:
        raise PlatformError(f"{name!r}: a landing zone's name starts with a letter and has letters, digits, "
                            "spaces, - or _, up to 40")
    return zid


def zone_lakehouse(name: Any) -> str:
    """The lakehouse a zone lives in: LH_Landing for Shared, LH_Landing_<Name> for the rest."""
    if zone_key(name) == SHARED_ZONE:
        return "LH_Landing"
    return "LH_Landing_" + re.sub(r"[^A-Za-z0-9]+", "_", str(name).strip()).strip("_")


def _source_zone(value: Any) -> str:
    zid = str(value or "").strip()
    return "" if not zid or zone_key(zid) == SHARED_ZONE else zone_key(zid)


def ensure_landing_zones(db: Db) -> list[str]:
    """Give a platform made before landing zones catalog.landing_zone and catalog.source.zone.
    Additive only, safe to run again."""
    added = []
    if not db.exists("catalog", "landing_zone"):
        db.run(f"CREATE TABLE {db.t('catalog', 'landing_zone')} "
               f"({', '.join(_column(db, n, k) for n, k in _CATALOG['landing_zone'])})")
        added.append("catalog.landing_zone")
    else:
        have = _columns(db, "landing_zone")
        for col in ("switched_at", "switched_by"):
            if col not in have:
                db.run(f"ALTER TABLE {db.t('catalog', 'landing_zone')} ADD {'' if db.tsql else 'COLUMN '}{_column(db, col, 'text')}")
                added.append(f"catalog.landing_zone.{col}")
    if db.exists("catalog", "source") and "zone" not in _columns(db, "source"):
        db.run(f"ALTER TABLE {db.t('catalog', 'source')} ADD {'' if db.tsql else 'COLUMN '}{_column(db, 'zone', 'text')}")
        added.append("catalog.source.zone")
    if added:
        db.commit()
    return added


def _zone_out(row: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    zone = dict(row)
    zone["secure"] = zone.get("secure") == "1"
    zone["readers"] = json.loads(zone.get("readers") or "[]")
    zone["devtest"] = zone.get("devtest") or ("read" if zone["id"] == SHARED_ZONE else "none")
    zone["sources"] = sorted(s["name"] for s in sources if (s.get("zone") or SHARED_ZONE) == zone["id"])
    # Read from the landing workspace: every zone but Shared at once, Shared once reads are switched.
    zone["switched_at"], zone["switched_by"] = zone.get("switched_at") or "", zone.get("switched_by") or ""
    zone["read"] = zone["id"] != SHARED_ZONE or bool(zone["switched_at"])
    return zone


def list_landing_zones(db: Db) -> list[dict[str, Any]]:
    """Every landing zone, Shared first, with the sources in each. Empty until the central landing
    workspace is set up (a read never migrates the catalog)."""
    if not db.exists("catalog", "landing_zone"):
        return []
    sources = db.rows(f"SELECT * FROM {db.t('catalog', 'source')}")
    zones = [_zone_out(r, sources) for r in db.rows(f"SELECT * FROM {db.t('catalog', 'landing_zone')}")]
    return sorted(zones, key=lambda z: (z["id"] != SHARED_ZONE, (z["name"] or "").lower()))


def save_landing_zone(db: Db, zone: dict[str, Any], by: str = "") -> list[dict[str, Any]]:
    """Add or change a landing zone: its name, domain, readers, whether it's secure, its label, and
    whether Dev and Test read it. Where it lives (workspace and lakehouse) is kept once recorded.
    Platform admins only."""
    name = str(zone.get("name") or "").strip()
    zid = zone_key(zone.get("id") or name)
    require_admin(db, by, "add or change landing zones")
    ensure_landing_zones(db)
    before = _row(db, "landing_zone", "id", zid) or {}
    name = name or before.get("name") or ""
    if not name:
        raise PlatformError("A landing zone needs a name")
    devtest = str(zone.get("devtest") or before.get("devtest") or ("read" if zid == SHARED_ZONE else "none"))
    if devtest not in DEVTEST:
        raise PlatformError("Dev and Test either don't read the zone (none) or read it like Prod (read)")
    domain = str((zone["domain_id"] if "domain_id" in zone else before.get("domain_id")) or "").strip()
    if domain:
        domain = domain_id(domain)
        if not _row(db, "domain", "id", domain):
            raise PlatformError(f"domain {domain} doesn't exist: add it first in Platform > Set up > Domains")
    readers = zone["readers"] if "readers" in zone else json.loads(before.get("readers") or "[]")
    if isinstance(readers, str):
        readers = re.split(r"[,;\n]", readers)
    readers = sorted({str(r).strip() for r in readers or [] if str(r).strip()})
    secure = bool(zone["secure"]) if "secure" in zone else before.get("secure") == "1"
    if zid == SHARED_ZONE and secure:
        raise PlatformError("The Shared zone is read by every domain, so it can't be secure: make a secure zone for that data")
    row = {"id": zid, "name": name, "domain_id": domain or None,
           "workspace_id": str(zone.get("workspace_id") or before.get("workspace_id") or ""),
           "workspace_name": str(zone.get("workspace_name") or before.get("workspace_name") or ""),
           "lakehouse_id": str(zone.get("lakehouse_id") or before.get("lakehouse_id") or ""),
           "lakehouse_name": str(zone.get("lakehouse_name") or before.get("lakehouse_name") or zone_lakehouse(name)),
           "secure": "1" if secure else "0", "readers": json.dumps(readers),
           "label": str((zone["label"] if "label" in zone else before.get("label")) or "").strip(),
           "devtest": devtest, "updated_at": _now(), "updated_by": by}
    _upsert(db, "catalog", "landing_zone", "id", row)
    _audit(db, by, "save landing zone", zid, {k: v for k, v in row.items() if k not in ("updated_at", "updated_by")})
    db.commit()
    return list_landing_zones(db)


def switch_landing_reads(db: Db, by: str = "") -> list[dict[str, Any]]:
    """From now on every environment reads Shared sources from the landing workspace's LH_Landing
    instead of its own. Done once, after the files runs need are there. Platform admins only."""
    require_admin(db, by, "switch reads to the landing workspace")
    ensure_landing_zones(db)
    shared = _row(db, "landing_zone", "id", SHARED_ZONE)
    if not shared or not shared.get("lakehouse_id"):
        raise PlatformError("Set up the landing workspace first: it has no Shared zone yet")
    if not shared.get("switched_at"):
        db.run(f"UPDATE {db.t('catalog', 'landing_zone')} SET switched_at = ?, switched_by = ? WHERE id = ?",
               (_now(), by, SHARED_ZONE))
        _audit(db, by, "switch landing reads", SHARED_ZONE, {"lakehouse_id": shared["lakehouse_id"]})
        db.commit()
    return list_landing_zones(db)


def delete_landing_zone(db: Db, id: str, by: str = "") -> list[dict[str, Any]]:
    """Take a zone out of the catalog. Its lakehouse and files stay in Fabric. Refused while a source
    is in it (a source with no zone is in Shared). Platform admins only."""
    zid = zone_key(id)
    require_admin(db, by, "remove landing zones")
    zone = next((z for z in list_landing_zones(db) if z["id"] == zid), None)
    if zone is None:
        raise PlatformError(f"{zid} isn't a landing zone")
    if zone["sources"]:
        used = zone["sources"]
        raise PlatformError(f"{zone['name']} still holds {', '.join(used)}: move {'it' if len(used) == 1 else 'them'} "
                            "to another zone first")
    db.run(f"DELETE FROM {db.t('catalog', 'landing_zone')} WHERE id = ?", (zid,))
    _audit(db, by, "delete landing zone", zid)
    db.commit()
    return list_landing_zones(db)


def _save_readers(db: Db, project: str, landing: dict[str, list[str]]) -> None:
    db.run(f"DELETE FROM {db.t('catalog', 'reader')} WHERE project = ?", (project,))
    for source, entities in sorted((landing or {}).items()):
        db.run(f"INSERT INTO {db.t('catalog', 'reader')} (id, project, source, entities, updated_at) VALUES (?, ?, ?, ?, ?)",
               (f"{project}:{source}", project, source, json.dumps(sorted(entities)), _now()))


# --------------------------------------------------------------------------- requests


def _product_owners(product: dict[str, Any]) -> set[str]:
    return _names(product.get("owner"))


def workspace_name(value: Any) -> str:
    """A Fabric workspace's name as Fabric takes it: up to 256 characters, none of \\ / : * ? " < > | # %."""
    name = re.sub(r"\s+", " ", str(value or "")).strip()
    if not name or len(name) > 256 or _WS_BAD.search(name):
        raise PlatformError(f"{name!r} isn't a workspace name: up to 256 characters, without \\ / : * ? \" < > | # %")
    return name


def principal(value: Any) -> dict[str, str]:
    """A person by email or UPN, or a group by its object ID (Microsoft Entra), as a request stores it."""
    if isinstance(value, dict):
        text, name = str(value.get("id") or value.get("email") or "").strip(), str(value.get("name") or "").strip()
    else:
        text, name = str(value or "").strip(), ""
    if _GUID.match(text):
        return {"type": "Group", "id": text.lower(), "email": "", "name": name or text.lower()}
    if _EMAIL.match(text):
        return {"type": "User", "id": "", "email": text.lower(), "name": name or text.lower()}
    raise PlatformError(f"{text or 'Nobody'} isn't a person's email or UPN, or a group's object ID")


def _principals(values: Any) -> list[dict[str, str]]:
    items = values if isinstance(values, list) else [v for v in re.split(r"[,;\n]", str(values or "")) if v.strip()]
    out, seen = [], set()
    for v in items:
        p = principal(v)
        if (p["id"] or p["email"]) not in seen:
            seen.add(p["id"] or p["email"])
            out.append(p)
    return out


def _check_workspace(db: Db, target: str, detail: dict[str, Any], reason: str) -> tuple[str, dict[str, Any]]:
    name = workspace_name(detail.get("name") or target)
    capacity = str(detail.get("capacity_id") or "").strip().lower()
    if capacity and not _GUID.match(capacity):
        raise PlatformError("Pick the capacity from the list")
    domain = str(detail.get("domain_id") or "").strip()
    row = _row(db, "domain", "id", domain_id(domain)) if domain else None
    if domain and row is None:
        raise PlatformError(f"{domain} isn't one of the platform's domains")
    admins = _principals(detail.get("admins"))
    if not admins:
        raise PlatformError("Say who its admins are: a person's email or UPN, or a group's object ID")
    if not str(reason or "").strip():
        raise PlatformError("Say what the workspace is for")
    return name, {"name": name, "capacity_id": capacity, "capacity_name": str(detail.get("capacity_name") or ""),
                  "domain_id": row["id"] if row else "", "domain_name": row["name"] if row else "",
                  "fabric_domain": (row or {}).get("fabric_domain") or "", "admins": admins}


def _check_workspace_access(db: Db, target: str, detail: dict[str, Any], by: str) -> tuple[str, dict[str, Any]]:
    wid = str(detail.get("workspace_id") or "").strip().lower()
    if not _GUID.match(wid):
        raise PlatformError("Pick the workspace, or paste its ID from its address in Fabric")
    role = str(detail.get("role") or "").strip().title()
    if role not in WORKSPACE_ROLES:
        raise PlatformError(f"role must be one of {', '.join(WORKSPACE_ROLES)}")
    if str(detail.get("who") or "me") == "me":
        if not _EMAIL.match(_who(by)):
            raise PlatformError("Sign in, so the request says who you are")
        who = principal(_who(by))
    else:
        who = principal({"id": detail.get("group_id"), "name": detail.get("group_name")})
        if who["type"] != "Group":
            raise PlatformError("A group is given by its object ID (Microsoft Entra admin center > Groups)")
    name = str(detail.get("workspace_name") or target or wid).strip()
    return name, {"workspace_id": wid, "workspace_name": name, "role": role, "principal": who}


def _check_domain(db: Db, target: str, detail: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    name = str(detail.get("name") or target or "").strip()
    if not name:
        raise PlatformError("A domain needs a name")
    did = domain_id(detail.get("id") or name)
    if _row(db, "domain", "id", did):
        raise PlatformError(f"{did} is already a domain")
    parent = str(detail.get("parent_id") or "").strip()
    if parent:
        parent = domain_id(parent)
        p = _row(db, "domain", "id", parent)
        if p is None:
            raise PlatformError(f"parent domain {parent} doesn't exist")
        if p.get("parent_id"):
            raise PlatformError("Subdomains go one level deep: pick a top-level domain as the parent")
    return did, {"id": did, "name": name, "parent_id": parent, "description": str(detail.get("description") or ""),
                 "owners": str(detail.get("owners") or "").strip()}


DATA_ASKS = ("new", "change")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _check_data_request(db: Db, target: str, detail: dict[str, Any], reason: str) -> tuple[str, dict[str, Any]]:
    """Ask another team for data: a new data product from a domain (its owners decide), or a change to
    a product (its owner decides). Accepting records that the producing team will do it; nothing is made."""
    what = str(detail.get("what") or "")
    if what not in DATA_ASKS:
        raise PlatformError("Say whether you need a new data product or a change to one")
    if not str(reason or "").strip():
        raise PlatformError("Say what you need, so the team can answer")
    needed_by = str(detail.get("needed_by") or "").strip()
    if needed_by:
        try:
            if not _DAY.match(needed_by):
                raise ValueError(needed_by)
            dt.date.fromisoformat(needed_by)
        except ValueError:
            raise PlatformError("Needed by is a date like 2026-11-30") from None
    if what == "change":
        product = _row(db, "product", "id", target)
        if product is None or product.get("status") == "draft":
            raise PlatformError(f"{target} isn't a published data product")
        out = {"what": what, "needed_by": needed_by, "product_name": product.get("name") or target,
               "domain_id": product.get("domain_id") or "", "owners": product.get("owner") or ""}
        return target, out
    domain = _row(db, "domain", "id", domain_id(target)) if target else None
    if domain is None:
        raise PlatformError("Pick the domain that should make it")
    return domain["id"], {"what": what, "needed_by": needed_by, "domain_name": domain.get("name") or domain["id"],
                          "domain_id": domain["id"], "owners": domain.get("owners") or ""}


def request(db: Db, kind: str, target: str, project: str = "", reason: str = "", detail: dict[str, Any] | None = None,
            by: str = "") -> dict[str, Any]:
    """Ask for something only someone else may give: a key table, source, workspace, domain or policy
    exception (platform admins), or access to a domain or private data product (its owner). Every
    field is checked now, so a request that couldn't be carried out is refused before anyone decides."""
    if kind not in REQUEST_KINDS:
        raise PlatformError(f"kind must be one of {', '.join(REQUEST_KINDS)}")
    detail = dict(detail or {})
    detail.pop("made", None)  # only the person approving records what was made
    target = str(target or "").strip()
    if kind == "key_table":
        target = key_name(target)
        if db.exists("keys", target):
            raise PlatformError(f"keys.{target} is already there: pick it from the list")
    elif kind == "source":
        target = key_name(target)
        if _row(db, "source", "name", target):
            raise PlatformError(f"{target} is already a source in the landing zone")
    elif kind == "shortcut":
        for need in ("workspace_id", "item_id", "where", "name", "source"):
            if not detail.get(need):
                raise PlatformError("Say which lakehouse, where in it, the shortcut's name and what it points at")
        target = shortcut_id(detail["item_id"], f"{str(detail['where']).strip('/')}/{detail['name']}")
        if _row(db, "shortcut", "id", target):
            raise PlatformError(f"There's already a shortcut {detail['name']} there")
    elif kind == "workspace":
        target, detail = _check_workspace(db, target, detail, reason)
    elif kind == "workspace_access":
        target, detail = _check_workspace_access(db, target, detail, by)
    elif kind == "domain":
        target, detail = _check_domain(db, target, detail)
    elif kind == "data":
        target, detail = _check_data_request(db, target, detail, reason)
    elif kind in REQUEST_HOOKS:
        target, detail = REQUEST_HOOKS[kind]["check"](db, target, detail, reason, by)
    elif kind == "exception":
        raise PlatformError("The platform can't take policy exceptions yet: governance policies aren't set up")
    else:
        product = _row(db, "product", "id", target)
        if product is None:
            raise PlatformError(f"{target} isn't a published data product")
        if not project:
            raise PlatformError("Say which project needs access")
        if product["access"] == "public":
            raise PlatformError(f"{target} is open to everyone: link to it without asking")
        if _row(db, "grant", "id", f"{target}|{project}"):
            raise PlatformError(f"{project} already has access to {target}")
    row = {"id": uuid.uuid4().hex[:12], "kind": kind, "target": target, "detail": json.dumps(detail),
           "project": project or "", "reason": str(reason or ""), "requested_by": _who(by), "requested_at": _now(),
           "status": "waiting", "decided_by": "", "decided_at": "", "note": ""}
    mine = _duplicate_key(_request_out(row))
    for r in db.rows(f"SELECT * FROM {db.t('catalog', 'request')} WHERE kind = ? AND status = 'waiting'", (kind,)):
        if _duplicate_key(_request_out(r)) == mine:
            raise PlatformError(f"There's already a request waiting for {target}, from {r['requested_by'] or 'someone'}")
    _upsert(db, "catalog", "request", "id", row)
    _audit(db, by, f"request {kind}", target, {"project": project})
    db.commit()
    return _request_out(row)


def _duplicate_key(req: dict[str, Any]) -> str:
    """Two waiting requests with the same key are the same request: the same target, and for some
    kinds the same project, person or group, or scope too."""
    d = req.get("detail") or {}
    kind, target = req["kind"], str(req.get("target") or "")
    if kind == "access":
        return f"{target}|{req.get('project') or ''}"
    if kind == "workspace":
        return target.lower()
    if kind == "workspace_access":
        p = d.get("principal") or {}
        return f"{d.get('workspace_id')}|{p.get('id') or p.get('email')}"
    if kind == "exception":
        return f"{target}|{d.get('scope')}|{str(d.get('scope_target') or '').lower()}"
    if kind == "data":
        return f"{target}|{req.get('project') or ''}|{' '.join(str(req.get('reason') or '').lower().split())}"
    return target


def record_request_progress(db: Db, id: str, made: dict[str, Any], by: str = "") -> dict[str, Any]:
    """Note what approving a request has made so far in Fabric (a workspace's id, the admins added),
    so a retry after a failed step finds it instead of making it twice. Only someone who may decide it."""
    req = _row(db, "request", "id", str(id))
    if req is None or req["status"] != "waiting":
        raise PlatformError("Only a waiting request can be carried out")
    req = _request_out(req)
    if not (is_admin(db, by) or _who(by) in _deciders(db, req)):
        raise PlatformError("Only a platform admin can carry out this request")
    detail = dict(req["detail"])
    detail["made"] = {**(detail.get("made") or {}), **{str(k): v for k, v in (made or {}).items()}}
    db.run(f"UPDATE {db.t('catalog', 'request')} SET detail = ? WHERE id = ?", (json.dumps(detail), req["id"]))
    _audit(db, by, f"made for {req['kind']} request", req["target"], {"request": req["id"], "made": made})
    db.commit()
    return _request_out(_row(db, "request", "id", req["id"]) or {})


def _request_out(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row, detail=json.loads(row.get("detail") or "{}"))


def _deciders(db: Db, req: dict[str, Any]) -> set[str]:
    if req["kind"] == "access":
        product = _row(db, "product", "id", req["target"]) or {}
        return _product_owners(product)
    if req["kind"] == "shortcut" and req["detail"].get("product_id"):
        return _product_owners(_row(db, "product", "id", req["detail"]["product_id"]) or {})
    if req["kind"] == "data":
        if req["detail"].get("what") == "change":
            return _product_owners(_row(db, "product", "id", req["target"]) or {})
        return _names((_row(db, "domain", "id", req["target"]) or {}).get("owners"))
    return set()


def list_requests(db: Db, status: str = "", by: str = "") -> list[dict[str, Any]]:
    """Requests, newest first, each saying whether the caller may decide it."""
    rows = db.rows(f"SELECT * FROM {db.t('catalog', 'request')}" + (" WHERE status = ?" if status else ""),
                   (status,) if status else ())
    admin, who = is_admin(db, by), _who(by)
    out = []
    for r in rows:
        r = _request_out(r)
        r["can_decide"] = r["status"] == "waiting" and (admin or who in _deciders(db, r))
        r["mine"] = bool(who) and r["requested_by"] == who
        out.append(r)
    return sorted(out, key=lambda r: r["requested_at"], reverse=True)


def decide_request(db: Db, id: str, approve: bool, note: str = "", by: str = "") -> dict[str, Any]:
    """Approve or decline a waiting request. Approving does what was asked: creates the key table,
    adds the source, or lets the project link to the product."""
    req = _row(db, "request", "id", str(id))
    if req is None:
        raise PlatformError("That request isn't there any more")
    if req["status"] != "waiting":
        raise PlatformError(f"That request was already {req['status']}")
    req = _request_out(req)
    if not (is_admin(db, by) or _who(by) in _deciders(db, req)):
        who = ("the product's owner or a platform admin" if req["kind"] == "access" or _deciders(db, req)
               else "a platform admin")
        if req["kind"] == "data":
            who = ("the product's owner or a platform admin" if req["detail"].get("what") == "change"
                   else "the domain's owners or a platform admin")
        raise PlatformError(f"Only {who} can decide this request")
    if approve:
        d = req["detail"]
        if req["kind"] == "key_table":
            if db.exists("keys", req["target"]):
                raise PlatformError(f"keys.{req['target']} was made since: decline this request")
            _make_key_table(db, req["target"])
            _register_key_table(db, req["target"], d.get("business_key", ""), d.get("description", "") or req["reason"],
                                d.get("domain_id", ""), _who(by))
        elif req["kind"] == "source":
            _put_source(db, _source_row({"name": req["target"], **d}, _who(by)))
        elif req["kind"] == "shortcut":
            pass  # the app creates the shortcut in Fabric first, as the person approving, then approves
        elif req["kind"] == "data":
            pass  # accepting says the producing team will do it; the note says how and when
        elif req["kind"] == "workspace":
            # The app makes it in Fabric first, as the person approving (central/requests_service.py).
            if not (d.get("made") or {}).get("workspace_id"):
                raise PlatformError("Create the workspace first: approve this request in KratosForge")
        elif req["kind"] == "workspace_access":
            if not (d.get("made") or {}).get("assigned"):
                raise PlatformError("Add the role first: approve this request in KratosForge")
        elif req["kind"] == "domain":
            if _row(db, "domain", "id", req["target"]) is None:
                raise PlatformError(f"Save the domain {req['target']} first: approve this request in KratosForge")
        elif req["kind"] in REQUEST_HOOKS:
            REQUEST_HOOKS[req["kind"]]["approve"](db, req, by)
        elif req["kind"] == "exception":
            raise PlatformError("The platform can't record policy exceptions yet: governance policies aren't set up")
        else:
            _upsert(db, "catalog", "grant", "id", {"id": f"{req['target']}|{req['project']}", "product_id": req["target"],
                                                   "project": req["project"], "request_id": req["id"],
                                                   "granted_by": _who(by), "granted_at": _now()})
    status = "approved" if approve else "declined"
    db.run(f"UPDATE {db.t('catalog', 'request')} SET status = ?, decided_by = ?, decided_at = ?, note = ? WHERE id = ?",
           (status, _who(by), _now(), str(note or ""), req["id"]))
    _audit(db, by, f"{status} {req['kind']}", req["target"], {"request": req["id"], "project": req["project"]})
    db.commit()
    return _request_out(_row(db, "request", "id", req["id"]) or {})


def withdraw_request(db: Db, id: str, by: str = "") -> dict[str, Any]:
    req = _row(db, "request", "id", str(id))
    if req is None or req["status"] != "waiting":
        raise PlatformError("Only a waiting request can be withdrawn")
    if req["requested_by"] != _who(by) and not is_admin(db, by):
        raise PlatformError("Only the person who sent a request can withdraw it")
    db.run(f"UPDATE {db.t('catalog', 'request')} SET status = 'withdrawn', decided_by = ?, decided_at = ? WHERE id = ?",
           (_who(by), _now(), req["id"]))
    db.commit()
    return _request_out(_row(db, "request", "id", req["id"]) or {})


def list_grants(db: Db, product_id: str = "") -> list[dict[str, Any]]:
    rows = db.rows(f"SELECT * FROM {db.t('catalog', 'grant')}" + (" WHERE product_id = ?" if product_id else ""),
                   (product_id,) if product_id else ())
    return sorted(rows, key=lambda g: g["id"])


def revoke_grant(db: Db, product_id: str, project: str, by: str = "") -> list[dict[str, Any]]:
    """Take a project's access to a product away. New links are refused; existing link files stay."""
    product = _row(db, "product", "id", product_id) or {}
    if not (is_admin(db, by) or _who(by) in _product_owners(product)):
        raise PlatformError("Only the product's owner or a platform admin can take access away")
    db.run(f"DELETE FROM {db.t('catalog', 'grant')} WHERE id = ?", (f"{product_id}|{project}",))
    _audit(db, by, "revoke access", product_id, {"project": project})
    db.commit()
    return list_grants(db, product_id)


# --------------------------------------------------------------------------- owner coverage
# Every domain, data product and landing zone source has an owner, and a second one to cover for
# them. An owner the platform hasn't seen for a while is named, so someone can hand it over before
# it's needed at 2am. "Seen" is the app using the platform as them, or any change they made.

QUIET_DAYS = 60
_UPN_LIKE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def ensure_people(db: Db) -> list[str]:
    """catalog.person, added after the mesh: additive, safe to run again; note_seen and owner_coverage call it."""
    if db.exists("catalog", "person"):
        return []
    db.run(f"CREATE TABLE {db.t('catalog', 'person')} ({', '.join(_column(db, n, k) for n, k in _CATALOG['person'])})")
    db.commit()
    return ["catalog.person"]


def note_seen(db: Db, by: str = "") -> dict[str, Any]:
    """Record that the caller used the platform today. At most one write a day per person."""
    ensure_people(db)
    who = _who(by)
    if not who:
        return {"user": "", "last_seen": ""}
    found = _row(db, "person", "user_name", who) or {}
    today = _now()[:10]
    if str(found.get("last_seen") or "")[:10] != today:
        _upsert(db, "catalog", "person", "user_name", {"user_name": who, "last_seen": _now()})
        db.commit()
    return {"user": who, "last_seen": today}


def _last_seen(db: Db) -> dict[str, str]:
    seen = ({r["user_name"]: str(r["last_seen"] or "") for r in db.rows(f"SELECT * FROM {db.t('catalog', 'person')}")}
            if db.exists("catalog", "person") else {})  # a read never creates the table
    for r in db.rows(f"SELECT by_user, MAX(at) AS at FROM {db.t('catalog', 'audit')} GROUP BY by_user"):
        who = _who(r["by_user"])
        if who and str(r["at"] or "") > seen.get(who, ""):
            seen[who] = str(r["at"])
    return seen


def owner_coverage(db: Db, presence: bool = True) -> dict[str, Any]:
    """Each domain, published data product and source with an owner problem: none, no second owner, or
    an owner the platform hasn't seen for QUIET_DAYS. Owners written as names, not sign-ins, can't be
    checked for being quiet, and are said to be so. Without ``presence`` (the organization hasn't turned
    on platform_presence) nobody is checked for being quiet. Only reads (the catalog is open to everyone)."""
    seen, now = _last_seen(db) if presence else {}, dt.datetime.now(dt.timezone.utc)

    def quiet(owner: str) -> int | None:
        if not presence or not _UPN_LIKE.match(owner):
            return None
        last = seen.get(owner)
        if not last:
            return -1
        try:
            days = (now - dt.datetime.fromisoformat(last)).days
        except ValueError:
            return -1
        return days if days >= QUIET_DAYS else None

    def check(kind: str, id_: str, name: str, owners_text: Any, opens: str) -> dict[str, Any]:
        owners = sorted(_names(owners_text))
        problems = []
        if not owners:
            problems.append("No owner")
        elif len(owners) == 1:
            problems.append("No second owner to cover")
        for o in owners:
            q = quiet(o)
            if q == -1:
                problems.append(f"{o} hasn't used the platform yet")
            elif q is not None:
                problems.append(f"{o} hasn't used the platform for {q} days")
        unchecked = [o for o in owners if not _UPN_LIKE.match(o)]
        return {"kind": kind, "id": id_, "name": name, "owners": owners, "problems": problems,
                "unchecked": unchecked, "opens": opens}

    rows = [check("domain", d["id"], d["name"] or d["id"], d.get("owners"), "domains")
            for d in db.rows(f"SELECT * FROM {db.t('catalog', 'domain')}")]
    rows += [check("product", p["id"], p["name"] or p["id"], p.get("owner"), "products")
             for p in db.rows(f"SELECT * FROM {db.t('catalog', 'product')}") if p.get("status") != "draft"]
    rows += [check("source", s["name"], s.get("title") or s["name"], s.get("owner"), "landing")
             for s in db.rows(f"SELECT * FROM {db.t('catalog', 'source')}")]
    gaps = [r for r in rows if r["problems"]]
    order = {"No owner": 0}
    gaps.sort(key=lambda r: (min(order.get(p, 1) for p in r["problems"]), r["kind"], r["name"].lower()))
    return {"quiet_days": QUIET_DAYS, "presence": presence, "checked": len(rows), "covered": len(rows) - len(gaps),
            "no_owner": sum("No owner" in r["problems"] for r in gaps), "gaps": gaps}


# --------------------------------------------------------------------------- shortcuts
# Fabric doesn't say who changes a shortcut. The app records its own changes as they happen
# (log_shortcut_change), and every scan compares what it sees with what was seen last
# (record_shortcuts), so changes made in Fabric show up too, as "seen at scan".

SHORTCUT_CHANGES = ("added", "removed", "repointed", "broke", "fixed")
HEALTH = ("healthy", "broken", "unreachable", "not_checked")
RULE_MODES = ("off", "warn", "block")
DEFAULT_SHORTCUT_RULES = {"cross_env": "block", "storage_role": "warn", "deprecated_product": "warn",
                          "connections": "off", "allowed_connections": [], "who_creates": "anyone"}


ACCEPTABLE_RISKS = ("cross_env",)
# Columns catalog.shortcut gained after it was made: the last names its OneLake target was seen
# with, so a target lakehouse that's gone can still be named, and found again by name.
_SHORTCUT_ADDED = ("target_workspace_name", "target_lakehouse_name")


def ensure_shortcut_risks(db: Db) -> list[str]:
    """Give a platform made before accepted risks catalog.shortcut_risk and catalog.shortcut's
    target name columns. Additive only, and safe to run again; the shortcut operations that use
    them call it, so a platform in Fabric gets them on first use."""
    added = []
    if not db.exists("catalog", "shortcut_risk"):
        cols = _CATALOG["shortcut_risk"]
        db.run(f"CREATE TABLE {db.t('catalog', 'shortcut_risk')} ({', '.join(_column(db, n, k) for n, k in cols)})")
        added.append("catalog.shortcut_risk")
    have = _columns(db, "shortcut")
    for col in _SHORTCUT_ADDED:
        if col not in have:
            db.run(f"ALTER TABLE {db.t('catalog', 'shortcut')} ADD {'' if db.tsql else 'COLUMN '}{_column(db, col, 'text')}")
            added.append(f"catalog.shortcut.{col}")
    if added:
        db.commit()
    return added


def shortcut_id(lakehouse_id: str, full_path: str) -> str:
    return f"{lakehouse_id}|{str(full_path).strip('/')}"


def _canon(target: Any) -> str:
    return json.dumps(target or {}, sort_keys=True, default=str)


def _shortcut_out(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row, target=json.loads(row.get("target") or "{}"),
                health_detail=row.get("health_detail") or "")


def _change(db: Db, kind: str, sid: str, ref: dict[str, Any], before: Any, after: Any, by: str, how: str,
            note: str = "", at: str = "") -> dict[str, Any]:
    # Microseconds, so changes made in the same second still list in the order they happened.
    at = at or dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
    row = {"id": uuid.uuid4().hex[:12], "at": at, "kind": kind, "shortcut_id": sid,
           "workspace_name": str(ref.get("workspace_name") or ""), "lakehouse_name": str(ref.get("lakehouse_name") or ""),
           "name": str(ref.get("name") or ""), "before_def": json.dumps(before) if before is not None else None,
           "after_def": json.dumps(after) if after is not None else None, "by_user": _who(by), "how": how,
           "note": str(note or "")}
    _upsert(db, "catalog", "shortcut_change", "id", row)
    return _change_out(row)


def _change_out(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["before"] = json.loads(row["before_def"]) if row.get("before_def") else None
    out["after"] = json.loads(row["after_def"]) if row.get("after_def") else None
    out.pop("before_def", None)
    out.pop("after_def", None)
    return out


def _definition(s: dict[str, Any]) -> dict[str, Any]:
    """What it takes to make a shortcut again: where it is and the REST API's target."""
    return {"workspace_id": s.get("workspace_id"), "lakehouse_id": s.get("lakehouse_id"),
            "path": str(s.get("path") or "").strip("/"), "name": s.get("name"), "target": s.get("target") or {}}


def record_shortcuts(db: Db, lakehouses: list[dict[str, Any]], shortcuts: list[dict[str, Any]]) -> dict[str, Any]:
    """A scan of some lakehouses: ``lakehouses`` [{lakehouse_id, workspace_id, workspace_name, lakehouse_name}]
    were all read, ``shortcuts`` [{lakehouse_id, workspace_id, workspace_name, lakehouse_name, path, name,
    target}] is everything in them. Records what's new, gone or pointing somewhere else since the last
    scan, and returns those changes. A lakehouse scanned for the first time only sets the baseline."""
    ensure_shortcut_risks(db)
    now = _now()
    scanned = {str(l["lakehouse_id"]): l for l in lakehouses if l.get("lakehouse_id")}
    seen = {}
    for s in shortcuts:
        if str(s.get("lakehouse_id")) in scanned:
            seen[shortcut_id(s["lakehouse_id"], f"{str(s.get('path') or '').strip('/')}/{s.get('name')}")] = s
    stored = {}
    for lh in scanned:
        for r in db.rows(f"SELECT * FROM {db.t('catalog', 'shortcut')} WHERE lakehouse_id = ?", (lh,)):
            stored[r["id"]] = r
    first = {lh for lh in scanned if _row(db, "shortcut_scope", "lakehouse_id", lh) is None}
    changes = []
    for sid, s in seen.items():
        before = stored.get(sid)
        row = {"id": sid, "workspace_id": s.get("workspace_id"), "workspace_name": s.get("workspace_name"),
               "lakehouse_id": s.get("lakehouse_id"), "lakehouse_name": s.get("lakehouse_name"),
               "path": str(s.get("path") or "").strip("/"), "name": s.get("name"), "target": _canon(s.get("target")),
               "last_seen": now}
        for col in _SHORTCUT_ADDED:  # a target seen by name keeps it; one that's gone keeps its last
            if s.get(col):
                row[col] = str(s[col])
        if before is None:
            row |= {"health": "not_checked", "health_detail": "", "checked_at": "", "first_seen": now}
            if str(s["lakehouse_id"]) not in first:
                changes.append(_change(db, "added", sid, s, None, _definition(s), "", "scan"))
        elif _canon(json.loads(before.get("target") or "{}")) != row["target"]:
            changes.append(_change(db, "repointed", sid, s, _definition(_shortcut_out(before)), _definition(s), "", "scan"))
            row |= {"health": "not_checked", "health_detail": "", "checked_at": ""}
            row |= {col: str(s.get(col) or "") for col in _SHORTCUT_ADDED}
            _end_risks(db, sid, "", "it was repointed (seen at scan)")
        _upsert(db, "catalog", "shortcut", "id", row)
    for sid, before in stored.items():
        if sid not in seen:
            changes.append(_change(db, "removed", sid, before, _definition(_shortcut_out(before)), None, "", "scan"))
            db.run(f"DELETE FROM {db.t('catalog', 'shortcut')} WHERE id = ?", (sid,))
            _end_risks(db, sid, "", "it was removed (seen at scan)")
    for lh in scanned:
        _upsert(db, "catalog", "shortcut_scope", "lakehouse_id",
                {"lakehouse_id": lh, "first_scan": (_row(db, "shortcut_scope", "lakehouse_id", lh) or {}).get("first_scan") or now,
                 "last_scan": now})
    db.commit()
    return {"changes": changes, "baseline": sorted(first)}


def log_shortcut_change(db: Db, kind: str, shortcut: dict[str, Any], before: dict[str, Any] | None = None,
                        after: dict[str, Any] | None = None, note: str = "", by: str = "") -> dict[str, Any]:
    """A change made from the app, recorded with who made it, and the inventory updated to match
    so the next scan doesn't count it again."""
    if kind not in ("added", "removed", "repointed"):
        raise PlatformError("kind is added, removed or repointed")
    ensure_shortcut_risks(db)
    sid = shortcut_id(shortcut["lakehouse_id"], f"{str(shortcut.get('path') or '').strip('/')}/{shortcut.get('name')}")
    out = _change(db, kind, sid, shortcut, before, after, by, "app", note)
    if kind == "removed":
        db.run(f"DELETE FROM {db.t('catalog', 'shortcut')} WHERE id = ?", (sid,))
    else:
        now = _now()
        prev = _row(db, "shortcut", "id", sid) or {}
        _upsert(db, "catalog", "shortcut", "id", {
            "id": sid, "workspace_id": shortcut.get("workspace_id"), "workspace_name": shortcut.get("workspace_name"),
            "lakehouse_id": shortcut.get("lakehouse_id"), "lakehouse_name": shortcut.get("lakehouse_name"),
            "path": str(shortcut.get("path") or "").strip("/"), "name": shortcut.get("name"),
            "target": _canon((after or {}).get("target")), "health": "not_checked", "health_detail": "",
            "checked_at": "", "first_seen": prev.get("first_seen") or now, "last_seen": now,
            # a new target: the next scan names it
            "target_workspace_name": "", "target_lakehouse_name": ""})
    if kind in ("removed", "repointed"):
        _end_risks(db, sid, by, f"it was {kind}")
    _audit(db, by, f"shortcut {kind}", sid, {"note": note} if note else None)
    db.commit()
    return out


def record_shortcut_health(db: Db, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Health checks: [{id, health, detail}]. A shortcut that breaks or comes back is a change."""
    now, changes = _now(), []
    for r in results:
        if r.get("health") not in HEALTH:
            raise PlatformError(f"health is one of {', '.join(HEALTH)}")
        row = _row(db, "shortcut", "id", str(r.get("id")))
        if row is None:
            continue
        was = row.get("health") or "not_checked"
        if r["health"] == "broken" and was != "broken":
            changes.append(_change(db, "broke", row["id"], row, None, None, "", "check", r.get("detail", "")))
        elif was == "broken" and r["health"] == "healthy":
            changes.append(_change(db, "fixed", row["id"], row, None, None, "", "check"))
        db.run(f"UPDATE {db.t('catalog', 'shortcut')} SET health = ?, health_detail = ?, checked_at = ? WHERE id = ?",
               (r["health"], str(r.get("detail") or ""), now, row["id"]))
    db.commit()
    return changes


def list_seen_shortcuts(db: Db) -> list[dict[str, Any]]:
    return [_shortcut_out(r) for r in db.rows(f"SELECT * FROM {db.t('catalog', 'shortcut')}")]


def shortcut_changes(db: Db, days: int = 30, limit: int = 500, shortcut: str = "") -> list[dict[str, Any]]:
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=int(days))).replace(microsecond=0).isoformat()
    sql = f"SELECT * FROM {db.t('catalog', 'shortcut_change')} WHERE at >= ?"
    params: list[Any] = [since]
    if shortcut:
        sql += " AND shortcut_id = ?"
        params.append(shortcut)
    rows = sorted(db.rows(sql, params), key=lambda r: r["at"], reverse=True)
    return [_change_out(r) for r in rows[:int(limit)]]


def shortcut_rules(db: Db) -> dict[str, Any]:
    saved = db.rows(f"SELECT value FROM {db.t('config', 'setting')} WHERE name = ?", ("shortcut_rules",))
    rules = dict(DEFAULT_SHORTCUT_RULES)
    if saved and saved[0].get("value"):
        rules.update(json.loads(saved[0]["value"]))
    return rules


def save_shortcut_rules(db: Db, rules: dict[str, Any], by: str = "") -> dict[str, Any]:
    require_admin(db, by, "change the shortcut rules")
    out = dict(DEFAULT_SHORTCUT_RULES)
    for k, v in (rules or {}).items():
        if k not in DEFAULT_SHORTCUT_RULES:
            raise PlatformError(f"{k} isn't a shortcut rule")
        if k == "allowed_connections":
            out[k] = [str(x) for x in (v or [])]
        elif k == "who_creates":
            if v not in ("anyone", "admins"):
                raise PlatformError("who_creates is anyone or admins")
            out[k] = v
        elif v not in RULE_MODES:
            raise PlatformError(f"{k} is one of {', '.join(RULE_MODES)}")
        else:
            out[k] = v
    _upsert(db, "config", "setting", "name", {"name": "shortcut_rules", "value": json.dumps(out)})
    _audit(db, by, "save shortcut rules", "shortcut_rules", out)
    db.commit()
    return out


# ---- accepted risks: a platform admin says a flagged shortcut is fine, and why. Repointing or
# removing the shortcut ends it, so a new target is judged afresh.

def _risk_out(row: dict[str, Any]) -> dict[str, Any]:
    return {"shortcut_id": row["shortcut_id"], "kind": row["kind"], "reason": row.get("reason") or "",
            "by": row.get("accepted_by") or "", "at": row.get("accepted_at") or ""}


def _end_risks(db: Db, sid: str, by: str, why: str) -> None:
    for r in db.rows(f"SELECT * FROM {db.t('catalog', 'shortcut_risk')} WHERE shortcut_id = ?", (sid,)):
        db.run(f"DELETE FROM {db.t('catalog', 'shortcut_risk')} WHERE id = ?", (r["id"],))
        _audit(db, by, "end accepted shortcut risk", sid, {"kind": r["kind"], "why": why})


def list_shortcut_risks(db: Db) -> list[dict[str, Any]]:
    ensure_shortcut_risks(db)
    return [_risk_out(r) for r in db.rows(f"SELECT * FROM {db.t('catalog', 'shortcut_risk')}")]


def accept_shortcut_risk(db: Db, shortcut_id: str, kind: str, reason: str, by: str = "") -> dict[str, Any]:
    """Accept a flagged risk on one shortcut (Dev reading Prod on purpose). Platform admins only."""
    require_admin(db, by, "accept a shortcut's risk")
    ensure_shortcut_risks(db)
    if kind not in ACCEPTABLE_RISKS:
        raise PlatformError(f"Only {', '.join(ACCEPTABLE_RISKS)} can be accepted")
    reason = str(reason or "").strip()
    if not reason:
        raise PlatformError("Say why it's fine, so the next person knows")
    row = _row(db, "shortcut", "id", str(shortcut_id))
    if row is None:
        raise PlatformError("That shortcut isn't in the platform's inventory. Refresh Shortcuts, then try again")
    out = {"id": f"{shortcut_id}#{kind}", "shortcut_id": str(shortcut_id), "kind": kind, "reason": reason,
           "target": row.get("target") or "", "accepted_by": _who(by), "accepted_at": _now()}
    _upsert(db, "catalog", "shortcut_risk", "id", out)
    _audit(db, by, "accept shortcut risk", str(shortcut_id), {"kind": kind, "reason": reason})
    db.commit()
    return _risk_out(out)


def withdraw_shortcut_risk(db: Db, shortcut_id: str, kind: str, by: str = "") -> list[dict[str, Any]]:
    """Take back an accepted risk: the shortcut is flagged again. Platform admins only."""
    require_admin(db, by, "withdraw a shortcut's accepted risk")
    ensure_shortcut_risks(db)
    key = f"{shortcut_id}#{kind}"
    if _row(db, "shortcut_risk", "id", key) is None:
        raise PlatformError("That risk isn't accepted")
    db.run(f"DELETE FROM {db.t('catalog', 'shortcut_risk')} WHERE id = ?", (key,))
    _audit(db, by, "withdraw shortcut risk", str(shortcut_id), {"kind": kind})
    db.commit()
    return list_shortcut_risks(db)


# --------------------------------------------------------------------------- trust badges (Preview)
# Endorsement (Certified, Promoted, Master data) is set in Fabric, and only Fabric administrators can
# read it, through Power BI's admin tenant scan. A platform admin who is also a Fabric administrator
# scans (central/trust_service.py); what the scan read is kept here, so everyone sees the last scan's
# badge. Nothing here sets endorsement in Fabric, and an item no scan has read stays unknown.
# Business terms are typed by a product's owner with a link to the term in Microsoft Purview; nothing
# here reads Purview.

PURVIEW_HOSTS = ("purview.microsoft.com", "web.purview.azure.com")
MAX_TERM = 200
MAX_URL = 2000
_ENDORSEMENT_LABELS = {"certified": "Certified", "promoted": "Promoted", "masterdata": "Master data", "none": "None"}
_TRUST_TABLES = ("endorsement", "product_term")
_SPACES = re.compile(r"\s+")


def ensure_trust_badges(db: Db) -> list[str]:
    """Give a platform made before trust badges catalog.endorsement and catalog.product_term.
    Additive only, and safe to run again; the operations that use them call it, so a platform in
    Fabric gets them on first use."""
    added = []
    for table in _TRUST_TABLES:
        if not db.exists("catalog", table):
            cols = _CATALOG[table]
            db.run(f"CREATE TABLE {db.t('catalog', table)} ({', '.join(_column(db, n, k) for n, k in cols)})")
            added.append(f"catalog.{table}")
    if added:
        db.commit()
    return added


def endorsement_label(value: Any) -> str:
    """Fabric's endorsement value as the app shows it: Certified, Promoted, Master data or None.
    A value this app doesn't know is kept as Fabric wrote it, never mapped to another."""
    text = str(value or "").strip()
    if not text:
        return "None"
    return _ENDORSEMENT_LABELS.get(re.sub(r"[\s_-]", "", text.lower()), text[:MAX_BK])


def product_items(product: dict[str, Any]) -> list[dict[str, Any]]:
    """The Fabric items behind a published product: the lakehouses its ports live in, in the highest
    environment it's published to (Prod, else Test, else Dev). Only items the catalog recorded."""
    ports = [p for p in product.get("ports") or [] if p.get("lakehouse_id")]
    here: list[dict[str, Any]] = []
    env = ""
    for env in ("prod", "test", "dev"):
        here = [p for p in ports if p.get("env") == env]
        if here:
            break
    items: dict[str, dict[str, Any]] = {}
    for p in here:
        iid = str(p["lakehouse_id"]).strip().lower()
        items.setdefault(iid, {"item_id": iid, "workspace_id": str(p.get("workspace_id") or "").strip().lower(),
                               "workspace_name": p.get("workspace_name") or "", "item_type": "Lakehouse",
                               "item_name": p.get("lakehouse_name") or "", "env": env})
    return sorted(items.values(), key=lambda i: (i["item_name"], i["item_id"]))


def purview_url(value: Any) -> str:
    """A link to a term in Microsoft Purview: https on purview.microsoft.com or web.purview.azure.com
    (or a host under them), with no sign-in name or other port in it. Anything else is refused."""
    text = str(value or "").strip()
    problem = PlatformError("Paste the term's address from Purview: an https link on purview.microsoft.com "
                            "or web.purview.azure.com")
    if not text or len(text) > MAX_URL or "\\" in text or any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in text):
        raise problem
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise problem from None
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() != "https" or "@" in parts.netloc or port not in (None, 443):
        raise problem
    if not any(host == h or host.endswith(f".{h}") for h in PURVIEW_HOSTS):
        raise problem
    return text


def _product(db: Db, product_id: str) -> dict[str, Any]:
    product = _row(db, "product", "id", str(product_id or ""))
    if product is None:
        raise PlatformError(f"{product_id} isn't a published data product")
    return product


def _can_edit_terms(db: Db, product: dict[str, Any], by: str) -> bool:
    return is_admin(db, by) or _who(by) in _product_owners(product)


def _term_key(product_id: str, term: Any) -> str:
    return f"{product_id}|{_SPACES.sub(' ', str(term or '')).strip().lower()}"


def list_product_terms(db: Db, product_id: str) -> list[dict[str, Any]]:
    ensure_trust_badges(db)
    rows = db.rows(f"SELECT * FROM {db.t('catalog', 'product_term')} WHERE product_id = ?", (str(product_id),))
    return sorted(({"term": r["term"], "url": r["url"], "added_by": r.get("added_by") or "",
                    "added_at": r.get("added_at") or ""} for r in rows), key=lambda t: t["term"].lower())


def product_trust(db: Db, product_id: str, by: str = "") -> dict[str, Any]:
    """A product's trust badges: each Fabric item behind it with the endorsement the last tenant scan
    read (None when no scan has read it: unknown, never guessed), and its business terms."""
    ensure_trust_badges(db)
    product = _product(db, product_id)
    ports = db.rows(f"SELECT * FROM {db.t('catalog', 'port')} WHERE product_id = ?", (product["id"],))
    items = []
    for item in product_items({"ports": ports}):
        seen = _row(db, "endorsement", "item_id", item["item_id"]) or {}
        items.append(item | {"endorsement": seen.get("endorsement") or None, "certified_by": seen.get("certified_by") or "",
                             "scanned_at": seen.get("scanned_at") or "", "scanned_by": seen.get("scanned_by") or ""})
    return {"product_id": product["id"], "name": product.get("name") or "", "owner": product.get("owner") or "",
            "items": items, "terms": list_product_terms(db, product["id"]), "admin": is_admin(db, by),
            "can_edit_terms": _can_edit_terms(db, product, by)}


def record_endorsements(db: Db, items: list[dict[str, Any]], by: str = "",
                        cleared: list[str] | None = None) -> dict[str, Any]:
    """Keep what Fabric's admin tenant scan read for each product item, so everyone sees it. Platform
    admins only: a badge everyone trusts can't be written by whoever calls. `cleared` are items the
    scan looked for but didn't confirm: their last reading goes, so they show unknown, never an old badge."""
    require_admin(db, by, "save endorsement from Fabric's tenant scan")
    ensure_trust_badges(db)
    at, counts = _now(), {}
    gone = 0
    for cid in cleared or []:
        cid = str(cid or "").strip().lower()
        if cid and len(cid) <= MAX_BK and _row(db, "endorsement", "item_id", cid) is not None:
            db.run(f"DELETE FROM {db.t('catalog', 'endorsement')} WHERE item_id = ?", (cid,))
            gone += 1
    for it in items or []:
        iid = str((it or {}).get("item_id") or "").strip().lower()
        if not iid or len(iid) > MAX_BK:
            raise PlatformError("Each scanned item needs its Fabric item id")
        label = endorsement_label(it.get("endorsement"))
        row = {"item_id": iid, "workspace_id": str(it.get("workspace_id") or "")[:MAX_BK],
               "item_type": str(it.get("item_type") or "")[:MAX_BK], "item_name": str(it.get("item_name") or "")[:MAX_BK],
               "endorsement": label, "certified_by": str(it.get("certified_by") or "").strip()[:MAX_BK],
               "scanned_at": at, "scanned_by": _who(by)}
        _upsert(db, "catalog", "endorsement", "item_id", row)
        counts[label] = counts.get(label, 0) + 1
    # Who certified stays out of the audit detail: Fabric may give a sign-in name.
    _audit(db, by, "record endorsement scan", "endorsement",
           {"items": sum(counts.values()), "endorsement": counts, "cleared": gone})
    db.commit()
    return {"recorded": sum(counts.values()), "cleared": gone, "scanned_at": at}


def add_product_term(db: Db, product_id: str, term: str, url: str, by: str = "") -> list[dict[str, Any]]:
    """Put a business term on a product, with a link to the term in Microsoft Purview. The product's
    owner or a platform admin. The same term again changes its link."""
    ensure_trust_badges(db)
    product = _product(db, product_id)
    if not _can_edit_terms(db, product, by):
        raise PlatformError("Only the product's owner or a platform admin can add business terms")
    name = _SPACES.sub(" ", str(term or "")).strip()
    if not name or len(name) > MAX_TERM:
        raise PlatformError(f"A business term needs a name of up to {MAX_TERM} characters")
    link = purview_url(url)
    row = {"id": _term_key(product["id"], name), "product_id": product["id"], "term": name, "url": link,
           "added_by": _who(by), "added_at": _now()}
    _upsert(db, "catalog", "product_term", "id", row)
    _audit(db, by, "add product term", product["id"], {"term": name, "url": link})
    db.commit()
    return list_product_terms(db, product["id"])


def remove_product_term(db: Db, product_id: str, term: str, by: str = "") -> list[dict[str, Any]]:
    """Take a business term off a product; the term stays in Purview. The product's owner or a platform admin."""
    ensure_trust_badges(db)
    product = _product(db, product_id)
    if not _can_edit_terms(db, product, by):
        raise PlatformError("Only the product's owner or a platform admin can remove business terms")
    key = _term_key(product["id"], term)
    if _row(db, "product_term", "id", key) is None:
        raise PlatformError(f"{term} isn't a business term on {product['id']}")
    db.run(f"DELETE FROM {db.t('catalog', 'product_term')} WHERE id = ?", (key,))
    _audit(db, by, "remove product term", product["id"], {"term": _SPACES.sub(" ", str(term or "")).strip()})
    db.commit()
    return list_product_terms(db, product["id"])


# Everything a caller (the app, a notebook, a report button) may ask the platform to do.
OPERATIONS = {
    "ensure_schema": ensure_schema, "ensure_key_table": ensure_key_table, "issue_keys": issue_keys,
    "import_keys": import_keys, "close_keys": close_keys, "stage_keys": stage_keys, "clear_keys": clear_keys,
    "keys_state": keys_state,
    "key_stats": key_stats, "lookup_key": lookup_key, "export_keys": export_keys,
    "list_domains": list_domains, "save_domain": save_domain, "delete_domain": delete_domain,
    "import_fabric_domains": import_fabric_domains,
    "register_project": register_project, "list_projects": list_projects, "register_product": register_product,
    "list_products": list_products, "resolve_port": resolve_port, "register_link": register_link,
    "save_enrichment": save_enrichment, "enrichment": enrichment, "list_glossary": list_glossary,
    "save_term": save_term, "audit": audit, "log_workload_action": log_workload_action,
    "whoami": whoami, "list_admins": list_admins, "claim_admin": claim_admin, "add_admin": add_admin,
    "remove_admin": remove_admin, "list_key_tables": list_key_tables, "list_sources": list_sources,
    "save_source": save_source, "delete_source": delete_source, "adopt_sources": adopt_sources, "request": request, "list_requests": list_requests,
    "list_landing_zones": list_landing_zones, "save_landing_zone": save_landing_zone,
    "delete_landing_zone": delete_landing_zone, "switch_landing_reads": switch_landing_reads,
    "decide_request": decide_request, "withdraw_request": withdraw_request, "list_grants": list_grants,
    "record_request_progress": record_request_progress,
    "revoke_grant": revoke_grant, "record_shortcuts": record_shortcuts, "log_shortcut_change": log_shortcut_change,
    "record_shortcut_health": record_shortcut_health, "list_seen_shortcuts": list_seen_shortcuts,
    "shortcut_changes": shortcut_changes, "shortcut_rules": shortcut_rules, "save_shortcut_rules": save_shortcut_rules,
    "list_shortcut_risks": list_shortcut_risks, "accept_shortcut_risk": accept_shortcut_risk,
    "withdraw_shortcut_risk": withdraw_shortcut_risk,
    "product_trust": product_trust, "record_endorsements": record_endorsements,
    "add_product_term": add_product_term, "remove_product_term": remove_product_term,
    "note_seen": note_seen, "owner_coverage": owner_coverage,
    "follow_product": follow_product, "unfollow_product": unfollow_product, "list_follows": list_follows,
    "product_followers": product_followers,
}
