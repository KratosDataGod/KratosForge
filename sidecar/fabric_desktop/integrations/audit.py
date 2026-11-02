"""The audit trail for external AI tools (PR-AI-010): one line for every call an external tool makes.

Every call through ``Registry.invoke`` (MCP over stdio or HTTP, and the REST API) adds a line,
whatever happened to it: refused, waiting for approval, denied, failed or done. An approved call
adds a second line before it runs. Each line holds

    at, seconds         when the call arrived (UTC) and how long it took, approval wait included
    channel             mcp-stdio, mcp-http or api
    client_id, client   the tool, as added in Settings > Integrations
    capability, permission
    decision            allowed, approved (by the person), denied (permissions or policy),
                        approval_denied, approval_pending (nobody answered in time), invalid
    outcome             ok, or the error code (timeout, not_found, ...); "started" on the line an
                        approved call writes before it runs, so a call killed part way still shows
    approval_id         on both lines of an approved call
    execution_id        for calls that start or read an execution
    arguments           ids, names and numbers; code, SQL and long text show as "(given)", and
                        anything credential-shaped is removed
    host, os_user, version
    prev, hash          a SHA-256 chain, the same as the app's audit trail (PR #89), so
                        ``fabric-desktop audit --folder <data>\\integrations\\audit --verify`` checks it

The lines go to ``<data>/integrations/audit/audit-YYYY-MM.jsonl`` for log shippers, and to the
integration store for Settings > Integrations > Recent activity. Several processes write (one MCP
server per connected tool, and the HTTP server), so each line is chained under the folder's lock
(audit_chain.py, the same writer as the app's audit trail) inside a store transaction: one writer
at a time, and the chain stays whole across processes.

It is evidence, not a lock: someone who controls the PC can delete the folder.
"""

from __future__ import annotations

import getpass
import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__, audit_chain
from ..audit import target
from .core import CallContext, IntegrationError, redact_secrets

SCHEMA = "integration-audit@1"
GENESIS = audit_chain.GENESIS
GIVEN = "(given)"
KEEP_ROWS = 50_000  # in the store, for the Settings page; the files keep everything

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
    """What a line keeps of a call's arguments: identifying values only, by the app audit trail's
    rules (audit.target), so code, queries and data are left out the same way in both."""
    if values is not None and not isinstance(values, dict):
        return {"arguments": GIVEN}
    return target(values or {})


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
        with self.store._db() as db:
            db.executescript(_SQL)
        self.host = socket.gethostname()
        try:
            self.os_user = getpass.getuser()
        except Exception:  # noqa: BLE001 - no user name on this system
            self.os_user = ""

    def record(self, ctx: CallContext, capability: str, permission: str, args: dict[str, Any] | None,
               started_at: float, seconds: float, error: IntegrationError | None = None,
               execution_id: str = "", stage: str = "") -> dict[str, Any]:
        """One line for a call. ``stage="started"`` is the line an approved call writes before it runs
        (outcome ``started``); its outcome line follows when it finishes, with the same approval_id."""
        client = self.store.client(ctx.caller.client_id) if ctx.caller.client_id else None
        entry: dict[str, Any] = {
            "schema": SCHEMA,
            "at": datetime.fromtimestamp(started_at, timezone.utc).isoformat(timespec="milliseconds"),
            "seconds": round(seconds, 3), "channel": ctx.caller.channel, "client_id": ctx.caller.client_id,
            "client": (client or {}).get("name") or ctx.caller.name or "", "capability": capability,
            "permission": permission, "decision": decision_for(error, ctx),
            "outcome": "started" if stage == "started" else error.code if error else "ok",
            "execution_id": execution_id, "arguments": arguments(args), "host": self.host, "os_user": self.os_user,
            "version": __version__,
        }
        if ctx.approval_id:
            entry["approval_id"] = ctx.approval_id
        if error is not None:
            entry["error"] = redact_secrets(error.message)[:500]
        with self.store._tx() as db, audit_chain.locked(self.folder):  # one writer at a time across every process
            line = audit_chain.seal(audit_chain.last_hash(self.folder), entry)
            audit_chain.write(self.folder, entry, line)
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
        rows = self.store._rows(sql + " order by seq desc limit ?", [*params, max(1, min(limit, 1000))])
        return [json.loads(r["line"]) for r in rows]

    def approvals_recorded(self, approval_ids: list[str]) -> set[str]:
        """Which of these approvals have any line in the store (started or outcome)."""
        if not approval_ids:
            return set()
        rows = self.store._rows("select distinct json_extract(line, '$.approval_id') as id from audit "
                                        f"where json_extract(line, '$.approval_id') in ({','.join('?' * len(approval_ids))})",
                                        approval_ids)
        return {r["id"] for r in rows}

    def verify(self) -> dict[str, Any]:
        """Walks the chain in the files: every line must match its hash and the line before it."""
        return audit_chain.verify(self.folder)
