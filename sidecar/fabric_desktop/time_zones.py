"""Every Windows time zone, for Fabric schedules (PB-048). Fabric's localTimeZoneId takes the same ids
Windows keeps under HKLM\\...\\Time Zones, so the list is read from this PC's registry, named the way
Windows names them, sorted by UTC offset like the Windows clock settings. Off Windows the list is empty
and the app keeps its short list."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable, Iterable
from typing import Any

_log = logging.getLogger(__name__)

ZONES_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Time Zones"
CURRENT_KEY = r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation"
_OFFSET = re.compile(r"^\(UTC(?:([+-])(\d{2}):(\d{2}))?\)")


def _offset_minutes(display: str) -> int:
    m = _OFFSET.match(display)
    if not m or not m.group(1):
        return 0
    minutes = int(m.group(2)) * 60 + int(m.group(3))
    return -minutes if m.group(1) == "-" else minutes


def _registry() -> tuple[Iterable[tuple[str, str]], str | None]:
    """(zone id, display name) pairs and this PC's zone id, from the registry."""
    if sys.platform != "win32":  # winreg is Windows-only; this also tells the type checker so
        raise OSError("The Windows registry is only on Windows.")
    import winreg

    zones = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ZONES_KEY) as root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            zone_id = winreg.EnumKey(root, i)
            try:
                with winreg.OpenKey(root, zone_id) as key:
                    zones.append((zone_id, winreg.QueryValueEx(key, "Display")[0]))
            except OSError:
                _log.debug("time zone %s has no display name; left out", zone_id)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CURRENT_KEY) as key:
            here = winreg.QueryValueEx(key, "TimeZoneKeyName")[0]
    except OSError:
        here = None
    return zones, here


def time_zones(read: Callable[[], tuple[Iterable[tuple[str, str]], str | None]] | None = None) -> dict[str, Any]:
    """{"zones": [{"id", "name"}...], "here": this PC's zone id or None}. Empty off Windows, or when the
    registry can't be read (the app then keeps its short list)."""
    if read is None:
        if sys.platform != "win32":
            return {"zones": [], "here": None}
        read = _registry
    try:
        pairs, here = read()
    except OSError as e:
        _log.warning("could not read the time zones from the registry: %s", e)
        return {"zones": [], "here": None}
    zones = sorted(({"id": str(i), "name": str(n)} for i, n in pairs if i and n),
                   key=lambda z: (_offset_minutes(z["name"]), z["name"]))
    ids = {z["id"] for z in zones}
    return {"zones": zones, "here": here if here in ids else None}
