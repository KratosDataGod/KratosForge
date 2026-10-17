"""A support bundle (FD-138): what someone helping you needs, with nothing they shouldn't see.

``fabric-desktop support-bundle`` writes ``fabric-desktop-support-<time>.zip`` holding:

    about.json      app and engine build, Python, operating system, where each setting comes from
    sidecar.log     the last lines of the engine's log
    agent.log       the last lines of the headless agent's log, on a server with an agent
    CONTENTS.txt    what is in the zip and what was taken out

Every file passes through :func:`redact.for_support` (no tokens, keys, passwords, email
addresses, user names in paths or ids). No table data, notebook contents or token cache is read.
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
from .redact import for_support

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
        elif key == "support_url":
            value = "set" if value else ""
        summary[key] = {"value": value, "source": entry["source"]}
    engine = app_settings._read_engine_info()
    return {
        "engine_version": __version__,
        "engine_build": engine.get("commit", ""),
        "python": sys.version.split()[0],
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "policy_file": app_settings.policy_path().exists(),
        "settings": summary,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def create(out_dir: Path, data_dir: Path | None = None, agent_home: Path | None = None) -> Path:
    data_dir = data_dir or _data_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"fabric-desktop-support-{stamp}.zip"
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
    contents = ["Fabric Desktop by KratosBI support bundle", "",
                *[f"{name}" for name in files], *[f"{name} (not found on this PC)" for name in missing], "",
                "Taken out of every file: tokens, keys, passwords, email addresses, user names in paths,",
                "and tenant, workspace and item ids. No table data, notebook contents or sign-in cache is included.",
                f"Logs are the last {LOG_LINES} lines."]
    files["CONTENTS.txt"] = "\n".join(contents) + "\n"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, for_support(text))
    return path
