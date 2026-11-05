"""Paired VS Code windows (docs/vscode.md, "Run cells from VS Code").

A VS Code window asks to pair through the editor channel (editor_channel.py). The app shows the
request with a short code that VS Code shows too; the person pairs the window or denies it. A
paired window gets a token, which it sends with every call. Only a SHA-256 of the token is kept.

How long a pairing lasts is the person's choice:

    session   until KratosForge closes (kept in memory only, never written down)
    8h, 7d    that long, kept hashed in ``<data>/editor/pairings.json``

A request nobody answers is dropped after two minutes, and only a few can wait at once, so a
program can't pile requests up. Settings > Integrations lists the paired windows and ends them.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .jsonfile import read_json, write_json

KEEP = {"session": None, "8h": 8 * 3600, "7d": 7 * 86400}
REQUEST_SECONDS = 120
MAX_WAITING = 3
CODE_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
TOKEN_PREFIX = "kfp_"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _text(value: Any, limit: int) -> str:
    """Text a window sent about itself, for showing only: no control characters, cut to ``limit``."""
    text = value if isinstance(value, str) else ""
    return "".join(c for c in text if c.isprintable())[:limit]


def new_code() -> str:
    letters = "".join(secrets.choice(CODE_LETTERS) for _ in range(6))
    return f"{letters[:3]}-{letters[3:]}"


class Pairings:
    def __init__(self, folder: Path, clock: Callable[[], float] = time.time):
        self.path = Path(folder) / "pairings.json"
        self.clock = clock
        self._lock = threading.Lock()
        self._requests: dict[str, dict[str, Any]] = {}
        self._session: dict[str, dict[str, Any]] = {}  # by token hash; never written down
        self._used: dict[str, float] = {}  # pairing id -> last call, in memory

    # ---- requests from VS Code

    def request(self, window: Any, client: Any) -> dict[str, Any]:
        """A window asks to pair. Returns the request's id and the code both sides show."""
        with self._lock:
            self._drop_old()
            waiting = [r for r in self._requests.values() if r["state"] == "waiting"]
            if len(waiting) >= MAX_WAITING:
                raise PermissionError("Too many VS Code windows are waiting to pair. Answer them in KratosForge first.")
            rid = "pq_" + secrets.token_hex(8)
            self._requests[rid] = {"id": rid, "code": new_code(), "window": _text(window, 300) or "(no folder)",
                                   "client": _text(client, 80) or "An unnamed VS Code extension",
                                   "at": self.clock(), "state": "waiting", "token": None, "keep": None}
            return {"id": rid, "code": self._requests[rid]["code"], "expires_in": REQUEST_SECONDS}

    def collect(self, request_id: Any) -> dict[str, Any]:
        """What became of a request. The token is handed over once, then forgotten."""
        with self._lock:
            self._drop_old()
            req = self._requests.get(request_id) if isinstance(request_id, str) else None
            if req is None:
                return {"state": "expired"}
            if req["state"] == "paired":
                del self._requests[request_id]
                return {"state": "paired", "token": req["token"], "keep": req["keep"]}
            if req["state"] == "denied":
                del self._requests[request_id]
            return {"state": req["state"]}

    # ---- the person's answers (the app)

    def waiting(self) -> list[dict[str, Any]]:
        with self._lock:
            self._drop_old()
            return [{k: r[k] for k in ("id", "code", "window", "client", "at")}
                    for r in self._requests.values() if r["state"] == "waiting"]

    def decide(self, request_id: str, approve: bool, keep: str = "session") -> dict[str, Any]:
        if keep not in KEEP:
            raise ValueError(f"keep must be one of {', '.join(KEEP)}")
        with self._lock:
            self._drop_old()
            req = self._requests.get(request_id)
            if req is None or req["state"] != "waiting":
                raise LookupError("That pairing request has expired. Pick KratosForge in VS Code again.")
            if not approve:
                req["state"] = "denied"
                return {"paired": False}
            token = TOKEN_PREFIX + secrets.token_urlsafe(32)
            now = self.clock()
            record = {"id": "pw_" + secrets.token_hex(8), "hash": _hash(token), "window": req["window"],
                      "client": req["client"], "created": now, "keep": keep,
                      "expires": None if KEEP[keep] is None else now + KEEP[keep]}
            if KEEP[keep] is None:
                self._session[record["hash"]] = record
            else:
                saved = [r for r in self._saved() if not self._expired(r)]
                write_json(self.path, saved + [record])
            req.update(state="paired", token=token, keep=keep)
            return {"paired": True, "id": record["id"]}

    # ---- calls from a paired window

    def check(self, token: Any) -> dict[str, Any] | None:
        """The pairing a token belongs to, while it lasts; None for any other token."""
        if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX):
            return None
        digest = _hash(token)
        with self._lock:
            record = self._session.get(digest) or next((r for r in self._saved() if r.get("hash") == digest), None)
            if record is None or self._expired(record):
                return None
            self._used[record["id"]] = self.clock()
            return {k: record[k] for k in ("id", "window", "client", "keep", "expires")}

    # ---- Settings > Integrations

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._session.values()) + [r for r in self._saved() if not self._expired(r)]
            return [{k: r[k] for k in ("id", "window", "client", "created", "keep", "expires")}
                    | {"last_used": self._used.get(r["id"])} for r in rows]

    def end(self, pairing_id: str) -> bool:
        """Ends a pairing: its token stops working at once."""
        with self._lock:
            for digest, record in list(self._session.items()):
                if record["id"] == pairing_id:
                    del self._session[digest]
                    return True
            saved = self._saved()
            kept = [r for r in saved if r.get("id") != pairing_id]
            if len(kept) == len(saved):
                return False
            write_json(self.path, kept)
            return True

    def end_all(self) -> None:
        with self._lock:
            self._session.clear()
            self._requests.clear()
            if self.path.exists():
                write_json(self.path, [])

    # ----

    def _saved(self) -> list[dict[str, Any]]:
        rows = read_json(self.path, [])
        return [r for r in rows if isinstance(r, dict) and isinstance(r.get("hash"), str)] if isinstance(rows, list) else []

    def _expired(self, record: dict[str, Any]) -> bool:
        expires = record.get("expires")
        return expires is not None and (not isinstance(expires, (int, float)) or self.clock() >= expires)

    def _drop_old(self) -> None:
        now = self.clock()
        for rid, req in list(self._requests.items()):
            if now - req["at"] > REQUEST_SECONDS:
                del self._requests[rid]
