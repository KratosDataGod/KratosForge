"""The audit trail for external AI tools (PR-AI-010): one line for every call an external tool makes.

Every call through ``Registry.invoke`` (MCP over stdio or HTTP, and the REST API) adds a line,
whatever happened to it: refused, waiting for approval, denied, failed or done. Each line holds

    at, seconds         when the call arrived (UTC) and how long it took, approval wait included
    channel             mcp-stdio, mcp-http or api
    client_id, client   the tool, as added in Settings > Integrations
    capability, permission
    decision            allowed, approved (by the person), denied (permissions or policy),
                        approval_denied, approval_pending (nobody answered in time), invalid
    outcome             ok, or the error code (timeout, not_found, ...)
    execution_id        for calls that start or read an execution
    arguments           ids, names and numbers; code, SQL and long text show as "(given)", and
                        anything credential-shaped is removed
    host, os_user, version
    prev, hash          a SHA-256 chain, the same as the app's audit trail (PR #89), so
                        ``fabric-desktop audit --folder <data>\\integrations\\audit --verify`` checks it

The lines go to ``<data>/integrations/audit/audit-YYYY-MM.jsonl`` for log shippers, and to the
integration store for Settings > Integrations > Recent activity. Several processes write (one MCP
server per connected tool, and the HTTP server), so each line is written inside a store
transaction: one writer at a time, and the chain stays whole across processes.

It is evidence, not a lock: someone who controls the PC can delete the folder.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from .core import CallContext, IntegrationError, redact_secrets

SCHEMA = "integration-audit@1"
GENESIS = "0" * 64
GIVEN = "(given)"
KEEP_ROWS = 50_000  # in the store, for the Settings page; the files keep everything
_MAX_TEXT = 200
# Arguments that carry code, queries or free text: never recorded.
_CONTENT = frozenset({"query", "sql", "code", "source", "text", "cells", "content", "body", "values", "rows", "data", "notes"})

# Error codes from the permission checks, and the decision they record.
_DECISIONS = {"permission_denied": "denied", "integrations_disabled": "denied", "approval_denied": "approval_denied",
              "approval_required": "approval_pending", "rate_limited": "denied"}

_SQL = """
create table if not exists audit (
    seq integer primary key autoincrement, at text not null, channel text not null, client_id text not null,
    client text not null, capability text not null, decision text not null, outcome text not null,
    execution_id text not null default '', line text not null, hash text not null);
create index if not exists audit_client on audit (client_id, seq);
"""


def arguments(values: dict[str, Any] | None) -> dict[str, Any]:
    """What a line keeps of a call's arguments: identifying values only."""
    if values is not None and not isinstance(values, dict):
        return {"arguments": GIVEN}
    out: dict[str, Any] = {}
    for key, value in (values or {}).items():
        lower = key.lower()
        if lower in _CONTENT or any(w in lower for w in ("secret", "password", "token")):
            out[key] = GIVEN
        elif isinstance(value, (bool, int, float)) or value is None:
            out[key] = value
        elif isinstance(value, str):
            out[key] = redact_secrets(value) if len(value) <= _MAX_TEXT else GIVEN
        elif isinstance(value, list) and len(value) <= 20 and all(isinstance(v, str) and len(v) <= _MAX_TEXT for v in value):
            out[key] = [redact_secrets(v) for v in value]
        elif isinstance(value, dict):
            inner = arguments(value)
            out[key] = inner if len(json.dumps(inner, default=str)) <= 4 * _MAX_TEXT else GIVEN
        else:
            out[key] = GIVEN
    return out


def _hash(prev: str, entry: dict[str, Any]) -> str:
    # The same as audit.py in the app's audit trail, so one verifier reads both.
    body = json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev + body).encode()).hexdigest()


def decision_for(error: IntegrationError | None, ctx: CallContext) -> str:
    """What was decided about the call. A call that failed after it was allowed (the notebook
    wasn't there, the query failed) keeps its decision; one that failed before the checks (an
    unknown capability, bad arguments) is "invalid"."""
    if error is not None and error.code in _DECISIONS:
        return _DECISIONS[error.code]
    if ctx.decision:
        return ctx.decision
    return "invalid" if error is not None else "allowed"


class IntegrationAudit:
    def __init__(self, store: Any, folder: Path | None = None):
        self.store = store
        self.folder = Path(folder) if folder else Path(store.path).parent / "audit"
        self.store._db().executescript(_SQL)
        self.host = socket.gethostname()
        try:
            self.os_user = getpass.getuser()
        except Exception:  # noqa: BLE001 - no user name on this system
            self.os_user = ""

    def record(self, ctx: CallContext, capability: str, permission: str, args: dict[str, Any] | None,
               started_at: float, seconds: float, error: IntegrationError | None = None,
               execution_id: str = "") -> dict[str, Any]:
        client = self.store.client(ctx.caller.client_id) if ctx.caller.client_id else None
        entry: dict[str, Any] = {
            "schema": SCHEMA,
            "at": datetime.fromtimestamp(started_at, timezone.utc).isoformat(timespec="milliseconds"),
            "seconds": round(seconds, 3), "channel": ctx.caller.channel, "client_id": ctx.caller.client_id,
            "client": (client or {}).get("name") or ctx.caller.name or "", "capability": capability,
            "permission": permission, "decision": decision_for(error, ctx), "outcome": error.code if error else "ok",
            "execution_id": execution_id, "arguments": arguments(args), "host": self.host, "os_user": self.os_user,
            "version": __version__,
        }
        if error is not None:
            entry["error"] = redact_secrets(error.message)[:500]
        with self.store._tx() as db:  # one writer at a time across every process
            last = db.execute("select hash from audit order by seq desc limit 1").fetchone()
            prev = last["hash"] if last else GENESIS
            entry["prev"] = prev
            entry["hash"] = _hash(prev, entry)
            line = json.dumps(entry, default=str)
            self.folder.mkdir(parents=True, exist_ok=True)
            with (self.folder / f"audit-{entry['at'][:7]}.jsonl").open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            db.execute("insert into audit (at, channel, client_id, client, capability, decision, outcome, execution_id,"
                       " line, hash) values (?,?,?,?,?,?,?,?,?,?)",
                       (entry["at"], entry["channel"], entry["client_id"], entry["client"], capability,
                        entry["decision"], entry["outcome"], execution_id, line, entry["hash"]))
            db.execute("delete from audit where seq <= (select max(seq) from audit) - ?", (KEEP_ROWS,))
        return entry

    def recent(self, limit: int = 100, client_id: str = "") -> list[dict[str, Any]]:
        sql, params = "select line from audit", []
        if client_id:
            sql, params = sql + " where client_id = ?", [client_id]
        rows = self.store._db().execute(sql + " order by seq desc limit ?", [*params, max(1, min(limit, 1000))])
        return [json.loads(r["line"]) for r in rows]

    def verify(self) -> dict[str, Any]:
        """Walks the chain in the files: every line must match its hash and the line before it."""
        prev, count, problems = GENESIS, 0, []
        for path in sorted(self.folder.glob("audit-*.jsonl")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                count += 1
                try:
                    entry = json.loads(line)
                    stored = entry.pop("hash")
                except (ValueError, KeyError):
                    problems.append(f"{path.name} line {number}: not an audit line")
                    continue
                if entry.get("prev") != prev:
                    problems.append(f"{path.name} line {number}: a line before it is missing or changed")
                if _hash(entry.get("prev", ""), entry) != stored:
                    problems.append(f"{path.name} line {number}: this line was changed")
                prev = stored
        return {"lines": count, "ok": not problems, "problems": problems}

