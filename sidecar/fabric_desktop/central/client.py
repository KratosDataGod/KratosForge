"""Talking to the platform: on this PC (SQLite) or in Fabric (UDF_Platform over SQL_Platform).

Both clients take the same calls, ``client.call("issue_keys", name="customer", business_keys=[...])``,
because both run the same code (core.py). The Fabric one invokes a User Data Function per call.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import requests

from . import core
from .. import http_session, redact
from ..retrying import send_with_retry

API = "https://api.fabric.microsoft.com/v1"
# Business keys per call. Each call's request stays well under User Data Functions' payload limit.
KEY_BATCH = 20000
REPAIR_TO_RETRY = ("Once a platform admin runs Platform > Settings > Finish or repair, the app sends such a "
                   "change again safely by itself.")


class LocalPlatform:
    """The platform as a SQLite file on this PC: for trying everything before Fabric, and for tests."""

    kind = "local"

    def __init__(self, path: Path | str, user: Callable[[], str] | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._user = user or (lambda: "")
        self._lock = threading.Lock()
        with self._db() as db:
            core.ensure_schema(db)
            # A platform on someone's own PC is theirs: they're its first admin.
            if not core.list_admins(db) and self._user():
                core.claim_admin(db, self._user())

    def _db(self) -> "_Open":
        return _Open(self.path)

    def call(self, op: str, **kwargs: Any) -> Any:
        if op not in core.OPERATIONS:
            raise ValueError(f"the platform has no operation {op!r}")
        fn = core.OPERATIONS[op]
        if "by" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
            kwargs["by"] = self._user()
        with self._lock, self._db() as db:
            return fn(db, **kwargs)


class _Open:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self) -> core.Db:
        self.conn = sqlite3.connect(self.path, timeout=30)
        return core.Db(self.conn, "sqlite")

    def __exit__(self, *exc: Any) -> None:
        self.conn.close()


class FabricPlatform:
    """UDF_Platform in a Platform workspace. Each call runs one function, as the signed-in person.

    Calls need Item.Execute.All (or UserDataFunction.Execute.All), the same permission that
    runs notebooks in Fabric. The function, not this app, decides who the change is recorded as."""

    kind = "fabric"

    def __init__(self, token: Callable[[], str], workspace_id: str, functions_id: str,
                 session: requests.Session | None = None, sleep: Callable[[float], None] = time.sleep):
        self._token, self.workspace_id, self.functions_id = token, workspace_id, functions_id
        self._http = session or http_session.session()
        self._sleep = sleep
        self._once: bool | None = None  # whether UDF_Platform has call_once: None until a change finds out

    def url(self, op: str) -> str:
        return f"{API}/workspaces/{self.workspace_id}/userDataFunctions/{self.functions_id}/functions/{op}/invoke"

    def call(self, op: str, **kwargs: Any) -> Any:
        """Run one operation, sending it again where that's safe (retrying.send_with_retry, waiting as
        Retry-After says). A read, Finish or repair and issuing keys are sent again after a 429, a busy
        platform (5xx), a timeout or a dropped connection. Any other change goes through call_once with
        a call id made once for it, so the platform makes it once however often it's sent; a platform
        without call_once yet gets the change itself, sent again only after a 429 (refused unread).

        The status is read before the body: a 5xx or 429 (a gateway's HTML page included) raises
        ``requests.HTTPError``, which the "can't be reached" fallbacks catch as a
        ``requests.RequestException``; any other answer that isn't a JSON object is a PlatformError."""
        if op not in core.OPERATIONS:
            raise ValueError(f"the platform has no operation {op!r}")
        kwargs.pop("by", None)
        if op in core.READ_OPERATIONS or op in core.REPEATABLE_OPERATIONS:
            return self._answer(op, self._send(op, kwargs, safe=True))
        if self._once is not False:
            payload = {"call_id": uuid.uuid4().hex, "operation": op, "args": kwargs}
            resp = self._send("call_once", payload, safe=True)
            if resp.status_code != 404:
                self._once = True
                return self._answer(op, resp)["result"]
            if self._once:  # it was there a moment ago: the 404 is the item's, not an older app's
                return self._answer(op, resp)
        try:
            out = self._answer(op, self._send(op, kwargs, safe=False))
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise requests.ConnectionError(
                f"UDF_Platform didn't answer, so this {op.replace('_', ' ')} may or may not have been made. "
                f"Check before trying again. {REPAIR_TO_RETRY}") from exc
        self._once = False  # an older UDF_Platform: until Finish or repair, changes go as they are
        return out

    def _send(self, op: str, payload: dict[str, Any], safe: bool) -> requests.Response:
        return send_with_retry(lambda: self._http.post(self.url(op), json=payload, timeout=300, headers={
            "Authorization": f"Bearer {self._token()}"}), "POST", sleep=self._sleep, attempts=3, safe=safe)

    def _answer(self, op: str, resp: requests.Response) -> Any:
        what = op.replace("_", " ")
        if resp.status_code in (401, 403):
            raise PermissionError("The platform refused this call. Ask your platform team for at least Viewer "
                                  "on the Platform workspace and Execute on UDF_Platform.")
        if resp.status_code == 404:
            raise LookupError("UDF_Platform wasn't found. Open Platform > Settings, then check the platform's workspace.")
        if resp.status_code == 429 or resp.status_code >= 500:
            reason = f" {resp.reason}" if getattr(resp, "reason", "") else ""
            raise requests.HTTPError(f"UDF_Platform couldn't be reached to {what}: Fabric answered "
                                     f"{resp.status_code}{reason}. Try again in a few minutes.", response=resp)
        body = _body(resp)
        if body is _NOT_JSON:
            raise core.PlatformError(f"The platform couldn't {what}: UDF_Platform answered {resp.status_code} "
                                     f"with something that isn't JSON: {_start(resp)}")
        if not isinstance(body, dict):
            raise core.PlatformError(f"The platform couldn't {what}: UDF_Platform answered {resp.status_code} "
                                     f"with JSON that isn't an object: {_start(resp)}")
        if resp.status_code >= 400 or body.get("status") == "Failed" or body.get("errors"):
            errors = body.get("errors") or [{"message": _start(resp)}]
            if not isinstance(errors, list):
                errors = [errors]
            message = "; ".join(str(e.get("message", e) if isinstance(e, dict) else e) for e in errors)
            raise core.PlatformError(f"The platform couldn't {what}: {redact.one_line(message)}")
        return body.get("output", body)


# What _body returns for a body that isn't JSON (None is JSON's null).
_NOT_JSON = object()


def _body(resp: requests.Response) -> Any:
    """A response's JSON, ``{}`` when it has no body, or _NOT_JSON."""
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:  # requests' JSONDecodeError is a ValueError; the caller raises PlatformError
        return _NOT_JSON


def _start(resp: requests.Response) -> str:
    """The start of a response's text on one line, for a message (a gateway's HTML page included)."""
    text = getattr(resp, "text", None)
    if not isinstance(text, str):
        text = bytes(resp.content or b"").decode("utf-8", "replace")
    return redact.one_line(text)


class PlatformKeys:
    """Surrogate keys from the platform instead of LH_Keys (what keys.py calls a key service)."""

    def __init__(self, client: LocalPlatform | FabricPlatform, env: str, batch: int = KEY_BATCH):
        self.client, self.env, self.batch = client, env, batch
        self._known: set[str] = set()

    def issue(self, name: str, business_keys: list[str]) -> tuple[dict[str, int], int]:
        """Every business key's surrogate key, issuing new ones. Returns (keys, how many were new)."""
        keys: dict[str, int] = {}
        inserted = 0
        unique = sorted({b for b in business_keys if b is not None})
        if name not in self._known:
            # A key table that isn't there yet: a platform admin's run creates it; anyone else is
            # told to request it.
            if not self.exists(name):
                try:
                    self.ensure(name)
                except core.PlatformError:
                    raise core.PlatformError(
                        f"keys.{name} isn't one of the platform's key tables yet. Ask a platform admin for it: "
                        "Platform > Set up > Business keys > Request a key table") from None
            self._known.add(name)
        for i in range(0, len(unique), self.batch):
            out = self.client.call("issue_keys", name=name, business_keys=unique[i:i + self.batch])
            keys.update({k: int(v) for k, v in out["keys"].items()})
            inserted += int(out.get("inserted", 0))
        return keys, inserted

    def ensure(self, name: str, business_key: str = "", description: str = "") -> bool:
        """Create a key table if it isn't there. Only platform admins may create one."""
        return bool(self.client.call("ensure_key_table", name=name, business_key=business_key, description=description))

    def exists(self, name: str) -> bool:
        return bool(self.client.call("key_stats", name=name).get("created"))

    def stats(self, name: str) -> dict[str, Any]:
        return self.client.call("key_stats", name=name)

    def lookup(self, name: str, value: str) -> dict[str, Any] | None:
        return self.client.call("lookup_key", name=name, value=value)

    def export(self, name: str) -> list[list[Any]]:
        rows, after = [], core.UNKNOWN_KEY - 1
        while True:
            page = self.client.call("export_keys", name=name, after=after)
            if not page:
                return rows
            rows += page
            after = page[-1][0]
