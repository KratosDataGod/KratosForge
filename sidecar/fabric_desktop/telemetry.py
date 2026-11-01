"""Opt-in usage data (FD-137): how often each app action ran, failed and how long it took.

Off until the person turns it on (Settings, Usage data), and an organization can turn it off for
everyone (``"telemetry": false`` in policy.json). Nothing is collected while it is off, and turning
it off deletes what was waiting to be sent.

What is kept, per day, in ``<data>/telemetry/usage.json``:

    install_id   a random id made on this PC, not tied to the person or tenant; Settings makes a new one
    days         {"2026-10-17": {"features": {"run_sql": {"count", "errors", "seconds"}}, "errors": {"HTTPError": 1}}}
    last_sent    the last day sent

Only actions the audit trail records (audit.audited: the ones that change or run something) are
counted, by their method name, which is the app's own identifier. An error is counted by its kind
(``HTTPError``), never its message. No parameters, data, names, paths, accounts or tenants.

Once a day, at engine start, finished days go to Azure Monitor Application Insights as one custom
event each. Where to: the organization's own connection string (policy ``telemetry_connection_string``),
else the one this build ships with (``engine.json``, from the repository variable
TELEMETRY_CONNECTION_STRING). A build with neither has the switch but sends nothing.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import platform
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from . import app_settings, audit, jsonfile

KEEP_DAYS = 30  # unsent days older than this are dropped, so a PC that never reaches the endpoint doesn't pile up
# Only an exception class's own name (HTTPError, TimeoutError), so a message that starts "orders: ..." can't
# pass a table or workspace name through as a "kind".
_ERROR_KIND = re.compile(r"^([A-Z][A-Za-z0-9]{0,60}(?:Error|Exception|Timeout|Interrupt|Exit)):")


def destination() -> tuple[str, str]:
    """(connection string, whose it is: "organization" or "kratosbi"); ("", "") when there is none."""
    own = app_settings.value("telemetry_connection_string")
    if own:  # one that isn't a connection string sends nothing; Settings lists it under policy problems
        return (own, "organization") if valid_connection_string(own) else ("", "")
    built_in = os.environ.get("FABRIC_DESKTOP_TELEMETRY_CONNECTION_STRING") or \
        str(app_settings._read_engine_info().get("telemetry_connection_string") or "")
    return (built_in, "kratosbi") if valid_connection_string(built_in) else ("", "")


def parse_connection_string(text: str) -> dict[str, str]:
    """Application Insights' ``InstrumentationKey=...;IngestionEndpoint=https://...`` as a dict."""
    parts = dict(p.split("=", 1) for p in text.split(";") if "=" in p)
    return {k.strip().lower(): v.strip() for k, v in parts.items()}


def valid_connection_string(text: str) -> bool:
    parts = parse_connection_string(text)
    endpoint = parts.get("ingestionendpoint", "https://dc.services.visualstudio.com")
    return bool(parts.get("instrumentationkey")) and app_settings.valid_client_id(parts["instrumentationkey"]) \
        and endpoint.startswith("https://")


def error_kind(error: str) -> str:
    """``HTTPError`` from ``"HTTPError: 409 ..."``; "Error" when the message doesn't start with a kind."""
    match = _ERROR_KIND.match(error or "")
    return match.group(1) if match else "Error"


def _build() -> str:
    return str(app_settings._read_engine_info().get("commit") or "")[:12] or "dev"


class Telemetry:
    def __init__(self, folder: Path, today: Callable[[], dt.date] = dt.date.today):
        self.folder = Path(folder)
        self.path = self.folder / "usage.json"
        self._today = today
        self._lock = threading.Lock()

    # ---------- switches ----------

    def chosen(self) -> bool:
        """The person (or their organization) turned it on."""
        return bool(app_settings.value("telemetry"))

    def enabled(self) -> bool:
        """On, and this build or the organization has somewhere to send it."""
        return self.chosen() and bool(destination()[0])

    # ---------- the file ----------

    def _load(self) -> dict[str, Any]:
        try:
            data = jsonfile.read_json(self.path, {})
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        jsonfile.write_text(self.path, json.dumps(data, indent=1, sort_keys=True))

    def _install_id(self, data: dict[str, Any]) -> str:
        if not data.get("install_id"):
            data["install_id"] = str(uuid.uuid4())
        return data["install_id"]

    # ---------- counting ----------

    def record(self, method: str, ok: bool, seconds: float, error: str = "") -> None:
        """Counts one call of an audited action. Does nothing while usage data is off."""
        if not audit.audited(method) or not self.enabled():
            return
        day = self._today().isoformat()
        with self._lock:
            data = self._load()
            self._install_id(data)
            today = data.setdefault("days", {}).setdefault(day, {"features": {}, "errors": {}})
            f = today["features"].setdefault(method, {"count": 0, "errors": 0, "seconds": 0.0})
            f["count"] += 1
            f["seconds"] = round(f["seconds"] + max(0.0, float(seconds)), 1)
            if not ok:
                f["errors"] += 1
                kind = error_kind(error)
                today["errors"][kind] = today["errors"].get(kind, 0) + 1
            self._save(data)

    def discard(self) -> None:
        """Turned off: forget every day not yet sent (the install id stays, it isn't about anyone)."""
        with self._lock:
            data = self._load()
            if data.get("days"):
                data["days"] = {}
                self._save(data)

    def new_install_id(self) -> str:
        with self._lock:
            data = self._load()
            data["install_id"] = str(uuid.uuid4())
            self._save(data)
            return data["install_id"]

    # ---------- what is sent ----------

    def payload(self, day: str, counts: dict[str, Any], install_id: str) -> dict[str, Any]:
        """Exactly what goes out for one day: this, and nothing else."""
        return {
            "name": "daily_usage", "day": day, "install_id": install_id, "build": _build(),
            "channel": app_settings.value("channel"), "windows": platform.version() if os.name == "nt" else platform.platform(),
            "python": platform.python_version(),
            "features": dict(sorted(counts.get("features", {}).items())), "errors": dict(sorted(counts.get("errors", {}).items())),
        }

    def status(self) -> dict[str, Any]:
        """For Settings: on or off and why, where it goes, and the exact text of what's waiting."""
        conn, whose = destination()
        data = self._load()
        install_id = data.get("install_id") or ""
        days = data.get("days", {})
        return {
            "on": self.chosen(), "available": bool(conn), "destination": whose,
            "install_id": install_id, "last_sent": data.get("last_sent") or "", "today": self._today().isoformat(),
            "waiting": [self.payload(d, days[d], install_id) for d in sorted(days)],
        }

    def send_due(self, post: Callable[[str, list[dict[str, Any]]], None] | None = None) -> dict[str, Any]:
        """Sends each finished day not yet sent (today waits until it's over). Returns what was sent."""
        if not self.enabled():
            return {"sent": []}
        conn, _ = destination()
        today = self._today()
        cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
        with self._lock:
            data = self._load()
            days = data.get("days", {})
            for old in [d for d in days if d < cutoff]:
                del days[old]
            due = sorted(d for d in days if d < today.isoformat())
            if not due:
                if data:
                    self._save(data)  # keeps the drop of days past KEEP_DAYS
                return {"sent": []}
            install_id = self._install_id(data)
            payloads = [self.payload(d, days[d], install_id) for d in due]
        (post or _post)(conn, [envelope(conn, p) for p in payloads])
        with self._lock:
            data = self._load()
            for d in due:
                data.get("days", {}).pop(d, None)
            data["last_sent"] = due[-1]
            self._save(data)
        return {"sent": due}


def envelope(conn: str, payload: dict[str, Any]) -> dict[str, Any]:
    """One Application Insights custom event carrying ``payload`` as its properties (all strings)."""
    key = parse_connection_string(conn)["instrumentationkey"]
    props = {k: v if isinstance(v, str) else json.dumps(v, sort_keys=True) for k, v in payload.items() if k != "name"}
    return {"name": "Microsoft.ApplicationInsights.Event", "time": f"{payload['day']}T23:59:59Z", "iKey": key,
            "data": {"baseType": "EventData", "baseData": {"ver": 2, "name": payload["name"], "properties": props}}}


def _post(conn: str, envelopes: list[dict[str, Any]]) -> None:
    import requests

    endpoint = parse_connection_string(conn).get("ingestionendpoint", "https://dc.services.visualstudio.com").rstrip("/")
    resp = requests.post(f"{endpoint}/v2/track", json=envelopes, timeout=15)
    resp.raise_for_status()
