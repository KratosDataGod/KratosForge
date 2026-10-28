"""Platform notifications in the app: governance's request and shortcut events to Teams.

Governance calls ``platform_notify`` with one event per change (workloads/platform_notify.py has
the shape); this file keeps the routes in the app's settings and the outbox and dedupe ledger in
the app's folder. Routes hold Teams webhook addresses, which work like passwords: they are
never returned to the UI, recorded in the audit log, or written to a backup, and settings.json
holds them encrypted for this Windows account (sealed_settings.py).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from . import sealed_settings
from .dataops_service import post_teams
from .redact import secrets
from .workloads.notify import Outbox, transaction
from .workloads.platform_notify import OPENS, VIEWS, Ledger, PlatformRouting, recipients, send, validate

METHODS = {"platform_notify", "platform_notify_settings", "platform_notify_save", "platform_notify_open"}
SETTING = "platform_notify"
_log = logging.getLogger(__name__)
# What the bell shows for each notice still open, beside the ledger in platform_outbox.sqlite.
_NOTICE = ("CREATE TABLE IF NOT EXISTS notice (key TEXT PRIMARY KEY, kind TEXT, title TEXT, whom TEXT, "
           "domain TEXT, env TEXT, detail TEXT, open TEXT)")


class PlatformNotifyMixin:
    root: Any

    def _pn_raw(self) -> dict[str, Any]:
        raw = self._settings().get(SETTING)  # type: ignore[attr-defined]
        return raw if isinstance(raw, dict) else {}

    def _pn_paths(self) -> tuple[Outbox, Ledger]:
        return Outbox(self.root / "platform_outbox.sqlite"), Ledger(self.root / "platform_outbox.sqlite")

    def platform_notify(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Post these events to the channels their routes name, once per dedupe key. Every event
        is checked first; one that doesn't fit the shape fails the call and nothing is posted."""
        if not isinstance(events, list):
            raise ValueError("events is a list")
        checked = []
        for i, event in enumerate(events):
            try:
                checked.append(validate(event))
            except ValueError as exc:
                raise ValueError(f"event {i + 1}: {exc}") from None
        routing = PlatformRouting.parse(self._pn_raw())
        if not routing.routes:
            return {"sent": 0, "queued": 0, "held": 0, "error": None, "skipped": "no platform Teams route set",
                    "events": [{"dedupe": e["dedupe"], "result": "skipped", "reason": "no platform Teams route set"}
                               for e in checked]}
        outbox, ledger = self._pn_paths()
        out = send(checked, routing, outbox, ledger, post_teams)
        self._pn_remember([e for e, r in zip(checked, out["events"]) if r["result"] != "skipped"])
        return out

    def _pn_remember(self, events: list[dict[str, Any]]) -> None:
        """What the bell shows for a notice still open: its title, who it waits on and where it opens.
        A request's detail isn't kept, as it isn't posted."""
        with transaction(self.root / "platform_outbox.sqlite") as db:
            db.execute(_NOTICE)
            for e in events:
                if e["kind"] in OPENS:
                    db.execute("INSERT OR REPLACE INTO notice VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (e["dedupe"], e["kind"], e["title"], recipients(e["to"]), e["domain"], e["env"],
                                e["detail"] if e["kind"].startswith("shortcut.") else "", json.dumps(e["open"])))

    def _governance_notify(self, build: Callable[[], list[dict[str, Any]]]) -> str | None:
        """Build and send governance's events (governance_notices.py) after the change they report
        is made. A notice never undoes or fails that change, building it included: a problem is
        logged, without any webhook address, and returned as one line; messages Teams didn't take
        wait in the outbox."""
        try:
            events = build()
            if events:
                self.platform_notify(events)
        except Exception as exc:  # noqa: BLE001 - the change is already made; say the notice wasn't sent
            problem = secrets(f"The Teams notice wasn't sent: {exc}")
            _log.warning(problem)
            return problem
        return None

    def platform_notify_settings(self) -> dict[str, Any]:
        """The routes without their webhook addresses: whether one is set, and its host. ``unreadable``:
        one is saved but this Windows account can't decrypt it (sealed_settings.py), so enter it again."""
        raw = self._pn_raw()
        lost = (self._settings().get(sealed_settings.UNREADABLE) or {}).get(SETTING) or {}  # type: ignore[attr-defined]
        routes = [{"name": r.get("name", ""), "kinds": r.get("kinds") or [], "domains": r.get("domains") or [],
                   "url_set": bool(r.get("url")), "host": urlparse(r.get("url") or "").hostname or ""}
                  | ({"unreadable": True} if str(r.get("name", "")) in lost else {})
                  for r in raw.get("routes") or []]
        outbox, _ = self._pn_paths()
        return {"routes": routes, "quiet": raw.get("quiet") or "", "quiet_exempt": raw.get("quiet_exempt") or [],
                "waiting": len(outbox.pending())}

    def platform_notify_save(self, routes: list[dict[str, Any]], quiet: str = "",
                             quiet_exempt: list[str] | None = None, test: str = "") -> dict[str, Any]:
        """Replace the routes. A route given without ``url`` keeps the address it had under its
        name. With ``test`` (a route name), a test message goes to that route before saving."""
        old = {r.get("name"): r.get("url", "") for r in self._pn_raw().get("routes") or []}
        merged = [dict(r, url=r["url"] if r.get("url") is not None else old.get(r.get("name"), "")) for r in routes or []]
        raw = {"routes": merged, "quiet": quiet, "quiet_exempt": quiet_exempt or []}
        routing = PlatformRouting.parse(raw)  # raises on a bad route before anything is saved
        if test:
            route = routing.by_name(test)
            if route is None or not route.url:
                raise ValueError(f"route {test} has no Teams webhook to test")
            post_teams(route.url, "KratosForge platform notices are on",
                       ["Requests waiting on someone and broken shortcuts will be posted here, once each."])
        self._save_settings(**{SETTING: raw})  # type: ignore[attr-defined]
        return self.platform_notify_settings()

    def platform_notify_open(self) -> dict[str, Any]:
        """Requests still waiting and shortcuts still broken, as far as this PC's notices know,
        newest first, plus the notices Teams hasn't taken yet and the last reason why."""
        outbox, ledger = self._pn_paths()
        with transaction(self.root / "platform_outbox.sqlite") as db:
            db.execute(_NOTICE)
            known = {r[0]: r for r in db.execute("SELECT * FROM notice").fetchall()}
        rows = []
        for k in reversed(ledger.open_keys()):
            n = known.get(k["key"])
            rows.append({"key": k["key"], "kind": k["kind"],
                         "at": dt.datetime.fromtimestamp(k["at"], dt.timezone.utc).isoformat(),
                         "title": n[2] if n else "", "whom": n[3] if n else "", "domain": n[4] if n else "",
                         "env": n[5] if n else "", "detail": n[6] if n else "",
                         "open": _target(k["kind"], n[7] if n else None)})
        pending = outbox.pending()
        failed = [p for p in pending if p["attempts"]]
        return {"open": rows, "waiting": len(pending), "held": len(pending) - len(failed),
                "error": failed[-1]["error"] if failed else "",
                "routes": bool(self._pn_raw().get("routes"))}


def _target(kind: str, stored: str | None) -> dict[str, str]:
    """Where a notice opens: what was recorded, or its kind's page when that's missing or unreadable,
    so one damaged row doesn't hide the rest."""
    try:
        target = json.loads(stored) if stored else None
    except ValueError:
        target = None
    if isinstance(target, dict) and target.get("view") in VIEWS and target.get("tab") in VIEWS[target["view"]]:
        return target
    return {"view": "platform", "tab": "requests"} if kind.startswith("request.") else {"view": "shortcuts", "tab": "health"}
