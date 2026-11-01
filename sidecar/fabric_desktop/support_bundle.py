"""A support bundle (FD-138): what someone helping you needs, with nothing they shouldn't see.

``fabric-desktop support-bundle`` writes ``fabric-desktop-support-<time>.zip`` holding:

    about.json      app and engine build, Python, operating system, where each setting comes from,
                    and the policy file's state (the setting names it sets, never their values)
    sidecar.log     the last lines of the engine's log
    agent.log       the last lines of the headless agent's log, on a server with an agent
    CONTENTS.txt    what is in the zip and what was taken out

Every file passes through :func:`redact.for_support` (no tokens, keys, passwords, email
addresses, the tenant's onmicrosoft.com and SharePoint names, the organization on OneDrive folders, user names in paths or ids). No table data, notebook contents or token cache is read.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__, app_settings
from .redact import for_support, secret_key

LOG_LINES = 2000


def _data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "FabricDesktop"


def _tail(path: Path, lines: int = LOG_LINES) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return None


def about() -> dict[str, Any]:
    """Versions and settings sources; values that identify an organization are summarized."""
    settings = app_settings.load()
    summary = {}
    for key, entry in settings.items():
        value = entry["value"]
        if key == "client_id":
            value = "Microsoft's Fabric sign-in" if value == app_settings.MICROSOFT_CLIENT_ID else "own app registration"
        elif key == "platform":
            value = sorted(value) if isinstance(value, dict) else []
        elif key in ("support_url", "dbconnect_index_url") or secret_key(key):  # a connection string is a secret whatever it looks like
            value = "set" if value else ""  # an internal help desk or package mirror names the organization by its host
        elif key == "sql_temp_dir" and isinstance(value, str) and value[:2] in ("\\\\", "//"):
            value = "a network folder"  # a share's server name is the organization's
        summary[key] = {"value": value, "source": entry["source"]}
    engine = app_settings._read_engine_info()
    return {
        "engine_version": __version__,
        "engine_build": engine.get("commit", ""),
        "python": sys.version.split()[0],
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "policy_file": app_settings.policy_path().exists(),
        "policy": policy_state(settings),
        "settings": summary,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def policy_state(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """The policy file's state for support, without a single value from it: whether it applies (or
    the last copy that could be read stands in), why not, the setting names it sets, and which
    settings take their value from it now. Only names of real settings are listed; others are counted."""
    data, reason, from_copy = app_settings._policy_file()
    settings = settings if settings is not None else app_settings.load()
    if not app_settings.policy_path().exists():
        state = "none"
    else:
        state = "last_known_good" if from_copy else "unreadable" if reason else "applied"
    known = [k for k in data if k in app_settings.DEFAULTS]  # an unknown key is free text IT typed: only counted
    return {"state": state, "problem": reason or "", "sets": sorted(known), "unknown_keys": len(data) - len(known),
            "from_policy": sorted(k for k, entry in settings.items() if entry.get("source") == "policy")}


def _files(data_dir: Path, agent_home: Path | None) -> dict[str, str]:
    """Each file of the bundle by name, already redacted: what create() writes and preview() shows."""
    files: dict[str, str] = {"about.json": json.dumps(about(), indent=2)}
    logs = {"sidecar.log": data_dir / "sidecar.log"}
    if agent_home:
        logs["agent.log"] = agent_home / "logs" / "agent.log"
    missing = []
    for name, source in logs.items():
        text = _tail(source)
        if text is None:
            missing.append(name)
        else:
            files[name] = text
    contents = ["KratosForge support bundle", "",
                *[f"{name}" for name in files], *[f"{name} (not found on this PC)" for name in missing], "",
                "Taken out of every file: tokens, keys, passwords, email addresses (a guest's too), the tenant's",
                "onmicrosoft.com and SharePoint names, the organization's name on OneDrive folders, user names in paths, and tenant, workspace and item ids.",
                "No table data, notebook contents or sign-in cache is included.",
                "Settings that hold a secret, such as a connection string, or an internal address show only whether they are set.",
                "The policy file shows only the setting names it sets, never their values; other keys are counted.",
                f"Logs are the last {LOG_LINES} lines."]
    files["CONTENTS.txt"] = "\n".join(contents) + "\n"
    return {name: for_support(text) for name, text in files.items()}


def preview(data_dir: Path | None = None, agent_home: Path | None = None) -> dict[str, Any]:
    """What a bundle made now would hold, without writing it: each file's name and size, and the
    CONTENTS.txt text that says what was taken out. Settings shows this before saving (PB-062)."""
    files = _files(data_dir or _data_dir(), agent_home)
    return {"files": [{"name": name, "bytes": len(text.encode("utf-8"))} for name, text in files.items()],
            "contents": files["CONTENTS.txt"]}


def create(out_dir: Path, data_dir: Path | None = None, agent_home: Path | None = None) -> Path:
    files = _files(data_dir or _data_dir(), agent_home)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"fabric-desktop-support-{stamp}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return path
