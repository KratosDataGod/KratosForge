"""Integration settings, connected tools and approvals, in ``<data>/integrations/integrations.sqlite``.

Several processes use this file at once: the app's engine (the Settings page, approving), every
``fabric-desktop mcp`` an AI tool started, and the local HTTP server. SQLite in WAL mode with
``begin immediate`` keeps their writes in order.

    settings    enabled (the person's on/off switch), http_enabled, http_port
    clients     one row per connected tool: what it may do (grants), which categories it may do
                without asking (auto), optional workspace list, HTTP token hash, revoked time
    approvals   one row per request that needs the person's yes: what, by whom, on what, the
                hash of the exact arguments, and what happened to it
    approval_uses  when each approval was used, so Recent activity can show an approved call
                that left no audit line (the process was killed as it started)

HTTP tokens are stored only as SHA-256 hashes. No Fabric or Entra token is ever stored here.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

_SCHEMA = """
create table if not exists settings (key text primary key, value text not null);
create table if not exists clients (
    id text primary key, name text not null, kind text not null default 'other',
    transport text not null default 'stdio', grants text not null default '[]', auto text not null default '[]',
    workspaces text not null default '[]', token_hash text, created_at real not null, last_used_at real,
    revoked_at real);
create table if not exists approvals (
    id text primary key, client_id text not null, client_name text not null, capability text not null,
    title text not null, permission text not null, summary text not null, arguments text not null,
    args_hash text not null, state text not null, created_at real not null, decided_at real,
    expires_at real not null, note text not null default '');
create index if not exists approvals_state on approvals (state, created_at);
create table if not exists approval_uses (approval_id text primary key, client_id text not null, used_at real not null);
"""

PENDING_SECONDS = 300      # a request nobody answers is dropped after 5 minutes
APPROVED_SECONDS = 600     # an approval must be used within 10 minutes
MAX_ARGUMENT_TEXT = 20_000
USES_SECONDS = 90 * 86400  # approval_uses rows are kept 90 days

DEFAULT_SETTINGS: dict[str, Any] = {"enabled": False, "http_enabled": False, "http_port": 47615,
                                    "extensions": {}}  # extension id -> on (extensions.py)


def args_hash(capability: str, arguments: dict[str, Any]) -> str:
    """Identifies one exact request, so an approval can only be used for what was approved."""
    body = json.dumps({"capability": capability, "arguments": arguments}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(5)


class IntegrationStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        with self._db() as db:
            db.execute("pragma journal_mode=wal")  # kept in the file, so later connections use it too
            db.executescript(_SCHEMA)

    @classmethod
    def at(cls, root: Path) -> "IntegrationStore":
        return cls(Path(root) / "integrations" / "integrations.sqlite")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """A connection for one read or change, closed after it. One kept per thread was never closed:
        each MCP call runs on a new worker thread, and on Windows an open handle keeps the file locked."""
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            yield db
        finally:
            db.close()

    def _rows(self, sql: str, args: Any = ()) -> list[sqlite3.Row]:
        with self._db() as db:
            return db.execute(sql, args).fetchall()

    def _one(self, sql: str, args: Any = ()) -> sqlite3.Row | None:
        rows = self._rows(sql, args)
        return rows[0] if rows else None

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._db() as db:
            db.execute("begin immediate")
            try:
                yield db
            except BaseException:
                db.execute("rollback")
                raise
            db.execute("commit")

    # ---- settings

    def settings(self) -> dict[str, Any]:
        rows = {r["key"]: json.loads(r["value"]) for r in self._rows("select key, value from settings")}
        return {k: rows.get(k, v) for k, v in DEFAULT_SETTINGS.items()}

    def set_setting(self, key: str, value: Any) -> dict[str, Any]:
        if key not in DEFAULT_SETTINGS:
            raise ValueError(f"unknown integration setting {key!r}")
        with self._tx() as db:
            db.execute("insert into settings (key, value) values (?, ?) on conflict(key) do update set value = excluded.value",
                       (key, json.dumps(value)))
        return self.settings()

    # ---- clients

    @staticmethod
    def _client(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        for key in ("grants", "auto", "workspaces"):
            out[key] = json.loads(out[key])
        out["has_token"] = bool(out.pop("token_hash"))
        return out

    def clients(self, include_revoked: bool = False) -> list[dict[str, Any]]:
        sql = "select * from clients" + ("" if include_revoked else " where revoked_at is null") + " order by created_at"
        return [self._client(r) for r in self._rows(sql)]  # type: ignore[misc]

    def client(self, client_id: str) -> dict[str, Any] | None:
        return self._client(self._one("select * from clients where id = ?", (client_id,)))

    def client_by_token(self, token: str) -> dict[str, Any] | None:
        row = self._one("select * from clients where token_hash = ? and revoked_at is null", (token_hash(token),))
        return self._client(row)

    def add_client(self, name: str, kind: str = "other", transport: str = "stdio", grants: list[str] | None = None,
                   auto: list[str] | None = None, workspaces: list[str] | None = None) -> tuple[dict[str, Any], str | None]:
        """A new connected tool. Returns it and, for HTTP, its bearer token (shown once, never stored)."""
        client_id = new_id("cl_")
        token = ("fdt_" + secrets.token_urlsafe(32)) if transport == "http" else None
        with self._tx() as db:
            db.execute("insert into clients (id, name, kind, transport, grants, auto, workspaces, token_hash, created_at) "
                       "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (client_id, name, kind, transport, json.dumps(sorted(set(grants or []))), json.dumps(sorted(set(auto or []))),
                        json.dumps(workspaces or []), token_hash(token) if token else None, self.clock()))
        return self.client(client_id), token  # type: ignore[return-value]

    def update_client(self, client_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {"name", "grants", "auto", "workspaces"}
        bad = set(changes) - allowed
        if bad:
            raise ValueError(f"can't change {', '.join(sorted(bad))}")
        if self.client(client_id) is None:
            raise LookupError(f"No connected tool {client_id}")
        with self._tx() as db:
            for key, value in changes.items():
                stored = json.dumps(sorted(set(value)) if key in ("grants", "auto") else value) if key != "name" else value
                db.execute(f"update clients set {key} = ? where id = ?", (stored, client_id))  # noqa: S608 - key is allowlisted
        return self.client(client_id)  # type: ignore[return-value]

    def new_token(self, client_id: str) -> str:
        """Replace an HTTP client's token; the old one stops working at once."""
        client = self.client(client_id)
        if client is None or client["revoked_at"]:
            raise LookupError(f"No connected tool {client_id}")
        token = "fdt_" + secrets.token_urlsafe(32)
        with self._tx() as db:
            db.execute("update clients set token_hash = ?, transport = 'http' where id = ?", (token_hash(token), client_id))
        return token

    def revoke_client(self, client_id: str) -> dict[str, Any]:
        """Disconnect a tool: every later call it makes is refused, its token stops working and its
        waiting requests are dropped."""
        now = self.clock()
        with self._tx() as db:
            db.execute("update clients set revoked_at = ?, token_hash = null where id = ? and revoked_at is null", (now, client_id))
            db.execute("update approvals set state = 'expired', decided_at = ?, note = 'tool disconnected' "
                       "where client_id = ? and state in ('pending', 'approved')", (now, client_id))
        client = self.client(client_id)
        if client is None:
            raise LookupError(f"No connected tool {client_id}")
        return client

    def touch(self, client_id: str) -> None:
        with self._tx() as db:
            db.execute("update clients set last_used_at = ? where id = ?", (self.clock(), client_id))

    # ---- approvals

    @staticmethod
    def _approval(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        out["summary"] = json.loads(out["summary"])
        out["arguments"] = json.loads(out["arguments"])
        return out

    def _expire(self, db: sqlite3.Connection) -> None:
        now = self.clock()
        db.execute("update approvals set state = 'expired', decided_at = ? where state in ('pending', 'approved') "
                   "and expires_at < ?", (now, now))

    def request_approval(self, client: dict[str, Any], capability: str, title: str, permission: str,
                         summary: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        """The request waiting for this exact call, or a new one. One request per identical call."""
        digest = args_hash(capability, arguments)
        text = json.dumps(arguments, default=str)
        shown = arguments if len(text) <= MAX_ARGUMENT_TEXT else {"(too long to show)": text[:MAX_ARGUMENT_TEXT]}
        with self._tx() as db:
            self._expire(db)
            # A recent "no" stands for the same call, so a tool can't ask again and again.
            row = db.execute("select * from approvals where client_id = ? and args_hash = ? and (state in ('pending', 'approved') "
                             "or (state = 'denied' and expires_at >= ?)) order by created_at desc limit 1",
                             (client["id"], digest, self.clock())).fetchone()
            if row is not None:
                return self._approval(row)  # type: ignore[return-value]
            approval_id = new_id("ap_")
            now = self.clock()
            db.execute("insert into approvals (id, client_id, client_name, capability, title, permission, summary, arguments, "
                       "args_hash, state, created_at, expires_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                       (approval_id, client["id"], client["name"], capability, title, permission, json.dumps(summary, default=str),
                        json.dumps(shown, default=str), digest, now, now + PENDING_SECONDS))
        return self.approval(approval_id)  # type: ignore[return-value]

    def approval(self, approval_id: str) -> dict[str, Any] | None:
        with self._tx() as db:
            self._expire(db)
        return self._approval(self._one("select * from approvals where id = ?", (approval_id,)))

    def approvals(self, state: str = "pending", limit: int = 50) -> list[dict[str, Any]]:
        with self._tx() as db:
            self._expire(db)
        sql = "select * from approvals" + (" where state = ?" if state else "") + " order by created_at desc limit ?"
        args: tuple[Any, ...] = (state, limit) if state else (limit,)
        return [self._approval(r) for r in self._rows(sql, args)]  # type: ignore[misc]

    def decide(self, approval_id: str, approve: bool, note: str = "") -> dict[str, Any]:
        """The person's answer. Only the app's own engine calls this; no external interface can."""
        now = self.clock()
        with self._tx() as db:
            self._expire(db)
            row = db.execute("select state from approvals where id = ?", (approval_id,)).fetchone()
            if row is None:
                raise LookupError(f"No request {approval_id}")
            if row["state"] != "pending":
                raise ValueError(f"This request was already {row['state']}.")
            db.execute("update approvals set state = ?, decided_at = ?, expires_at = ?, note = ? where id = ?",
                       ("approved" if approve else "denied", now, now + APPROVED_SECONDS, note, approval_id))
        return self.approval(approval_id)  # type: ignore[return-value]

    def consume(self, approval_id: str, client_id: str, digest: str) -> bool:
        """Use an approval once, for exactly the call it was given for."""
        with self._tx() as db:
            self._expire(db)
            changed = db.execute("update approvals set state = 'used' where id = ? and client_id = ? and args_hash = ? "
                                 "and state = 'approved'", (approval_id, client_id, digest)).rowcount
            if changed == 1:
                db.execute("insert or replace into approval_uses (approval_id, client_id, used_at) values (?,?,?)",
                           (approval_id, client_id, self.clock()))
                db.execute("delete from approval_uses where used_at < ?", (self.clock() - USES_SECONDS,))
        return changed == 1

    def approval_uses(self, since: float, client_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        """Approvals used since ``since``, newest first, with what they were for."""
        sql = ("select u.used_at, a.* from approval_uses u join approvals a on a.id = u.approval_id where u.used_at >= ?"
               + (" and u.client_id = ?" if client_id else "") + " order by u.used_at desc limit ?")
        rows = self._rows(sql, [since, *([client_id] if client_id else []), limit])
        return [{**(self._approval(r) or {}), "used_at": r["used_at"]} for r in rows]

