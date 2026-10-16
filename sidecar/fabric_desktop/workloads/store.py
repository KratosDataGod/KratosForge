"""workloads.sqlite: the management records this PC keeps about workloads.

    assignment   owner roles, criticality and tags assigned on this PC (declared ones live in Git)
    history      every assignment change: who, when, before, after
    link         a notebook linked to its Fabric item: recorded by the app, or made by hand
    action       what was done from a workload page, and how it ended
    cache        the last collection from each slow source, so offline shows what was last seen

These are local records: the app labels them "on this PC". Shared records come with the
platform catalog (docs/workloads/plan.md, stage 2B), which this file migrates into.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .model import CRITICALITY, ROLES, iso, now

SCHEMA_VERSION = 1
ACTION_KEEP = 10000
_FIELDS = ROLES + ("criticality", "tags")
_ENDED = ("succeeded", "failed", "cancelled")


class ManagementStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._tx() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS assignment (wid TEXT, field TEXT, value TEXT, set_by TEXT, set_at TEXT,
                    PRIMARY KEY (wid, field));
                CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, wid TEXT, field TEXT,
                    before TEXT, after TEXT, by TEXT, at TEXT);
                CREATE TABLE IF NOT EXISTS link (notebook TEXT PRIMARY KEY, workspace_id TEXT, workspace_name TEXT,
                    item_id TEXT, how TEXT, by TEXT, at TEXT);
                CREATE TABLE IF NOT EXISTS action (id TEXT PRIMARY KEY, wid TEXT, action TEXT, env TEXT,
                    target TEXT, identity TEXT, by TEXT, at TEXT, status TEXT, detail TEXT, ref TEXT,
                    ended_at TEXT);
                CREATE INDEX IF NOT EXISTS action_wid ON action (wid, at);
                CREATE TABLE IF NOT EXISTS cache (source TEXT PRIMARY KEY, collected_at TEXT, state TEXT,
                    detail TEXT, payload TEXT);
            """)
            db.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            db = sqlite3.connect(self.path, timeout=30)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            finally:
                db.close()

    # ---- assignments

    def assignments(self, wids: list[str] | None = None) -> dict[str, dict[str, dict[str, Any]]]:
        """{wid: {field: {value, set_by, set_at}}}; tags are a list."""
        with self._tx() as db:
            rows = db.execute("SELECT * FROM assignment").fetchall()
        out: dict[str, dict[str, dict[str, Any]]] = {}
        wanted = set(wids) if wids is not None else None
        for r in rows:
            if wanted is not None and r["wid"] not in wanted:
                continue
            value: Any = json.loads(r["value"]) if r["field"] == "tags" else r["value"]
            out.setdefault(r["wid"], {})[r["field"]] = {"value": value, "set_by": r["set_by"], "set_at": r["set_at"]}
        return out

    def assign(self, wid: str, changes: dict[str, Any], by: str = "") -> dict[str, dict[str, Any]]:
        """Set or clear (empty value) assigned fields. Unknown fields and criticality values are refused."""
        for field, value in changes.items():
            if field not in _FIELDS:
                raise ValueError(f"{field} can't be assigned; use one of {', '.join(_FIELDS)}")
            if field == "criticality" and value and value not in CRITICALITY:
                raise ValueError(f"criticality is one of {', '.join(CRITICALITY)}")
        at = iso(now())
        with self._tx() as db:
            for field, value in changes.items():
                if field == "tags":
                    tags = sorted({str(t).strip() for t in (value or []) if str(t).strip()})
                    text = json.dumps(tags) if tags else ""
                else:
                    text = str(value or "").strip()
                old = db.execute("SELECT value FROM assignment WHERE wid = ? AND field = ?", (wid, field)).fetchone()
                before = old["value"] if old else ""
                if before == text:
                    continue
                if text:
                    db.execute("INSERT OR REPLACE INTO assignment VALUES (?, ?, ?, ?, ?)", (wid, field, text, by, at))
                else:
                    db.execute("DELETE FROM assignment WHERE wid = ? AND field = ?", (wid, field))
                db.execute("INSERT INTO history (wid, field, before, after, by, at) VALUES (?, ?, ?, ?, ?, ?)",
                           (wid, field, before, text, by, at))
        return self.assignments([wid]).get(wid, {})

    def history(self, wid: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._tx() as db:
            rows = db.execute("SELECT * FROM history WHERE wid = ? ORDER BY id DESC LIMIT ?", (wid, limit)).fetchall()
        return [dict(r) for r in rows]

    # ---- notebook <-> Fabric item links

    def record_link(self, notebook: str, workspace_id: str, item_id: str, workspace_name: str = "",
                    how: str = "recorded", by: str = "") -> None:
        """Remember which Fabric item a notebook is. A hand-made link is kept over a recorded one
        only until the app publishes the notebook somewhere again."""
        if not (notebook and workspace_id and item_id):
            return
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO link VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (notebook, workspace_id, workspace_name, item_id, how, by, iso(now())))

    def unlink(self, notebook: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM link WHERE notebook = ?", (notebook,))

    def rename_link(self, old: str, new: str) -> None:
        with self._tx() as db:
            db.execute("UPDATE link SET notebook = ? WHERE notebook = ?", (new, old))

    def links(self) -> dict[str, dict[str, Any]]:
        with self._tx() as db:
            return {r["notebook"]: dict(r) for r in db.execute("SELECT * FROM link").fetchall()}

    # ---- actions

    def start_action(self, wid: str, action: str, env: str = "", target: dict[str, Any] | None = None,
                     identity: str = "", by: str = "", detail: str = "") -> dict[str, Any]:
        row = {"id": uuid.uuid4().hex[:12], "wid": wid, "action": action, "env": env,
               "target": json.dumps(target or {}), "identity": identity, "by": by, "at": iso(now()),
               "status": "submitted", "detail": detail, "ref": "{}", "ended_at": None}
        with self._tx() as db:
            db.execute("INSERT INTO action VALUES (:id, :wid, :action, :env, :target, :identity, :by, :at, :status, "
                       ":detail, :ref, :ended_at)", row)
            db.execute("DELETE FROM action WHERE id IN (SELECT id FROM action ORDER BY at DESC LIMIT -1 OFFSET ?)",
                       (ACTION_KEEP,))
        return self._action_out(row)

    def update_action(self, action_id: str, status: str | None = None, detail: str | None = None,
                      ref: dict[str, Any] | None = None) -> dict[str, Any]:
        """Move an action on. An ended action never changes again."""
        with self._tx() as db:
            row = db.execute("SELECT * FROM action WHERE id = ?", (action_id,)).fetchone()
            if row is None:
                raise KeyError(f"no action {action_id}")
            row = dict(row)
            if row["status"] in _ENDED:
                return self._action_out(row)
            if status:
                row["status"] = status
                if status in _ENDED:
                    row["ended_at"] = iso(now())
            if detail is not None:
                row["detail"] = detail
            if ref is not None:
                row["ref"] = json.dumps(json.loads(row["ref"] or "{}") | ref)
            db.execute("UPDATE action SET status = ?, detail = ?, ref = ?, ended_at = ? WHERE id = ?",
                       (row["status"], row["detail"], row["ref"], row["ended_at"], action_id))
        return self._action_out(row)

    def actions(self, wid: str | None = None, limit: int = 50, open_only: bool = False) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM action", []
        where = []
        if wid:
            where.append("wid = ?")
            params.append(wid)
        if open_only:
            where.append("status = 'submitted'")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY at DESC LIMIT ?"
        params.append(limit)
        with self._tx() as db:
            return [self._action_out(dict(r)) for r in db.execute(sql, params).fetchall()]

    @staticmethod
    def _action_out(row: dict[str, Any]) -> dict[str, Any]:
        return dict(row, target=json.loads(row.get("target") or "{}"), ref=json.loads(row.get("ref") or "{}"))

    # ---- collection cache

    def cache_put(self, source: str, payload: Any, state: str = "live", detail: str = "",
                  collected_at: str | None = None) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?, ?)",
                       (source, collected_at or iso(now()), state, detail, json.dumps(payload, default=str)))

    def cache_get(self, source: str) -> dict[str, Any] | None:
        with self._tx() as db:
            r = db.execute("SELECT * FROM cache WHERE source = ?", (source,)).fetchone()
        if r is None:
            return None
        return {"collected_at": r["collected_at"], "state": r["state"], "detail": r["detail"],
                "payload": json.loads(r["payload"])}
