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
    catalog.port_version each port's history: one row per environment each time its version or columns change
    catalog.link         which project reads which port, the versions it accepts and the columns it relies on
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
    catalog.product_item a data product's semantic models and reports: the Fabric item's id, type, workspace id
                         and display name, picked by the product's owner; nothing in Fabric changes (Preview)
    catalog.call         each change the app sent with a call id (call_once) and its answer, kept 7 days, so a
                         change retried after a timeout is made once
    catalog.policy*      governance policies, their versions, assignments and history (policy.py)

Who may change what (the caller is always who Fabric says it is, see udf.py):

    shortcut inventory   anyone records a new or a broken shortcut; a removal, a repoint or a healthy mark
                         comes from a platform admin or a member of a project with a product in that lakehouse
    platform admins      domains, key tables, copying keys in, source systems, landing zones, glossary, admins,
                         shortcut rules, accepted shortcut risks, policy exceptions, endorsement scans, every product's
                         business terms, and every request
    domain owners        glossary terms of their domain, certifying catalog entries of their domain's projects
    product owners       access requests to their product, their product's business terms, and the semantic
                         models and reports grouped under it
    project members      their project's catalog text (descriptions, tags, terms, steward)
    everyone             reading, issuing keys from existing key tables, products, links, requests
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
import uuid
from typing import Any, Callable, cast
from urllib.parse import urlsplit

SCHEMA_VERSION = 5
UNKNOWN_KEY = -1
UNKNOWN_BK = "UNKNOWN"
MAX_BK = 400
ACCESS = ("public", "domain", "private")
STATUS = ("draft", "published", "deprecated")
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
REQUEST_KINDS = ("key_table", "source", "access", "shortcut", "workspace", "workspace_access", "domain", "exception", "data",
                 "join_domain")
PROJECT_ROLES = ("owner", "member")
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
             ("port", "text"), ("reads", "long"), ("updated_at", "text"), ("updated_by", "text"),
             ("accepts", "text"), ("columns", "long")],
    "port_version": [("id", "key"), ("product_id", "text"), ("port", "text"), ("env", "text"), ("version", "text"),
                     ("columns", "long"), ("published_at", "text"), ("published_by", "text")],
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


    # The last time a product's owner confirmed a project's access still stands (keyed like catalog.grant).
    "grant_review": [("id", "key"), ("product_id", "text"), ("project", "text"), ("reviewed_by", "text"),
                     ("reviewed_at", "text")],
    "follow": [("id", "key"), ("user_name", "text"), ("product_id", "text"), ("followed_at", "text")],
    # Who may change a project's entry in the catalog, publish its products and make its links.
    "project_member": [("id", "key"), ("project", "text"), ("user_name", "text"), ("role", "text"),
                       ("added_by", "text"), ("added_at", "text")],
    # What each environment last published of a product, so a promote's changes are told once per environment.
    "product_env": [("id", "key"), ("product_id", "text"), ("env", "text"), ("version", "text"), ("status", "text"),
                    ("deprecated_on", "text"), ("published_at", "text")],
    # An Entra ID group someone already has, linked to one of a domain's roles in one environment ("" for roles
    # that span them), so the role uses it instead of the group the platform suggests (entra.py).
    "entra_group": [("id", "key"), ("domain_id", "text"), ("role", "text"), ("env", "text"), ("group_id", "text"),
                    ("group_name", "text"), ("linked_by", "text"), ("linked_at", "text")],
    # A semantic model or report a product's owner put under the product, by its Fabric item id (product_id|item_id).
    "product_item": [("id", "key"), ("product_id", "text"), ("item_id", "text"), ("item_type", "text"),
                     ("workspace_id", "text"), ("workspace_name", "text"), ("item_name", "text"),
                     ("added_by", "text"), ("added_at", "text")],
    # A change the app sent with a call id, and its answer as JSON (call_once).
    "call": [("id", "key"), ("op", "text"), ("by_user", "text"), ("at", "text"), ("result", "long")],
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
    created += ensure_grant_reviews(db)
    created += ensure_follows(db)
    created += ensure_entra_groups(db)
    created += ensure_project_members(db)
    created += ensure_product_items(db)
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
    else:
        _audit(db, by, "stage keys" if staging else "unstage keys", "keys")
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
    that isn't there yet is created. Copying keys in is a migration: only a platform admin may."""
    name = key_name(name)
    require_admin(db, by, "copy keys in")
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
        _audit(db, by, "import keys", name, {"imported": len(new)})
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


def _domain_uses(db: Db, did: str) -> list[str]:
    """What else names a domain, so deleting it would leave a reference to nothing, or hand a domain
    made later with the same id its policies (#1310). Tables an older platform lacks are skipped."""
    out = []
    if db.exists("catalog", "landing_zone"):
        out += [f"landing zone {z['name']}" for z in
                db.rows(f"SELECT name FROM {db.t('catalog', 'landing_zone')} WHERE domain_id = ?", (did,))]
    if db.exists("catalog", "policy_assignment"):
        out += [f"policy {a['policy_id']}" for a in db.rows(
            f"SELECT policy_id FROM {db.t('catalog', 'policy_assignment')} WHERE scope = 'domain' AND target = ?", (did,))]
    if db.exists("catalog", "policy_exception"):
        today = _today().isoformat()  # UTC, as policy reads exceptions
        out += [f"an exception to {e['policy_id']}" for e in db.rows(
            f"SELECT policy_id FROM {db.t('catalog', 'policy_exception')} "
            "WHERE scope = 'domain' AND target = ? AND until_date >= ?", (did, today))]
    if db.exists("catalog", "entra_group"):
        out += [f"the {g['role']} group link ({g['env']})" for g in
                db.rows(f"SELECT role, env FROM {db.t('catalog', 'entra_group')} WHERE domain_id = ?", (did,))]
    for r in db.rows(f"SELECT * FROM {db.t('catalog', 'request')} WHERE status = 'waiting'"):
        r = _request_out(r)
        d = r["detail"]
        if did in (d.get("domain_id"), d.get("parent_id"), d.get("domain"), d.get("subdomain")) \
                or (r["kind"] == "exception" and d.get("scope") == "domain" and d.get("scope_target") == did):
            out.append(f"a waiting {r['kind'].replace('_', ' ')} request for {r['target'] or r['project']}")
    return sorted(set(out))


def delete_domain(db: Db, did: str, by: str = "") -> None:
    did = domain_id(did)
    require_admin(db, by, "delete domains")
    children = db.rows(f"SELECT id FROM {db.t('catalog', 'domain')} WHERE parent_id = ?", (did,))
    users = db.rows(f"SELECT name FROM {db.t('catalog', 'project')} WHERE domain_id = ? OR subdomain_id = ?", (did, did))
    what = [f"subdomain {c['id']}" for c in children] + [f"project {u['name']}" for u in users] + _domain_uses(db, did)
    if what:
        raise PlatformError(f"{did} is still used by " + ", ".join(what))
    db.run(f"DELETE FROM {db.t('catalog', 'domain')} WHERE id = ?", (did,))
    _audit(db, by, "delete domain", did)
    db.commit()


def register_project(db: Db, project: dict[str, Any], by: str = "") -> dict[str, Any]:
    """Record a metadata project and its domain. The domain must be one the central team made. Only the
    project's members (or a platform admin) change it; the first to record a project owns it. Joining
    a domain, or moving to another, waits for that domain's owners (or a platform admin): the answer
    has ``domain_request`` and the catalog keeps the domain it had until they approve."""
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
    require_member(db, name, by, "change it")
    before = _row(db, "project", "name", name) or {}
    asked = None
    if dom and (dom, sub) != (before.get("domain_id") or "", before.get("subdomain_id") or "") \
            and not (is_admin(db, by) or _who(by) in _domain_deciders(db, dom, sub)):
        asked = _ask_to_join(db, name, dom, sub, by)
        dom, sub = before.get("domain_id") or "", before.get("subdomain_id") or ""
    row = {"name": name, "domain_id": dom or None, "subdomain_id": sub or None, "team": str(project.get("team") or ""),
           "description": str(project.get("description") or ""), "updated_at": _now(), "updated_by": by}
    _upsert(db, "catalog", "project", "name", row)
    if "landing" in project:
        _save_readers(db, name, project.get("landing") or {})
    # Who recorded a project owns it, so the trail says who and when; readers are on the project.
    _audit(db, by, "change project" if before else "record project", name,
           {"domain": dom, "subdomain": sub, **({"readers": True} if "landing" in project else {})})
    db.commit()
    return dict(row, domain_request=asked) if asked else row


def list_projects(db: Db) -> list[dict[str, Any]]:
    return db.rows(f"SELECT * FROM {db.t('catalog', 'project')}")


def register_product(db: Db, product: dict[str, Any], ports: list[dict[str, Any]], env: str, by: str = "") -> dict[str, Any]:
    """Publish a data product and where its ports live in one environment (done on promote). Each
    port whose version or columns changed in ``env`` gets a row in its history (catalog.port_version);
    ``history`` in the answer lists those ports, so a caller can tell an older function from none changed."""
    project = str(product.get("project") or "").strip()
    name = str(product.get("name") or "").strip()
    if not project or not name:
        raise PlatformError("A product needs its project and name")
    if product.get("access", "private") not in ACCESS:
        raise PlatformError(f"access must be one of {', '.join(ACCESS)}")
    if product.get("status", "draft") not in STATUS:
        raise PlatformError(f"status must be one of {', '.join(STATUS)}")
    ensure_contracts(db)
    pid = f"{project}/{name}"
    require_member(db, project, by, "publish its products")
    _may_publish(db, pid, _row(db, "product", "id", pid), product, by)
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
    changed = []
    for p in ports:
        port = {"id": f"{pid}:{p['port']}:{env}", "product_id": pid, "port": p["port"], "entity": p.get("entity", ""),
                "env": env, "workspace_id": p.get("workspace_id", ""), "workspace_name": p.get("workspace_name", ""),
                "lakehouse_id": p.get("lakehouse_id", ""), "lakehouse_name": p.get("lakehouse_name", ""),
                "schema_name": p.get("schema", "dbo"), "table_name": p.get("table", ""),
                "columns": json.dumps(p.get("columns", {})), "updated_at": _now()}
        _upsert(db, "catalog", "port", "id", port)
        if _record_port_version(db, pid, p["port"], env, row["version"], p.get("columns") or {}, by):
            changed.append(p["port"])
    changes = product_changes(before, row, old_ports, {p["port"]: p.get("columns", {}) for p in ports})
    _upsert(db, "catalog", "product_env", "id", {"id": f"{pid}:{env}", "product_id": pid, "env": env, "version": row["version"],
                                                 "status": row["status"], "deprecated_on": row["deprecated_on"] or "",
                                                 "published_at": _now()})
    _audit(db, by, "publish product", pid, {"env": env, "ports": [p["port"] for p in ports], "history": changed,
                                            **({"changes": changes} if changes else {})})
    db.commit()
    out = dict(row, changes=changes, history=changed)
    if changes:
        out["followers"] = _followers(db, pid)
    return out

def _may_publish(db: Db, pid: str, existing: dict[str, Any] | None, product: dict[str, Any], by: str) -> None:
    """Only a product's owners, whoever last published it, or a platform admin may publish it again.
    A first publish must name the publisher among its owners, unless its owners are a team or group
    name rather than people (no email), which the platform can't check."""
    if is_admin(db, by):
        return
    who = _who(by)
    if existing is not None:
        owners, last = _product_owners(existing), _who(existing.get("updated_by"))
        # UDF_Platform has always recorded the publisher, so a row naming no owner and no publisher
        # can't be told from one anyone could claim: only an admin publishes it again.
        if who and (who in owners or who == last):
            return
        raise PlatformError(f"Only {pid}'s owners or a platform admin can publish it. Its owners are "
                            f"{existing.get('owner') or 'not set'}; ask one of them, or a platform admin.")
    people = {x for x in _names(product.get("owner")) if "@" in x}
    if people and who not in people:
        raise PlatformError(f"{pid} names {product.get('owner')} as its owners, but you publish it: add yourself "
                            "to its owners, or ask one of them or a platform admin to publish it.")


def _port_versions(db: Db, product_id: str, port: str = "", env: str = "") -> list[dict[str, Any]]:
    """A product's port history, newest first. Reads only: none before the table exists."""
    if not db.exists("catalog", "port_version"):
        return []
    rows = db.rows(f"SELECT * FROM {db.t('catalog', 'port_version')} WHERE product_id = ?", (product_id,))
    rows = [dict(r, columns=json.loads(r["columns"] or "{}")) for r in rows
            if (not port or r["port"] == port) and (not env or r["env"] == env)]
    # Times are kept to the microsecond, and ids end in a random part so two publishes at once never collide.
    return sorted(rows, key=lambda r: (r["published_at"] or "", r["id"]), reverse=True)


def _record_port_version(db: Db, product_id: str, port: str, env: str, version: str, columns: dict[str, Any],
                         by: str) -> bool:
    """Add a history row when the port's version or columns differ from its latest one in ``env``."""
    seen = _port_versions(db, product_id, port, env)
    if seen and seen[0]["version"] == version and seen[0]["columns"] == dict(columns):
        return False
    at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
    db.run(f"INSERT INTO {db.t('catalog', 'port_version')} (id, product_id, port, env, version, columns, published_at, "
           "published_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
           (f"{product_id}:{port}:{env}:{at}:{uuid.uuid4().hex[:12]}", product_id, port, env, version,
            json.dumps(dict(columns)), at, by or ""))
    return True


def product_history(db: Db, product_id: str) -> list[dict[str, Any]]:
    """Every version of every port of a product, per environment, newest first. Reads only."""
    return _port_versions(db, str(product_id or ""))


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
    """The products the caller follows. A read: a platform without catalog.follow follows nothing."""
    if not db.exists("catalog", "follow"):
        return []
    return sorted(r["product_id"] for r in db.rows(f"SELECT product_id FROM {db.t('catalog', 'follow')} WHERE user_name = ?",
                                                     (_who(by),)))


def product_followers(db: Db, product_id: str, by: str = "") -> list[str]:
    """Everyone who hears about a product's changes: its followers and whoever linked a project to it.
    Who follows what is personal data, so only the product's owners and platform admins may list it."""
    product = _row(db, "product", "id", product_id) or {}
    if not (is_admin(db, by) or _who(by) in _product_owners(product)):
        raise PlatformError("Only the product's owner or a platform admin can see who follows it")
    return _followers(db, product_id)


def _followers(db: Db, product_id: str) -> list[str]:
    people = ({r["user_name"] for r in db.rows(f"SELECT user_name FROM {db.t('catalog', 'follow')} WHERE product_id = ?",
                                               (product_id,))}
              if db.exists("catalog", "follow") else set())  # a read never creates the table
    people |= {_who(r["updated_by"]) for r in db.rows(f"SELECT updated_by FROM {db.t('catalog', 'link')} WHERE product_id = ?",
                                                      (product_id,)) if r["updated_by"]}
    return sorted(p for p in people if p)


# --------------------------------------------------------------------------- Entra ID groups per domain
# Each domain's roles (owners, creators, readers...) are given to Entra ID security groups, never to people.
# The platform keeps two things: its naming pattern for the groups it suggests, and the groups people already
# have that they linked to a role. The app writes scripts from these (entra.py); nothing here creates a group
# or grants a role.

# Role -> whether it is given per environment.
ENTRA_ROLES = {"domain_admins": False, "domain_contributors": False, "workspace_admins": True, "creators": True,
               "readers": True, "viewers": True, "stewards": False}
ENTRA_ENVS = ("dev", "test", "prod")
DEFAULT_GROUP_NAMING: dict[str, Any] = {
    "pattern": "{PREFIX}-{DOMAIN}-{SUBDOMAIN}-{ROLE}-{ENV}", "prefix": "SG-FAB", "case": "upper", "domain_part": "id",
    "words": {"domain_admins": "DOMAIN-ADMINS", "domain_contributors": "DOMAIN-CONTRIBUTORS",
              "workspace_admins": "WSADMINS", "creators": "CREATORS", "readers": "READERS", "viewers": "VIEWERS",
              "stewards": "STEWARDS"},
    "roles_off": [],
}
_NAMING_SETTING = "entra_group_naming"
_GROUP_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
_PATTERN = re.compile(r"^(?:[A-Za-z0-9_.-]|\{(?:PREFIX|DOMAIN|SUBDOMAIN|ROLE|ENV)\}){1,120}$")


def entra_group_naming(db: Db) -> dict[str, Any]:
    """The platform's naming pattern for the Entra ID groups it suggests: DEFAULT_GROUP_NAMING until an admin saves one.
    What is stored is checked again on every read, so a value written to the table some other way never reaches
    a script; one that fails the checks reads as the default."""
    if db.exists("config", "setting"):
        saved = db.rows(f"SELECT value FROM {db.t('config', 'setting')} WHERE name = ?", (_NAMING_SETTING,))
        if saved and saved[0].get("value"):
            try:
                return checked_entra_naming(json.loads(saved[0]["value"]))
            except (PlatformError, ValueError, TypeError, AttributeError):
                pass
    return json.loads(json.dumps(DEFAULT_GROUP_NAMING))


def checked_entra_naming(naming: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    """``naming`` laid over ``base`` (the default), every part checked: letters, digits, -, _ or . only, so a
    name is safe in a script, on a command line and in Entra's mail nickname. Raises PlatformError."""
    out = json.loads(json.dumps(base or DEFAULT_GROUP_NAMING))
    if not isinstance(naming, dict):
        raise PlatformError("The group naming is a set of named parts")
    for k, v in naming.items():
        if k not in DEFAULT_GROUP_NAMING:
            raise PlatformError(f"{k} isn't part of the group naming")
        if k == "pattern":
            v = str(v or "").strip()
            if not _PATTERN.fullmatch(v) or "{DOMAIN}" not in v or "{ROLE}" not in v:
                raise PlatformError("The pattern needs {DOMAIN} and {ROLE}, and may use {PREFIX}, {SUBDOMAIN} and {ENV} "
                                    "with letters, digits, -, _ or . between them (no spaces)")
        elif k == "prefix":
            v = str(v or "").strip()
            if v and not _GROUP_PART.fullmatch(v):
                raise PlatformError("The prefix is up to 40 letters, digits, -, _ or .")
        elif k == "case":
            if v not in ("upper", "lower", "as_typed"):
                raise PlatformError("case is upper, lower or as_typed")
        elif k == "domain_part":
            if v not in ("id", "name"):
                raise PlatformError("domain_part is id or name")
        elif k == "words":
            words = dict(out["words"])
            for role, word in dict(v or {}).items():
                if role not in ENTRA_ROLES:
                    raise PlatformError(f"{role} isn't a domain role")
                if not _GROUP_PART.fullmatch(str(word or "")):
                    raise PlatformError(f"The word for {role} is up to 40 letters, digits, -, _ or .")
                words[role] = str(word)
            v = words
        elif k == "roles_off":
            v = sorted({str(r) for r in (v or [])})
            if any(r not in ENTRA_ROLES for r in v):
                raise PlatformError(f"roles_off names roles from: {', '.join(ENTRA_ROLES)}")
        out[k] = v
    return out


def save_entra_group_naming(db: Db, naming: dict[str, Any], by: str = "") -> dict[str, Any]:
    """Change the naming pattern. Platform admins only."""
    require_admin(db, by, "change how Entra ID groups are named")
    out = checked_entra_naming(naming or {}, entra_group_naming(db))
    _upsert(db, "config", "setting", "name", {"name": _NAMING_SETTING, "value": json.dumps(out)})
    _audit(db, by, "save Entra group naming", _NAMING_SETTING, out)
    db.commit()
    return out


def ensure_entra_groups(db: Db) -> list[str]:
    """catalog.entra_group, added after the mesh: additive, safe to run again; linking calls it."""
    if db.exists("catalog", "entra_group"):
        return []
    db.run(f"CREATE TABLE {db.t('catalog', 'entra_group')} ({', '.join(_column(db, n, k) for n, k in _CATALOG['entra_group'])})")
    db.commit()
    return ["catalog.entra_group"]


def list_entra_groups(db: Db, domain: str = "") -> list[dict[str, Any]]:
    """The groups people linked to a domain's roles (every domain's without ``domain``). Reads only."""
    if not db.exists("catalog", "entra_group"):
        return []
    if domain:
        rows = db.rows(f"SELECT * FROM {db.t('catalog', 'entra_group')} WHERE domain_id = ?", (domain,))
    else:
        rows = db.rows(f"SELECT * FROM {db.t('catalog', 'entra_group')}")
    return sorted(rows, key=lambda r: (r["domain_id"] or "", r["role"] or "", r["env"] or ""))


def _require_domain_owner(db: Db, did: str, by: str, action: str) -> dict[str, Any]:
    """The domain, when the caller is a platform admin or one of its owners (or its parent's)."""
    domain = _row(db, "domain", "id", did)
    if domain is None:
        raise PlatformError(f"{did} isn't a domain")
    if is_admin(db, by):
        return domain
    owners = _names(domain.get("owners"))
    if domain.get("parent_id"):
        owners |= _names((_row(db, "domain", "id", domain["parent_id"]) or {}).get("owners"))
    if _who(by) and _who(by) in owners:
        return domain
    raise PlatformError(f"Only platform admins or {domain['name']}'s owners can {action}")


def _entra_key(role: str, env: str) -> tuple[str, str]:
    if role not in ENTRA_ROLES:
        raise PlatformError(f"role must be one of {', '.join(ENTRA_ROLES)}")
    env = str(env or "").strip().lower()
    if ENTRA_ROLES[role] and env not in ENTRA_ENVS:
        raise PlatformError(f"{role} is given per environment: env is one of {', '.join(ENTRA_ENVS)}")
    if not ENTRA_ROLES[role]:
        env = ""
    return role, env


def link_entra_group(db: Db, domain: str, role: str, env: str, group_id: str, group_name: str = "",
                     by: str = "") -> list[dict[str, Any]]:
    """Use an existing Entra ID group (its object ID) for one of a domain's roles. Platform admins or the
    domain's owners. Nothing changes in Entra or Fabric. Returns the domain's linked groups."""
    did = domain_id(domain)
    role, env = _entra_key(role, env)
    _require_domain_owner(db, did, by, "link groups to its roles")
    gid = str(group_id or "").strip().lower()
    if not _GUID.fullmatch(gid):
        raise PlatformError("A group is linked by its object ID, like 00000000-0000-0000-0000-000000000000 "
                            "(Microsoft Entra admin center > Groups > the group > Object ID)")
    name = re.sub(r"[\x00-\x1f]", "", str(group_name or "")).strip()[:256] or gid
    ensure_entra_groups(db)
    row = {"id": f"{did}|{role}|{env}", "domain_id": did, "role": role, "env": env, "group_id": gid,
           "group_name": name, "linked_by": by, "linked_at": _now()}
    _upsert(db, "catalog", "entra_group", "id", row)
    _audit(db, by, "link Entra group", row["id"], {"group_id": gid, "group_name": name})
    db.commit()
    return list_entra_groups(db, did)


def unlink_entra_group(db: Db, domain: str, role: str, env: str = "", by: str = "") -> list[dict[str, Any]]:
    """Go back to the suggested group for a role. Platform admins or the domain's owners."""
    did = domain_id(domain)
    role, env = _entra_key(role, env)
    _require_domain_owner(db, did, by, "unlink groups from its roles")
    if db.exists("catalog", "entra_group"):
        db.run(f"DELETE FROM {db.t('catalog', 'entra_group')} WHERE id = ?", (f"{did}|{role}|{env}",))
        _audit(db, by, "unlink Entra group", f"{did}|{role}|{env}")
        db.commit()
    return list_entra_groups(db, did)


def list_products(db: Db) -> list[dict[str, Any]]:
    products = db.rows(f"SELECT * FROM {db.t('catalog', 'product')}")
    ports = db.rows(f"SELECT * FROM {db.t('catalog', 'port')}")
    links = db.rows(f"SELECT * FROM {db.t('catalog', 'link')}")
    grants = db.rows(f"SELECT * FROM {db.t('catalog', 'grant')}")
    for p in products:
        p["granted"] = sorted(g["project"] for g in grants if g["product_id"] == p["id"])
        p["ports"] = [dict(x, columns=json.loads(x["columns"] or "{}")) for x in ports if x["product_id"] == p["id"]]
        # A catalog made before versioned contracts has no accepts or columns on its links: read as none.
        p["consumers"] = [dict(l, reads=json.loads(l["reads"] or "{}"), accepts=l.get("accepts") or "",
                               columns=json.loads(l.get("columns") or "{}"))
                          for l in links if l["product_id"] == p["id"]]
    return sorted(products, key=lambda p: p["id"])


def resolve_port(db: Db, product_id: str, port: str, env: str) -> dict[str, Any] | None:
    found = _row(db, "port", "id", f"{product_id}:{port}:{env}")
    if found:
        found["columns"] = json.loads(found["columns"] or "{}")
    return found


def _today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _date(value: Any) -> dt.date | None:
    """A deprecated_on date ("2026-12-31", or a time on that day); None when it isn't one."""
    try:
        return dt.date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def register_link(db: Db, consumer: str, alias: str, product_id: str, port: str, reads: dict[str, str],
                  by: str = "", accepts: str = "", columns: dict[str, Any] | None = None) -> dict[str, Any]:
    """A consumer project's link to a product port. Refused if the product isn't open to it, or, for a
    new link, if the product was deprecated on a date that has come. ``accepts`` is the product
    versions the consumer can read ("2", "2-3" or "2+"); ``columns`` the ones it relies on."""
    ensure_contracts(db)
    require_member(db, consumer, by, "make its links")
    product = _row(db, "product", "id", product_id)
    if product is None:
        raise PlatformError(f"{product_id} isn't a published data product")
    if product["status"] == "draft":
        raise PlatformError(f"{product_id} is still a draft; its owner publishes it on promote")
    accepts = str(accepts or "").strip()
    if accepts and not _ACCEPTS.match(accepts):
        raise PlatformError(f"accepts is a version (\"2\"), a range (\"2-3\") or a version and later (\"2+\"), "
                            f"not {accepts!r}")
    lid = f"{consumer}:{alias}"
    stops = _date(product.get("deprecated_on"))
    if product["status"] == "deprecated" and stops and stops <= _today() and _row(db, "link", "id", lid) is None:
        owner = f" Ask its owner ({product['owner']}) what replaces it." if product.get("owner") else ""
        raise PlatformError(f"{product_id} was deprecated on {stops.isoformat()} and takes no new links.{owner}")
    granted = _row(db, "grant", "id", f"{product_id}|{consumer}") is not None
    if product["access"] == "private" and product["project"] != consumer and not granted:
        raise PlatformError(f"{product_id} is private to {product['project']}. Ask its owner: Request access")
    if product["access"] == "domain" and not granted:
        mine = _row(db, "project", "name", consumer) or {}
        if mine.get("domain_id") != product.get("domain_id"):
            raise PlatformError(f"{product_id} is open to the {product.get('domain_id')} domain only. "
                                "Ask its owner: Request access")
    columns = {str(k): str(v) for k, v in (columns or {}).items()}
    row = {"id": lid, "consumer_project": consumer, "alias": alias, "product_id": product_id,
           "port": port, "reads": json.dumps(reads), "updated_at": _now(), "updated_by": by,
           "accepts": accepts or None, "columns": json.dumps(columns) if columns else None}
    _upsert(db, "catalog", "link", "id", row)
    _audit(db, by, "link", row["id"], {"product": product_id, "port": port, "reads": reads, "accepts": accepts,
                                       "columns": sorted(columns)})
    db.commit()
    return row


# A link's accepted product versions: "2" (one), "2-3" (a range) or "2+" (that version or later).
_ACCEPTS = re.compile(r"^\d+(?:\s*-\s*\d+|\+)?$")
# Columns catalog.link gained with versioned contracts.
_LINK_ADDED = (("accepts", "text"), ("columns", "long"))


def ensure_contracts(db: Db) -> list[str]:
    """Give a platform made before versioned contracts catalog.port_version and catalog.link's accepts
    and columns. Additive only, and safe to run again; publishing a product and saving a link call it,
    so a platform in Fabric gets them on first use."""
    added = []
    if not db.exists("catalog", "port_version"):
        cols = _CATALOG["port_version"]
        db.run(f"CREATE TABLE {db.t('catalog', 'port_version')} ({', '.join(_column(db, n, k) for n, k in cols)})")
        added.append("catalog.port_version")
    have = _columns(db, "link")
    for col, kind in _LINK_ADDED:
        if col not in have:
            db.run(f"ALTER TABLE {db.t('catalog', 'link')} ADD {'' if db.tsql else 'COLUMN '}{_column(db, col, kind)}")
            added.append(f"catalog.link.{col}")
    if added:
        db.commit()
    return added


SCHEMA_HOOKS.append(ensure_contracts)


def save_enrichment(db: Db, target: str, fields: dict[str, Any], by: str = "") -> dict[str, Any]:
    """Description, tags, classification, glossary terms, steward or certification of a table or
    column. ``target`` is "<project>:<entity>" or "<project>:<entity>#<column>". Only the fields
    given change, for a project the catalog has. Only the project's members (or a platform admin)
    change the text; ``certified`` is changed only by the owners of the project's domain or an admin,
    who need not be members to certify."""
    target = str(target or "").strip()
    if ":" not in target:
        raise PlatformError("target is <project>:<entity> or <project>:<entity>#<column>")
    project = target.split(":", 1)[0].strip()
    admin = is_admin(db, by)
    if not project or (not admin and not _in_catalog(db, project)):
        raise PlatformError(f"{project or 'That project'} isn't in the platform's catalog yet. Join the mesh first "
                            "(Data mesh > Domains > This project), then its catalog text can be saved")
    current = _row(db, "enrichment", "target", target) or {"target": target}
    if not admin and (not fields or any(f != "certified" for f in fields)):
        require_member(db, project, by, "change its catalog text")
    if "certified" in fields and str(fields["certified"] or "") != str(current.get("certified") or ""):
        _require_certifier(db, project, by)
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
    if all(str(row.get(f) or "") == str(current.get(f) or "") for f in fields):
        return _enrichment_out(current)  # nothing changes: nothing is written or recorded
    row["updated_at"], row["updated_by"] = _now(), by
    _upsert(db, "catalog", "enrichment", "target", row)
    _audit(db, by, "enrich", target, {k: fields[k] for k in fields})
    db.commit()
    return _enrichment_out(row)


def _require_certifier(db: Db, project: str, by: str) -> None:
    """Certifying a table or column (or taking it back) is for the owners of the project's domain, or a
    platform admin: a project's own members can't vouch for their own data."""
    if is_admin(db, by):
        return
    row = _row(db, "project", "name", project) or {}
    dom = row.get("domain_id") or ""
    if dom and _who(by) in _domain_deciders(db, dom, row.get("subdomain_id") or ""):
        return
    raise PlatformError(f"Only the owners of {project}'s domain or a platform admin can change what's certified"
                        + ("" if dom else f": {project} isn't in a domain yet, so a platform admin decides"))


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


def is_sign_in_name(text: str) -> bool:
    """Whether ``text`` can be a caller's sign-in name (UPN), the only thing an admin is matched by."""
    return bool(_EMAIL.match(str(text or "")))


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
    raise PlatformError(f"Only platform admins can {action}. Send a request in Data mesh > Requests, "
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
    # Admins are matched by the sign-in name Fabric gives the function, so anything else never matches.
    if not is_sign_in_name(who):
        raise PlatformError("Give the new admin's sign-in name, like ana@contoso.com. Groups can't be platform admins.")
    _upsert(db, "catalog", "admin", "user_name", {"user_name": who, "added_at": _now(), "added_by": _who(by)})
    _audit(db, by, "add admin", who)
    db.commit()
    return list_admins(db)


def remove_admin(db: Db, user: str, by: str = "") -> list[dict[str, Any]]:
    require_admin(db, by, "remove platform admins")
    who = _who(user)
    admins = list_admins(db)
    if who not in [a["user_name"] for a in admins]:
        raise PlatformError(f"{who or 'Nobody'} isn't a platform admin")
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
            raise PlatformError(f"domain {domain} doesn't exist: add it first in Data mesh > Domains")
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


# --------------------------------------------------------------------------- project members
# A project's owners and members change its catalog entry, publish its products and make its links.
# Owners also add and remove members. A project kept from before members starts with the people who
# already worked on it there: who recorded it, published its products, owns them or made its links.

def ensure_project_members(db: Db) -> list[str]:
    """catalog.project_member, added after the mesh: additive, safe to run again."""
    if db.exists("catalog", "project_member"):
        return []
    cols = _CATALOG["project_member"]
    db.run(f"CREATE TABLE {db.t('catalog', 'project_member')} ({', '.join(_column(db, n, k) for n, k in cols)})")
    db.commit()
    return ["catalog.project_member"]


def _stored_members(db: Db, project: str) -> dict[str, str]:
    if not db.exists("catalog", "project_member"):
        return {}
    rows = db.rows(f"SELECT user_name, role FROM {db.t('catalog', 'project_member')} WHERE project = ?", (project,))
    return {r["user_name"]: r["role"] for r in rows}


def _earlier_people(db: Db, project: str) -> list[str]:
    """Who already worked on a project in the catalog before it had members: people, never team names."""
    people = [_who((_row(db, "project", "name", project) or {}).get("updated_by"))]
    for p in db.rows(f"SELECT owner, updated_by FROM {db.t('catalog', 'product')} WHERE project = ?", (project,)):
        people += [_who(p.get("updated_by")), *_product_owners(p)]
    if db.exists("catalog", "link"):
        people += [_who(r.get("updated_by")) for r in
                   db.rows(f"SELECT updated_by FROM {db.t('catalog', 'link')} WHERE consumer_project = ?", (project,))]
    return sorted({x for x in people if x and _EMAIL.match(x)})


def _put_member(db: Db, project: str, user: str, role: str, by: str) -> None:
    _upsert(db, "catalog", "project_member", "id", {"id": f"{project}|{user}", "project": project, "user_name": user,
                                                    "role": role, "added_by": _who(by), "added_at": _now()})


def require_member(db: Db, project: str, by: str, action: str) -> None:
    """Refuse unless the caller is one of the project's members or a platform admin. A project with no
    members yet gets the people who already worked on it as owners. Only a name the catalog has never
    seen (no project, products or links) makes the caller its first owner; any other waits for an admin."""
    who = _who(by)
    ensure_project_members(db)
    members = _stored_members(db, project)
    if not members:
        first = _earlier_people(db, project)
        if not first and not _in_catalog(db, project) and _EMAIL.match(who):
            first = [who]
        for person in first:
            _put_member(db, project, person, "owner", by or person)
        members = _stored_members(db, project)
    if is_admin(db, by) or (who and who in members):
        return
    owners = sorted(u for u, r in members.items() if r == "owner")
    if not owners:
        raise PlatformError(f"{project} has no owner yet, so only a platform admin can {action}. A platform admin "
                            "names its first owner on Data mesh > Domains > This project.")
    raise PlatformError(f"Only {project}'s members or a platform admin can {action}. Ask one of its owners "
                        f"({', '.join(owners)}) to add you on Data mesh > Domains > This project.")


def _in_catalog(db: Db, project: str) -> bool:
    """Whether the catalog already knows a project by this name: its row, a product of it, or a link it made
    (a link records no project row, so a name can be only there)."""
    if _row(db, "project", "name", project) is not None:
        return True
    if db.rows(f"SELECT id FROM {db.t('catalog', 'product')} WHERE project = ?", (project,)):
        return True
    return db.exists("catalog", "link") and bool(
        db.rows(f"SELECT id FROM {db.t('catalog', 'link')} WHERE consumer_project = ?", (project,)))


def list_project_members(db: Db, project: str, by: str = "") -> dict[str, Any]:
    """A project's members, and whether the caller may change them. Reads only: a project kept from
    before members lists who would start as its owners, marked ``earlier``."""
    members = _stored_members(db, project)
    earlier = not members
    if earlier:
        members = {p: "owner" for p in _earlier_people(db, project)}
    who = _who(by)
    rows = sorted(({"user": u, "role": r} for u, r in members.items()), key=lambda m: (m["role"] != "owner", m["user"]))
    return {"project": project, "members": rows, "earlier": earlier,
            "can_change": is_admin(db, by) or members.get(who) == "owner"
            or (not members and not _in_catalog(db, project) and bool(_EMAIL.match(who))),
            "domain_request": _waiting_join(db, project, who)}


def _waiting_join(db: Db, project: str, who: str) -> dict[str, Any] | None:
    """The project's waiting ask to join a domain, if any. None when the catalog has no requests yet."""
    if not db.exists("catalog", "request"):
        return None
    rows = db.rows(f"SELECT * FROM {db.t('catalog', 'request')} WHERE kind = 'join_domain' AND status = 'waiting' "
                   "AND target = ?", (project,))
    if not rows:
        return None
    r = _request_out(rows[0])
    return r | {"mine": bool(who) and r["requested_by"] == who}


def set_project_member(db: Db, project: str, user: str, role: str = "", by: str = "") -> dict[str, Any]:
    """Add a member, make one an owner or a member, or take one off (``role`` ""). Only the project's
    owners or a platform admin; a project always keeps an owner."""
    project = str(project or "").strip()
    user = _who(user)
    if not project:
        raise PlatformError("Say which project")
    if not _EMAIL.match(user):
        raise PlatformError(f"{user or 'That'} isn't an email address: members are people, by their sign-in")
    if role and role not in PROJECT_ROLES:
        raise PlatformError(f"role must be one of {', '.join(PROJECT_ROLES)}")
    require_member(db, project, by, "change its members")
    members = _stored_members(db, project)
    if not is_admin(db, by) and members.get(_who(by)) != "owner":
        raise PlatformError(f"Only {project}'s owners or a platform admin can change its members")
    after = dict(members)
    if role:
        after[user] = role
    else:
        after.pop(user, None)
    if "owner" in members.values() and "owner" not in after.values():
        raise PlatformError(f"{project} needs an owner: make someone else an owner first")
    if role:
        _put_member(db, project, user, role, by)
    else:
        db.run(f"DELETE FROM {db.t('catalog', 'project_member')} WHERE id = ?", (f"{project}|{user}",))
    _audit(db, by, "project member", project, {"user": user, "role": role or "removed", "was": members.get(user) or ""})
    db.commit()
    return list_project_members(db, project, by)


def _domain_deciders(db: Db, dom: str, sub: str = "") -> set[str]:
    """Who approves a project joining a domain: its owners, and the subdomain's, if one is picked."""
    people = _names((_row(db, "domain", "id", dom) or {}).get("owners"))
    if sub:
        people |= _names((_row(db, "domain", "id", sub) or {}).get("owners"))
    return people


def _ask_to_join(db: Db, project: str, dom: str, sub: str, by: str) -> dict[str, Any]:
    """A join_domain request for the project, unless the same one is already waiting. One that asked for
    another domain is withdrawn: the project's latest choice is the one its owners decide."""
    for r in db.rows(f"SELECT * FROM {db.t('catalog', 'request')} WHERE kind = 'join_domain' AND status = 'waiting' "
                     "AND target = ?", (project,)):
        d = _request_out(r)["detail"]
        if (d.get("domain"), d.get("subdomain") or "") == (dom, sub):
            return _request_out(r)
        db.run(f"UPDATE {db.t('catalog', 'request')} SET status = 'withdrawn', decided_by = ?, decided_at = ? WHERE id = ?",
               (_who(by), _now(), r["id"]))
    return request(db, "join_domain", project, project=project, detail={"domain": dom, "subdomain": sub}, by=by)


def _check_join_domain(db: Db, target: str, detail: dict[str, Any], reason: str, by: str) -> tuple[str, dict[str, Any]]:
    dom, sub = str(detail.get("domain") or "").strip(), str(detail.get("subdomain") or "").strip()
    domain = _row(db, "domain", "id", dom)
    if domain is None:
        raise PlatformError(f"domain {dom} isn't one of your organization's domains")
    if sub and (_row(db, "domain", "id", sub) or {}).get("parent_id") != dom:
        raise PlatformError(f"{sub} isn't a subdomain of {dom}")
    require_member(db, target, by, "ask to join a domain")
    return target, {"domain": dom, "subdomain": sub, "domain_name": domain.get("name") or dom,
                    "owners": ", ".join(sorted(_domain_deciders(db, dom, sub)))}


def _approve_join_domain(db: Db, req: dict[str, Any], by: str) -> None:
    d = req["detail"]
    if _row(db, "domain", "id", d["domain"]) is None:
        raise PlatformError(f"domain {d['domain']} isn't there any more: decline this request")
    row = _row(db, "project", "name", req["target"])
    if row is None:
        raise PlatformError(f"{req['target']} isn't in the catalog any more: decline this request")
    db.run(f"UPDATE {db.t('catalog', 'project')} SET domain_id = ?, subdomain_id = ?, updated_at = ? WHERE name = ?",
           (d["domain"], d.get("subdomain") or None, _now(), req["target"]))


REQUEST_HOOKS["join_domain"] = {"check": _check_join_domain, "approve": _approve_join_domain}


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
    if req["kind"] == "join_domain":
        return _domain_deciders(db, req["detail"].get("domain") or "", req["detail"].get("subdomain") or "")
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
        if req["kind"] == "join_domain":
            who = "the domain's owners or a platform admin"
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
    what = {"request": req["id"], "project": req["project"]}
    if not approve and _left(req):
        what["left"] = _left(req)
    db.run(f"UPDATE {db.t('catalog', 'request')} SET status = ?, decided_by = ?, decided_at = ?, note = ? WHERE id = ?",
           (status, _who(by), _now(), str(note or ""), req["id"]))
    _audit(db, by, f"{status} {req['kind']}", req["target"], what)
    db.commit()
    return _request_out(_row(db, "request", "id", req["id"]) or {})


def _left(req: dict[str, Any]) -> dict[str, Any]:
    """The ids of what approving a request made before it was declined or withdrawn, for the audit
    trail: closing it deletes nothing in Fabric."""
    made = (req.get("detail") or {}).get("made") or {}
    if made.get("change") == "kept":  # a higher role they already had: approving changed nothing
        return {}
    return {k: made[k] for k in _LEFT.get(req.get("kind") or "", ()) if made.get(k)}


# What each kind's approval makes, by the keys record_request_progress keeps: a workspace request's
# capacity_id or a source's landing workspace_id are where things went, not what was made.
_LEFT = {"workspace": ("workspace_id",), "workspace_access": ("assigned", "change", "from"), "source": ("copy_job_id", "schedule_id"),
         "domain": ("domain_id", "fabric_domain_id"), "shortcut": ("shortcut_id", "found")}


def withdraw_request(db: Db, id: str, by: str = "") -> dict[str, Any]:
    req = _row(db, "request", "id", str(id))
    if req is None or req["status"] != "waiting":
        raise PlatformError("Only a waiting request can be withdrawn")
    if req["requested_by"] != _who(by) and not is_admin(db, by):
        raise PlatformError("Only the person who sent a request can withdraw it")
    req = _request_out(req)
    db.run(f"UPDATE {db.t('catalog', 'request')} SET status = 'withdrawn', decided_by = ?, decided_at = ? WHERE id = ?",
           (_who(by), _now(), req["id"]))
    what = {"request": req["id"], "project": req["project"]}
    if _left(req):
        what["left"] = _left(req)
    _audit(db, by, f"withdrew {req['kind']}", req["target"], what)
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
    # After the role check, so only someone who may take access away learns whether a grant exists.
    grant = _row(db, "grant", "id", f"{product_id}|{project}")
    if grant is None:
        raise PlatformError(f"{project} has no access to {product_id} to take away")
    db.run(f"DELETE FROM {db.t('catalog', 'grant')} WHERE id = ?", (f"{product_id}|{project}",))
    if db.exists("catalog", "grant_review"):
        db.run(f"DELETE FROM {db.t('catalog', 'grant_review')} WHERE id = ?", (f"{product_id}|{project}",))
    # The grant row goes, so its audit row keeps what gave the access (#1352): the request, who and when.
    given = {"request": grant.get("request_id"), "granted_by": grant.get("granted_by"), "granted_at": grant.get("granted_at")}
    _audit(db, by, "revoke access", product_id, {"project": project, **{k: v for k, v in given.items() if v}})
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


# --------------------------------------------------------------------------- access reviews
# Every quarter a product's owner confirms that each project given access still needs it, or takes
# it away. A grant nobody confirmed is flagged, never taken away by itself.

REVIEW_DAYS = 90


def ensure_grant_reviews(db: Db) -> list[str]:
    """catalog.grant_review, added after grants: additive, safe to run again; the review operations call it."""
    if db.exists("catalog", "grant_review"):
        return []
    db.run(f"CREATE TABLE {db.t('catalog', 'grant_review')} ({', '.join(_column(db, n, k) for n, k in _CATALOG['grant_review'])})")
    db.commit()
    return ["catalog.grant_review"]


def access_reviews(db: Db, by: str = "") -> dict[str, Any]:
    """The grants the caller reviews (their own products', or every product's for a platform admin),
    oldest confirmation first, each with when it was last confirmed and whether it's due. A read: a
    platform without catalog.grant_review has no confirmations yet."""
    admin, who = is_admin(db, by), _who(by)
    products = {p["id"]: p for p in db.rows(f"SELECT id, name, owner FROM {db.t('catalog', 'product')}")}
    reviews = ({r["id"]: r for r in db.rows(f"SELECT * FROM {db.t('catalog', 'grant_review')}")}
               if db.exists("catalog", "grant_review") else {})
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for g in db.rows(f"SELECT * FROM {db.t('catalog', 'grant')}"):
        product = products.get(g["product_id"]) or {}
        if not (admin or (who and who in _product_owners(product))):
            continue
        review = reviews.get(g["id"]) or {}
        since = review.get("reviewed_at") or g.get("granted_at") or ""
        try:
            age = (now - dt.datetime.fromisoformat(since)).days
        except ValueError:
            age = REVIEW_DAYS
        out.append({"product_id": g["product_id"], "product_name": product.get("name") or g["product_id"],
                    "project": g["project"], "granted_by": g.get("granted_by") or "", "granted_at": g.get("granted_at") or "",
                    "reviewed_by": review.get("reviewed_by") or "", "reviewed_at": review.get("reviewed_at") or "",
                    "days": age, "due": age >= REVIEW_DAYS})
    out.sort(key=lambda r: (-r["days"], r["product_id"], r["project"]))
    return {"every_days": REVIEW_DAYS, "grants": out, "due": sum(r["due"] for r in out)}


def confirm_access(db: Db, product_id: str, projects: list[str], by: str = "") -> dict[str, Any]:
    """The product's owner (or a platform admin) confirms these projects still need access."""
    product = _row(db, "product", "id", product_id) or {}
    if not (is_admin(db, by) or _who(by) in _product_owners(product)):
        raise PlatformError("Only the product's owner or a platform admin can confirm access")
    granted = {g["project"] for g in list_grants(db, product_id)}
    missing = sorted(set(projects or []) - granted)
    if missing:
        raise PlatformError(f"{', '.join(missing)} {'has' if len(missing) == 1 else 'have'} no access to {product_id} to confirm")
    if not projects:
        return access_reviews(db, by)  # nothing confirmed: nothing to record
    ensure_grant_reviews(db)
    for project in sorted(set(projects)):
        _upsert(db, "catalog", "grant_review", "id", {"id": f"{product_id}|{project}", "product_id": product_id,
                                                      "project": project, "reviewed_by": _who(by), "reviewed_at": _now()})
    _audit(db, by, "confirm access", product_id, {"projects": sorted(set(projects))})
    db.commit()
    return access_reviews(db, by)


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


def _shortcut_changer(db: Db, by: str) -> Callable[[Any], bool]:
    """Who may change what the inventory says about a lakehouse's shortcuts in a way that could hide a
    problem (a removal, a repoint, a healthy mark): a platform admin, or a member of a project with a
    product port in that lakehouse. Anyone may report a new shortcut or a broken one."""
    if is_admin(db, by):
        return lambda lakehouse_id: True
    who, seen = _who(by), {}

    def may(lakehouse_id: Any) -> bool:
        lakehouse_id = str(lakehouse_id or "")
        if lakehouse_id not in seen:
            projects = {r["project"] for r in db.rows(
                f"SELECT DISTINCT p.project AS project FROM {db.t('catalog', 'port')} o JOIN {db.t('catalog', 'product')} p "
                "ON p.id = o.product_id WHERE o.lakehouse_id = ?", (lakehouse_id,))} if lakehouse_id else set()
            seen[lakehouse_id] = bool(who) and any(who in _stored_members(db, pr) for pr in projects)
        return seen[lakehouse_id]
    return may


def _skipped(sid: str, kind: str) -> dict[str, Any]:
    return {"kind": "skipped", "shortcut_id": sid, "change": kind,
            "why": "only a platform admin, or a member of a project with a product in this lakehouse, can record it"}


def record_shortcuts(db: Db, lakehouses: list[dict[str, Any]], shortcuts: list[dict[str, Any]],
                     by: str = "") -> dict[str, Any]:
    """A scan of some lakehouses: ``lakehouses`` [{lakehouse_id, workspace_id, workspace_name, lakehouse_name}]
    were all read, ``shortcuts`` [{lakehouse_id, workspace_id, workspace_name, lakehouse_name, path, name,
    target}] is everything in them. Records what's new, gone or pointing somewhere else since the last
    scan, and returns those changes. A lakehouse scanned for the first time only sets the baseline.

    A removal or a repoint changes the inventory only from someone who may change that lakehouse's
    (_shortcut_changer); from anyone else a removal is skipped and a repoint is recorded as reported,
    leaving the shortcut, its health and its accepted risks as they were. Both are listed in ``skipped``."""
    ensure_shortcut_risks(db)
    now, may, skipped = _now(), _shortcut_changer(db, by), []
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
                changes.append(_change(db, "added", sid, s, None, _definition(s), by, "scan"))
        elif _canon(json.loads(before.get("target") or "{}")) != row["target"]:
            if not may(s["lakehouse_id"]):
                changes.append(_change(db, "repointed", sid, s, _definition(_shortcut_out(before)), _definition(s), by,
                                       "scan", "reported: not applied to the inventory"))
                skipped.append(_skipped(sid, "repointed"))
                continue
            changes.append(_change(db, "repointed", sid, s, _definition(_shortcut_out(before)), _definition(s), by, "scan"))
            row |= {"health": "not_checked", "health_detail": "", "checked_at": ""}
            row |= {col: str(s.get(col) or "") for col in _SHORTCUT_ADDED}
            _end_risks(db, sid, by, "it was repointed (seen at scan)")
        _upsert(db, "catalog", "shortcut", "id", row)
    for sid, before in stored.items():
        if sid not in seen:
            if not may(before.get("lakehouse_id")):
                skipped.append(_skipped(sid, "removed"))
                continue
            changes.append(_change(db, "removed", sid, before, _definition(_shortcut_out(before)), None, by, "scan"))
            db.run(f"DELETE FROM {db.t('catalog', 'shortcut')} WHERE id = ?", (sid,))
            _end_risks(db, sid, by, "it was removed (seen at scan)")
    for lh in scanned:
        _upsert(db, "catalog", "shortcut_scope", "lakehouse_id",
                {"lakehouse_id": lh, "first_scan": (_row(db, "shortcut_scope", "lakehouse_id", lh) or {}).get("first_scan") or now,
                 "last_scan": now})
    _audit(db, by, "shortcut scan", ",".join(sorted(scanned)),
           {"changes": len(changes), "skipped": len(skipped)} if changes or skipped else None)
    db.commit()
    return {"changes": changes, "baseline": sorted(first), "skipped": skipped}


def log_shortcut_change(db: Db, kind: str, shortcut: dict[str, Any], before: dict[str, Any] | None = None,
                        after: dict[str, Any] | None = None, note: str = "", by: str = "") -> dict[str, Any]:
    """A change made from the app, recorded with who made it, and the inventory updated to match
    so the next scan doesn't count it again. A removal or a repoint changes the inventory only from
    someone who may change that lakehouse's shortcuts (_shortcut_changer): anyone else's removal is
    refused (the next scan by someone who may records it) and their repoint is recorded as reported."""
    if kind not in ("added", "removed", "repointed"):
        raise PlatformError("kind is added, removed or repointed")
    ensure_shortcut_risks(db)
    sid = shortcut_id(shortcut["lakehouse_id"], f"{str(shortcut.get('path') or '').strip('/')}/{shortcut.get('name')}")
    if kind != "added" and not _shortcut_changer(db, by)(shortcut.get("lakehouse_id")):
        if kind == "removed":
            raise PlatformError("The shortcut is removed, but only a platform admin, or a member of a project with a "
                                "product in this lakehouse, can record that; their next scan does")
        # What it pointed at comes from the inventory, not the caller; a reported change can't be undone.
        stored = _row(db, "shortcut", "id", sid)
        before = _definition(_shortcut_out(stored)) if stored else None
        out = _change(db, kind, sid, shortcut, before, after, by, "app",
                      ("reported: not applied to the inventory" + (f"; {note}" if note else "")))
        _audit(db, by, "shortcut repointed (reported)", sid, {"note": note} if note else None)
        db.commit()
        return out
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


def record_shortcut_health(db: Db, results: list[dict[str, Any]], by: str = "") -> list[dict[str, Any]]:
    """Health checks: [{id, health, detail}]. A shortcut that breaks or comes back is a change. Anyone
    may report a shortcut broken; any other result (even the same health again, which would freshen
    the check's time and detail) is recorded only from someone who may change that lakehouse's
    shortcuts (_shortcut_changer), and is otherwise listed as skipped."""
    now, changes, may, done = _now(), [], _shortcut_changer(db, by), 0
    for r in results:
        if r.get("health") not in HEALTH:
            raise PlatformError(f"health is one of {', '.join(HEALTH)}")
        row = _row(db, "shortcut", "id", str(r.get("id")))
        if row is None:
            continue
        was = row.get("health") or "not_checked"
        if r["health"] != "broken" and not may(row.get("lakehouse_id")):
            changes.append(_skipped(row["id"], r["health"]))
            continue
        if r["health"] == "broken" and was != "broken":
            changes.append(_change(db, "broke", row["id"], row, None, None, by, "check", r.get("detail", "")))
        elif was == "broken" and r["health"] == "healthy":
            changes.append(_change(db, "fixed", row["id"], row, None, None, by, "check"))
        db.run(f"UPDATE {db.t('catalog', 'shortcut')} SET health = ?, health_detail = ?, checked_at = ? WHERE id = ?",
               (r["health"], str(r.get("detail") or ""), now, row["id"]))
        done += 1
    if changes or done:
        _audit(db, by, "shortcut health", f"{done} checked",
               {"changes": [c["kind"] for c in changes if c["kind"] != "skipped"],
                "skipped": sum(c["kind"] == "skipped" for c in changes)})
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
    changers: dict[str, Callable[[str], bool]] = {}
    out = []
    for r in rows[:int(limit)]:
        c = _change_out(r)
        c["undoable"] = _undoable(db, c, changers)
        out.append(c)
    return out


def _undoable(db: Db, c: dict[str, Any], changers: dict[str, Callable[[str], bool]]) -> bool:
    """Undo acts with the rights of whoever clicks it, on the definitions the change row holds. So it's
    offered only for a change that was applied (not reported) and whose author may change shortcuts in
    every lakehouse those definitions name, as they stand now (_shortcut_changer)."""
    if c["kind"] not in ("added", "removed", "repointed") or str(c.get("note") or "").startswith("reported"):
        return False
    lakes = {str(d.get("lakehouse_id") or "") for d in (c.get("before"), c.get("after")) if isinstance(d, dict)}
    if not lakes or "" in lakes:
        return False
    who = str(c.get("by_user") or "")
    if who not in changers:
        changers[who] = _shortcut_changer(db, who)
    return all(changers[who](lh) for lh in lakes)


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


# --------------------------------------------------------------------------- product groups (Preview)
# A data product offers tables (its ports, from the project's definition), semantic models and reports.
# The product's owner picks a model or report from Fabric; the catalog keeps only its item id, type,
# workspace id and display name. Nothing here writes to Fabric. Each item's endorsement is what the last
# tenant scan read (catalog.endorsement), unknown until a scan has read it.

ITEM_TYPES = ("SemanticModel", "Report")


def ensure_product_items(db: Db) -> list[str]:
    """catalog.product_item, added after trust badges: additive, safe to run again; adding calls it."""
    if db.exists("catalog", "product_item"):
        return []
    db.run(f"CREATE TABLE {db.t('catalog', 'product_item')} ({', '.join(_column(db, n, k) for n, k in _CATALOG['product_item'])})")
    db.commit()
    return ["catalog.product_item"]


def _item_out(db: Db, row: dict[str, Any]) -> dict[str, Any]:
    seen = _row(db, "endorsement", "item_id", row["item_id"]) or {}
    return {k: row.get(k) or "" for k in ("product_id", "item_id", "item_type", "workspace_id", "workspace_name",
                                          "item_name", "added_by", "added_at")} | {
        "endorsement": seen.get("endorsement") or None, "certified_by": seen.get("certified_by") or "",
        "scanned_at": seen.get("scanned_at") or ""}


def list_product_items(db: Db, product_id: str = "") -> list[dict[str, Any]]:
    """The semantic models and reports under a product (every product's with no id), each with the
    endorsement the last tenant scan read (None when none has: unknown, never guessed)."""
    ensure_product_items(db)
    ensure_trust_badges(db)
    t = db.t("catalog", "product_item")
    rows = (db.rows(f"SELECT * FROM {t} WHERE product_id = ?", (str(product_id),)) if product_id
            else db.rows(f"SELECT * FROM {t}"))
    order = {k: n for n, k in enumerate(ITEM_TYPES)}
    return sorted((_item_out(db, r) for r in rows),
                  key=lambda i: (i["product_id"], order.get(i["item_type"], 9), i["item_name"].lower(), i["item_id"]))


def _fabric_id(value: Any, what: str) -> str:
    text = str(value or "").strip().lower()
    if not _GUID.match(text):
        raise PlatformError(f"{what} needs its Fabric id")
    return text


def add_product_item(db: Db, product_id: str, item: dict[str, Any], by: str = "") -> list[dict[str, Any]]:
    """Put a semantic model or report from Fabric under a product. The product's owner or a platform
    admin. Records the item's id, type, workspace id and display name; nothing in Fabric changes. The
    same item again updates its name."""
    ensure_product_items(db)
    product = _product(db, product_id)
    if not _can_edit_terms(db, product, by):
        raise PlatformError("Only the product's owner or a platform admin can add semantic models and reports")
    item = dict(item or {})
    kind = str(item.get("item_type") or "").strip()
    if kind not in ITEM_TYPES:
        raise PlatformError("A product groups semantic models and reports: item_type is SemanticModel or Report")
    iid = _fabric_id(item.get("item_id"), "The item")
    ws = _fabric_id(item.get("workspace_id"), "The item's workspace")
    name = _SPACES.sub(" ", str(item.get("item_name") or "")).strip()
    if not name or len(name) > MAX_BK:
        raise PlatformError(f"The item needs its display name, up to {MAX_BK} characters")
    row = {"id": f"{product['id']}|{iid}", "product_id": product["id"], "item_id": iid, "item_type": kind,
           "workspace_id": ws, "workspace_name": _SPACES.sub(" ", str(item.get("workspace_name") or "")).strip()[:MAX_BK],
           "item_name": name, "added_by": _who(by), "added_at": _now()}
    _upsert(db, "catalog", "product_item", "id", row)
    _audit(db, by, "add product item", product["id"], {"item_type": kind, "item_id": iid, "workspace_id": ws, "item_name": name})
    db.commit()
    return list_product_items(db, product["id"])


def remove_product_item(db: Db, product_id: str, item_id: str, by: str = "") -> list[dict[str, Any]]:
    """Take a semantic model or report off a product; the item stays in Fabric. The product's owner or a platform admin."""
    ensure_product_items(db)
    product = _product(db, product_id)
    if not _can_edit_terms(db, product, by):
        raise PlatformError("Only the product's owner or a platform admin can remove semantic models and reports")
    iid = str(item_id or "").strip().lower()
    found = _row(db, "product_item", "id", f"{product['id']}|{iid}")
    if found is None:
        raise PlatformError(f"{item_id} isn't a semantic model or report on {product['id']}")
    db.run(f"DELETE FROM {db.t('catalog', 'product_item')} WHERE id = ?", (found["id"],))
    _audit(db, by, "remove product item", product["id"],
           {"item_type": found.get("item_type") or "", "item_id": iid, "item_name": found.get("item_name") or ""})
    db.commit()
    return list_product_items(db, product["id"])


# --------------------------------------------------------------------------- calls made once

# Operations that only read. The app sends them again after a timeout, a dropped connection or a busy
# platform as they are (tests/test_central_roles.py checks each one changes nothing).
READ_OPERATIONS = frozenset({
    "access_reviews", "audit", "enrichment", "entra_group_naming", "export_keys", "get_policy", "key_stats",
    "keys_state", "list_admins", "list_assignments", "list_domains", "list_entra_groups", "list_follows",
    "list_glossary", "list_grants", "list_key_tables", "list_landing_zones", "list_policies",
    "list_policy_exceptions", "list_policy_templates", "list_product_items", "list_products",
    "list_project_members", "list_projects", "list_requests", "list_seen_shortcuts", "list_shortcut_risks",
    "list_sources", "lookup_key", "owner_coverage", "policies_ready", "policy_applicability", "policy_history",
    "policy_options", "policy_snapshot", "policy_version", "product_followers", "product_history",
    "product_trust", "resolve_policies", "resolve_port", "shortcut_changes", "shortcut_rules", "whoami",
})
# Writes whose second run changes nothing the first didn't: Finish or repair, and issuing keys (a
# business key keeps the key it was given). Sent again as they are, like reads.
REPEATABLE_OPERATIONS = frozenset({"ensure_schema", "ensure_policy_schema", "ensure_template_schema", "issue_keys"})
CALL_DAYS = 7
MAX_CALL_RESULT = 1_000_000
CALL_WAITS = 10  # half seconds a second call waits for the first one's answer
_NOT_KEPT = "-"  # the answer of a call made, too big to keep (not JSON, so never an answer)
_CALL_ID = re.compile(r"[0-9a-f]{32}")


def call_once(db: Db, call_id: str, operation: str, args: dict[str, Any] | None = None,
              by: str = "") -> dict[str, Any]:
    """Run the change ``operation`` once for ``call_id``, which the app makes once per change and keeps
    when it sends the change again after a timeout or a dropped connection: the platform may have made
    it already. A call id seen before gets its first answer back and changes nothing. The operation
    checks who may make the change as it always does. Answers: {"result": ..., "replayed": bool}."""
    op = str(operation or "")
    cid = str(call_id or "").strip().lower()
    if not _CALL_ID.fullmatch(cid):
        raise PlatformError("A call id is 32 hexadecimal characters")
    if op not in OPERATIONS or op == "call_once" or op in READ_OPERATIONS or op in REPEATABLE_OPERATIONS:
        raise PlatformError(f"{op!r} isn't a change made through call_once")
    args = dict(args or {})
    if "by" in args:
        raise PlatformError("Who makes a change is the caller, never an argument")
    fn = cast(Callable[..., Any], OPERATIONS[op])  # OPERATIONS is built below this function
    takes_by = "by" in fn.__code__.co_varnames[:fn.__code__.co_argcount]
    who = _who(by)
    calls = db.t("catalog", "call")
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=CALL_DAYS)).isoformat(timespec="seconds")
    db.run(f"DELETE FROM {calls} WHERE at < ?", (cutoff,))
    try:
        # Kept uncommitted until the change commits, so the two go in together; a second call with the
        # same id waits for this one, then finds its row.
        db.run(f"INSERT INTO {calls} (id, op, by_user, at, result) VALUES (?, ?, ?, ?, NULL)", (cid, op, who, _now()))
    except Exception:  # the id is taken: read what the first call left
        db.conn.rollback()
        row = _row(db, "call", "id", cid)
        for _ in range(CALL_WAITS):  # the first call made the change and is still writing its answer
            if row is None or row.get("result") is not None:
                break
            time.sleep(0.5)
            row = _row(db, "call", "id", cid)
        if row is None:
            raise
        if row["op"] != op or (row.get("by_user") or "") != who:
            raise PlatformError("That call id belongs to another change") from None
        if row.get("result") in (None, _NOT_KEPT):
            raise PlatformError(f"This {op.replace('_', ' ')} was made, but its answer wasn't kept. "
                                "Refresh to see it.") from None
        return {"result": json.loads(row["result"]), "replayed": True}
    try:
        out = fn(db, **args, **({"by": by} if takes_by else {}))
    except Exception:
        # Refused or failed: the id is freed, so the same change can be sent again.
        db.conn.rollback()
        db.run(f"DELETE FROM {calls} WHERE id = ?", (cid,))
        db.commit()
        raise
    text = json.dumps(out, default=str)
    db.run(f"UPDATE {calls} SET result = ? WHERE id = ?", (text if len(text) <= MAX_CALL_RESULT else _NOT_KEPT, cid))
    db.commit()
    return {"result": out, "replayed": False}


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
    "product_history": product_history,
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
    "access_reviews": access_reviews, "confirm_access": confirm_access,
    "list_project_members": list_project_members, "set_project_member": set_project_member,
    "follow_product": follow_product, "unfollow_product": unfollow_product, "list_follows": list_follows,
    "product_followers": product_followers,
    "entra_group_naming": entra_group_naming, "save_entra_group_naming": save_entra_group_naming,
    "list_entra_groups": list_entra_groups, "link_entra_group": link_entra_group, "unlink_entra_group": unlink_entra_group,
    "list_product_items": list_product_items, "add_product_item": add_product_item, "remove_product_item": remove_product_item,
    "call_once": call_once,
}
