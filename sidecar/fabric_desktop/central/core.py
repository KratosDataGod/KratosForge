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
    catalog.request      requests from domain teams: a key table, a source, or access to a product
    catalog.grant        projects let in to a domain or private product by its owner
    catalog.shortcut     every OneLake shortcut last seen in a scan: its target and health
    catalog.shortcut_scope  lakehouses scanned for shortcuts, so a first scan isn't read as "all added"
    catalog.shortcut_change every shortcut added, repointed, removed, broken or fixed, and by whom
    catalog.policy*      governance policies, their versions, assignments and history (policy.py)

Who may change what (the caller is always who Fabric says it is, see udf.py):

    platform admins      domains, key tables, source systems, glossary, admins, shortcut rules, and every request
    domain owners        glossary terms of their domain
    product owners       access requests to their product
    everyone             reading, issuing keys from existing key tables, catalog text, products, links, requests
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from typing import Any

SCHEMA_VERSION = 5
UNKNOWN_KEY = -1
UNKNOWN_BK = "UNKNOWN"
MAX_BK = 400
ACCESS = ("public", "domain", "private")
STATUS = ("draft", "published", "deprecated")
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
REQUEST_KINDS = ("key_table", "source", "access", "shortcut")
REQUEST_STATUS = ("waiting", "approved", "declined", "withdrawn")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


# Tables other modules add to the platform (policy.py's governance policies), created with the rest.
SCHEMA_HOOKS: list[Any] = []


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
               ("fabric_domain", "text"), ("updated_at", "text"), ("updated_by", "text")],
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
               ("source_id", "text"), ("code", "text"), ("system_type", "text"), ("active", "text")],
    "reader": [("id", "key"), ("project", "text"), ("source", "text"), ("entities", "long"), ("updated_at", "text")],
    "request": [("id", "key"), ("kind", "text"), ("target", "text"), ("detail", "long"), ("project", "text"),
                ("reason", "long"), ("requested_by", "text"), ("requested_at", "text"), ("status", "text"),
                ("decided_by", "text"), ("decided_at", "text"), ("note", "long")],
    "grant": [("id", "key"), ("product_id", "text"), ("project", "text"), ("request_id", "text"),
              ("granted_by", "text"), ("granted_at", "text")],
    "shortcut": [("id", "key"), ("workspace_id", "text"), ("workspace_name", "text"), ("lakehouse_id", "text"),
                 ("lakehouse_name", "text"), ("path", "text"), ("name", "text"), ("target", "long"),
                 ("health", "text"), ("health_detail", "long"), ("checked_at", "text"), ("first_seen", "text"),
                 ("last_seen", "text")],
    "shortcut_scope": [("lakehouse_id", "key"), ("first_scan", "text"), ("last_scan", "text")],
    "shortcut_change": [("id", "key"), ("at", "text"), ("kind", "text"), ("shortcut_id", "text"),
                        ("workspace_name", "text"), ("lakehouse_name", "text"), ("name", "text"),
                        ("before_def", "long"), ("after_def", "long"), ("by_user", "text"), ("how", "text"),
                        ("note", "long")],
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
                         "Platform > Business keys > Request a key table")


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


def issue_keys(db: Db, name: str, business_keys: list[Any]) -> dict[str, Any]:
    """The surrogate key of every business key given, issuing the next keys for new ones.

    New keys are inserted in business key order inside one transaction, under a lock on the
    range they would go in, so two runs asking at the same time never get two keys for one
    business key. Returns {"keys": {bk: sk}, "inserted": n}."""
    name = key_name(name)
    bks = _clean_bks(business_keys)
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


def import_fabric_domains(db: Db, domains: list[dict[str, Any]], by: str = "") -> dict[str, Any]:
    """Make the catalog's domains follow Fabric's domains and subdomains (``GET /v1/admin/domains``).

    A Fabric domain matches the catalog domain already linked to it (``fabric_domain``), else one
    with the same name that isn't linked yet, else it's added. Linked domains take Fabric's name and
    parent; their owners and description stay as the platform set them. Domains that are no longer
    in Fabric are reported, never deleted: projects may still use them. Platform admins only.
    """
    require_admin(db, by, "import domains from Fabric")
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
        ids[fid] = row["id"]
        if kind != "unchanged":
            new = {"id": row["id"], "name": name, "parent_id": parent,
                   "description": str(row.get("description") or d.get("description") or ""),
                   "owners": str(row.get("owners") or ""), "fabric_domain": fid, "updated_at": _now(), "updated_by": by}
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
    _audit(db, by, "publish product", pid, {"env": env, "ports": [p["port"] for p in ports]})
    db.commit()
    return row


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


def _source_columns(db: Db) -> set[str]:
    if db.tsql:
        return {r["name"] for r in db.rows("SELECT name FROM sys.columns WHERE object_id = OBJECT_ID('catalog.source')")}
    return {r["name"] for r in db.rows(f"PRAGMA table_info({db.t('catalog', 'source')})")}


def ensure_source_ids(db: Db) -> list[str]:
    """Give catalog.source the columns of schema 5 and every source an id. A platform made before
    keeps its sources: each gets the next free id, in name order, and a code from its name."""
    added = []
    have = _source_columns(db)
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


def list_sources(db: Db) -> list[dict[str, Any]]:
    ensure_source_ids(db)
    sources = db.rows(f"SELECT * FROM {db.t('catalog', 'source')}")
    readers = db.rows(f"SELECT * FROM {db.t('catalog', 'reader')}")
    for src in sources:
        src["connections"] = json.loads(src["connections"] or "{}")
        src["source_id"] = int(src["source_id"])
        src["active"] = src["active"] != "0"
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
            "active": "0" if source.get("active") is False else "1"}


def _put_source(db: Db, row: dict[str, Any], source_id: Any = None) -> None:
    """Add or change one source. An existing source keeps its id; a new one takes ``source_id``
    (a project's own id, when sources move to the platform) or the next free one."""
    ensure_source_ids(db)
    before = _row(db, "source", "name", row["name"])
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
    projects with the same id everywhere. Platform admins only."""
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


def _save_readers(db: Db, project: str, landing: dict[str, list[str]]) -> None:
    db.run(f"DELETE FROM {db.t('catalog', 'reader')} WHERE project = ?", (project,))
    for source, entities in sorted((landing or {}).items()):
        db.run(f"INSERT INTO {db.t('catalog', 'reader')} (id, project, source, entities, updated_at) VALUES (?, ?, ?, ?, ?)",
               (f"{project}:{source}", project, source, json.dumps(sorted(entities)), _now()))


# --------------------------------------------------------------------------- requests


def _product_owners(product: dict[str, Any]) -> set[str]:
    return _names(product.get("owner"))


def request(db: Db, kind: str, target: str, project: str = "", reason: str = "", detail: dict[str, Any] | None = None,
            by: str = "") -> dict[str, Any]:
    """Ask for something only someone else may give: a key table or source (platform admins), or
    access to a domain or private data product (its owner)."""
    if kind not in REQUEST_KINDS:
        raise PlatformError(f"kind must be one of {', '.join(REQUEST_KINDS)}")
    detail = dict(detail or {})
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
    for r in db.rows(f"SELECT * FROM {db.t('catalog', 'request')} WHERE kind = ? AND target = ? AND status = 'waiting'",
                     (kind, target)):
        if kind != "access" or r["project"] == project:
            raise PlatformError(f"There's already a request waiting for {target}, from {r['requested_by'] or 'someone'}")
    row = {"id": uuid.uuid4().hex[:12], "kind": kind, "target": target, "detail": json.dumps(detail),
           "project": project or "", "reason": str(reason or ""), "requested_by": _who(by), "requested_at": _now(),
           "status": "waiting", "decided_by": "", "decided_at": "", "note": ""}
    _upsert(db, "catalog", "request", "id", row)
    _audit(db, by, f"request {kind}", target, {"project": project})
    db.commit()
    return _request_out(row)


def _request_out(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row, detail=json.loads(row.get("detail") or "{}"))


def _deciders(db: Db, req: dict[str, Any]) -> set[str]:
    if req["kind"] == "access":
        product = _row(db, "product", "id", req["target"]) or {}
        return _product_owners(product)
    if req["kind"] == "shortcut" and req["detail"].get("product_id"):
        return _product_owners(_row(db, "product", "id", req["detail"]["product_id"]) or {})
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


# --------------------------------------------------------------------------- shortcuts
# Fabric doesn't say who changes a shortcut. The app records its own changes as they happen
# (log_shortcut_change), and every scan compares what it sees with what was seen last
# (record_shortcuts), so changes made in Fabric show up too, as "seen at scan".

SHORTCUT_CHANGES = ("added", "removed", "repointed", "broke", "fixed")
HEALTH = ("healthy", "broken", "unreachable", "not_checked")
RULE_MODES = ("off", "warn", "block")
DEFAULT_SHORTCUT_RULES = {"cross_env": "block", "storage_role": "warn", "deprecated_product": "warn",
                          "connections": "off", "allowed_connections": [], "who_creates": "anyone"}


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
        if before is None:
            row |= {"health": "not_checked", "health_detail": "", "checked_at": "", "first_seen": now}
            if str(s["lakehouse_id"]) not in first:
                changes.append(_change(db, "added", sid, s, None, _definition(s), "", "scan"))
        elif _canon(json.loads(before.get("target") or "{}")) != row["target"]:
            changes.append(_change(db, "repointed", sid, s, _definition(_shortcut_out(before)), _definition(s), "", "scan"))
            row |= {"health": "not_checked", "health_detail": "", "checked_at": ""}
        _upsert(db, "catalog", "shortcut", "id", row)
    for sid, before in stored.items():
        if sid not in seen:
            changes.append(_change(db, "removed", sid, before, _definition(_shortcut_out(before)), None, "", "scan"))
            db.run(f"DELETE FROM {db.t('catalog', 'shortcut')} WHERE id = ?", (sid,))
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
            "checked_at": "", "first_seen": prev.get("first_seen") or now, "last_seen": now})
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


# Everything a caller (the app, a notebook, a report button) may ask the platform to do.
OPERATIONS = {
    "ensure_schema": ensure_schema, "ensure_key_table": ensure_key_table, "issue_keys": issue_keys,
    "import_keys": import_keys, "key_stats": key_stats, "lookup_key": lookup_key, "export_keys": export_keys,
    "list_domains": list_domains, "save_domain": save_domain, "delete_domain": delete_domain,
    "import_fabric_domains": import_fabric_domains,
    "register_project": register_project, "list_projects": list_projects, "register_product": register_product,
    "list_products": list_products, "resolve_port": resolve_port, "register_link": register_link,
    "save_enrichment": save_enrichment, "enrichment": enrichment, "list_glossary": list_glossary,
    "save_term": save_term, "audit": audit,
    "whoami": whoami, "list_admins": list_admins, "claim_admin": claim_admin, "add_admin": add_admin,
    "remove_admin": remove_admin, "list_key_tables": list_key_tables, "list_sources": list_sources,
    "save_source": save_source, "delete_source": delete_source, "adopt_sources": adopt_sources, "request": request, "list_requests": list_requests,
    "decide_request": decide_request, "withdraw_request": withdraw_request, "list_grants": list_grants,
    "revoke_grant": revoke_grant, "record_shortcuts": record_shortcuts, "log_shortcut_change": log_shortcut_change,
    "record_shortcut_health": record_shortcut_health, "list_seen_shortcuts": list_seen_shortcuts,
    "shortcut_changes": shortcut_changes, "shortcut_rules": shortcut_rules, "save_shortcut_rules": save_shortcut_rules,
}
