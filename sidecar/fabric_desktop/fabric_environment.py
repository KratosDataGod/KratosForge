"""Fabric Environment items: what one has published, and how it differs from a notebook's packages here.

Code that works on this PC can fail in Fabric when a package is missing there or at another
version. ``read`` gets an Environment item's published libraries and Spark runtime with two
read-only calls (no Spark session starts, so no capacity is used for a job); ``drift`` compares
them with what the notebook finds on this PC: its notebook environment (environments.py) on top of
the engine's own packages.

A library is:

* ``missing_here``: published to the Environment item, not installed on this PC
* ``different``: on both, at different versions
* ``local_only``: in the notebook's environment here, not published to the item (Fabric's runtime
  may still ship it, which the item's libraries don't say)

An item the person can't read (no access, deleted, a typo in its id) gives ``couldnt_read`` with
the reason, never an error, so a check before a run never stops the run.
"""

from __future__ import annotations

import importlib.metadata
import re
import sys
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from .redact import describe

# The Python each Fabric Spark runtime runs (Microsoft's "Apache Spark runtimes in Fabric" pages).
RUNTIME_PYTHON = {"1.1": "3.10", "1.2": "3.10", "1.3": "3.11"}
_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_EXTRAS = re.compile(r"\[[^\]]*\]\s*$")  # requests[socks] is the requests package
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def read(fabric: Any, workspace_id: str, environment_id: str) -> dict[str, Any]:
    """The item's published libraries and runtime: {"status": "read", "libraries", "custom", "runtime"},
    or {"status": "couldnt_read", "reason"}."""
    if not _ID.fullmatch(str(workspace_id or "")) or not _ID.fullmatch(str(environment_id or "")):
        return {"status": "couldnt_read", "reason": "The workspace and the Environment item are each named by their id "
                "(a GUID, from the item's address in Fabric)."}
    try:
        libraries = fabric.environment_libraries(workspace_id, environment_id)
    except Exception as exc:  # noqa: BLE001 - a check before a run reports, never stops the run
        return {"status": "couldnt_read", "reason": _reason(exc)}
    try:
        runtime = (fabric.environment_spark_compute(workspace_id, environment_id) or {}).get("runtimeVersion")
    except Exception as exc:  # noqa: BLE001 - the libraries are still worth comparing
        runtime, why = None, _reason(exc)
    else:
        why = None
    external, custom = [], []
    for lib in libraries:
        if not isinstance(lib, dict):
            continue
        name = str(lib.get("name") or "")
        if lib.get("libraryType") == "External":
            name = _EXTRAS.sub("", name).strip()
            external.append({"name": name, "version": str(lib.get("version") or "") or None})
        elif name:
            custom.append(_custom(name))
    out = {"status": "read", "libraries": external, "custom": custom, "runtime": runtime,
           "python": RUNTIME_PYTHON.get(str(runtime)) if runtime else None}
    if why:
        out["runtime_problem"] = why
    return out


def _reason(exc: Exception) -> str:
    status = getattr(exc, "status", None)
    if status in (401, 403):
        return "You can't read this Environment item: ask its workspace admin for at least Viewer access."
    if status == 404:
        return "There's no Environment item with that id in this workspace, or it was deleted."
    return f"Couldn't read the Environment item: {describe(exc)}"


def _custom(file: str) -> dict[str, Any]:
    """A library uploaded as a file. A wheel's name and version are in its file name; other files
    (.py, .jar, .tar.gz for R) aren't compared."""
    try:
        name, version, _, _ = parse_wheel_filename(file)
    except InvalidWheelFilename:
        return {"file": file, "name": None, "version": None}
    return {"file": file, "name": str(name), "version": str(version)}


def on_this_pc(environment: list[dict[str, str]] | None = None) -> dict[str, dict[str, str]]:
    """{normalized name: {"name", "version", "from"}} for what a notebook imports here: the engine's
    packages, and over them its notebook environment's (environments.Environments.installed).
    This PC's Python is the engine's, which notebooks run in here."""
    found: dict[str, dict[str, str]] = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get("Name")
        if name:
            found[canonicalize_name(name)] = {"name": name, "version": dist.version, "from": "engine"}
    for pkg in environment or []:
        found[canonicalize_name(pkg["name"])] = {"name": pkg["name"], "version": pkg["version"], "from": "environment"}
    return found


def drift(item: dict[str, Any], here: dict[str, dict[str, str]], python: str | None = None) -> dict[str, Any]:
    """How the notebook's packages here differ from ``item`` (what ``read`` returned)."""
    if item.get("status") != "read":
        return {"status": "couldnt_read", "reason": item.get("reason"), "drifted": None}
    python = python or f"{sys.version_info.major}.{sys.version_info.minor}"
    published = {canonicalize_name(lib["name"]): lib for lib in item["libraries"] if lib["name"]}
    published.update({canonicalize_name(c["name"]): c for c in item["custom"] if c["name"]})
    missing, different, same = [], [], 0
    for key, lib in sorted(published.items()):
        mine = here.get(key)
        if mine is None:
            missing.append({"name": lib["name"], "fabric": lib["version"]})
        elif lib["version"] and not _same_version(lib["version"], mine["version"]):
            different.append({"name": lib["name"], "fabric": lib["version"], "here": mine["version"], "from": mine["from"]})
        else:
            same += 1
    local_only = [{"name": p["name"], "here": p["version"]} for key, p in sorted(here.items())
                  if p["from"] == "environment" and key not in published]
    python_differs = bool(item.get("python")) and item["python"] != python
    out = {"status": "read", "runtime": item.get("runtime"),
           "python": {"fabric": item.get("python"), "here": python, "differs": python_differs,
                      "unknown": bool(item.get("runtime")) and not item.get("python")},
           "missing_here": missing, "different": different, "local_only": local_only, "same": same,
           "not_compared": [c["file"] for c in item["custom"] if not c["name"]],
           "drifted": bool(missing or different or local_only or python_differs)}
    if item.get("runtime_problem"):
        out["runtime_problem"] = item["runtime_problem"]
    return out


def _same_version(fabric: str, here: str) -> bool:
    """Whether ``here`` is what Fabric has: the same version, or one a specifier like ``>=1.4`` allows."""
    if fabric.strip()[:1] in "<>=!~":
        try:
            return SpecifierSet(fabric).contains(here, prereleases=True)
        except (InvalidSpecifier, InvalidVersion):
            return fabric.strip() == here.strip()
    return _version(fabric) == _version(here)


def _version(version: str) -> Version | str:
    """1.0 and 1.0.0 are the same version; one pip can't read is compared as written."""
    try:
        return Version(version)
    except InvalidVersion:
        return version.strip()


def _plain(text: Any) -> str:
    """Text from Fabric with control characters taken out, so a name can't rewrite the terminal or the log."""
    return _CONTROL.sub("", str(text))


def summary(result: dict[str, Any]) -> str:
    """The drift in a few lines, for the command line and a run's log."""
    if result["status"] != "read":
        return f"Couldn't compare with the Environment item: {_plain(result['reason'])}"
    lines = []
    py = result["python"]
    if py["differs"]:
        lines.append(f"Python {py['fabric']} in Fabric (runtime {_plain(result['runtime'])}), {py['here']} here.")
    elif py.get("unknown"):
        lines.append(f"Python not compared: the app doesn't know which Python runtime {_plain(result['runtime'])} runs.")
    lines += [f"Missing here: {_plain(d['name'])}" + (f"=={_plain(d['fabric'])}" if d["fabric"] else "")
              for d in result["missing_here"]]
    lines += [f"Different version: {_plain(d['name'])} {_plain(d['fabric'])} in Fabric, {_plain(d['here'])} here"
              for d in result["different"]]
    lines += [f"Only here: {_plain(d['name'])} {_plain(d['here'])} (in this notebook's environment, not published to the item)"
              for d in result["local_only"]]
    if result.get("runtime_problem"):
        lines.append(f"Runtime not read: {_plain(result['runtime_problem'])}")
    if not result["drifted"]:
        lines.insert(0, f"No drift: {result['same']} published librar{'y' if result['same'] == 1 else 'ies'} "
                        "match this PC.")
    return "\n".join(lines)
