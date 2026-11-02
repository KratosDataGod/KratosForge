"""A support bundle (FD-138): what someone helping you needs, with nothing they shouldn't see.

``fabric-desktop support-bundle`` writes ``fabric-desktop-support-<time>.zip`` holding:

    about.json      app and engine build, Python, operating system, where each setting comes from,
                    and the policy file's state (the setting names it sets, never their values)
    sidecar.log     the last lines of the engine's log
    sidecar.prev.log  the last lines of the run before, when there was one (a crash's log survives the restart)
    agent.log       the last lines of the headless agent's log, on a server with an agent
    CONTENTS.txt    what is in the zip and what was taken out

Every file passes through :func:`redact.for_support` (no tokens, keys, passwords, email
addresses, the tenant's onmicrosoft.com and SharePoint names, the organization on OneDrive folders, user names in paths or ids). No table data, notebook contents or token cache is read.
"""

from __future__ import annotations

import json
import os
import platform
import re
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


TAIL_BYTES = 4 * 1024 * 1024  # LOG_LINES of even long lines; a log of any size costs no more to read


def _tail(path: Path, lines: int = LOG_LINES, max_bytes: int = TAIL_BYTES) -> str | None:
    """The last ``lines`` lines, reading only the file's last ``max_bytes``."""
    try:
        with path.open("rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return None
    if size > max_bytes:
        data = data[data.find(b"\n") + 1:] if b"\n" in data else b""  # drop the line cut in half
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")  # as text mode reads it
    return "".join(text.splitlines(keepends=True)[-lines:])


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
    previous = _tail(data_dir / "sidecar.prev.log")  # only there once the app has restarted the engine
    if previous is not None:
        files["sidecar.prev.log"] = previous
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
    expected = ["sidecar.log", *(["agent.log"] if agent_home else [])]
    return {"files": [{"name": name, "bytes": len(text.encode("utf-8"))} for name, text in files.items()],
            "missing": [name for name in expected if name not in files], "contents": files["CONTENTS.txt"]}


def create(out_dir: Path, data_dir: Path | None = None, agent_home: Path | None = None) -> Path:
    files = _files(data_dir or _data_dir(), agent_home)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    for n in range(1, 100):  # a new file every time: never replaces one, even within the same second
        path = out_dir / f"fabric-desktop-support-{stamp}{'' if n == 1 else f'-{n}'}.zip"
        try:
            handle = path.open("xb")
        except FileExistsError:
            continue
        break
    else:
        raise FileExistsError(f"There are already 99 support bundles from this second in {out_dir}")
    try:
        with handle, zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as z:
            for name, text in files.items():
                z.writestr(name, text)
    except BaseException:
        path.unlink(missing_ok=True)  # never leave half a zip behind
        raise
    return path


NAME = re.compile(r"fabric-desktop-support-\d{8}-\d{6}(?:-\d{1,2})?\.zip")
