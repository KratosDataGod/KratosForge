"""App Studio projects on disk (AS-04): a plain folder that works with Git.

::

    Sales overview.KFApp/
      kfapp.json        format version, app id, name, created/modified, undo and redo stacks
      app.json          the canonical specification (spec.py): the source of truth
      history.jsonl     every change, one JSON line each: its operations and their inverse
      validation.json   the last validation result
      versions/0001.json  named snapshots (Version history), each a whole specification

No secret, no rows and no AI conversation is ever written here. Every save scans the specification and
refuses one that holds something that looks like a key, token or password; data connections are named by
workspace and item ids, and credentials stay in Windows Credential Manager. Files are swapped in whole
(jsonfile.write_text), so a crash leaves the old file or the new one.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .. import redact
from ..jsonfile import read_json, read_text, write_json, write_text
from . import patch as patches
from . import spec as specs
from .spec import SpecError

FORMAT = 1
SUFFIX = ".KFApp"
META, SPEC, HISTORY, VALIDATION, VERSIONS = "kfapp.json", "app.json", "history.jsonl", "validation.json", "versions"
MAX_SPEC_BYTES = 2 * 1024 * 1024
MAX_HISTORY_BYTES = 32 * 1024 * 1024
MAX_EXPORT_BYTES = 64 * 1024 * 1024
MAX_VERSIONS = 500
SOURCES = {"ai", "visual", "manual", "template", "import"}
_NAME_OK = re.compile(r"[^A-Za-z0-9 _\-]")
_VERSION_FILE = re.compile(r"^\d{4,6}\.json$")
# Model provider keys that redact.secrets doesn't know yet: Anthropic, OpenAI and Google AI Studio.
_MODEL_KEY = re.compile(r"\b(?:sk-ant-[A-Za-z0-9_\-]{20,}|sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,}|AIza[0-9A-Za-z_\-]{35})\b")


class ProjectError(ValueError):
    """The project folder can't be used as asked; the message says why in the person's words."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def folder_name(name: str) -> str:
    """A safe folder name for an application name: letters, digits, spaces, _ and -, 80 at most."""
    clean = " ".join(_NAME_OK.sub(" ", str(name or "")).split())[:80].strip()
    if not clean:
        raise ProjectError("Give the application a name with at least one letter or digit.")
    return clean + SUFFIX


# ---------------------------------------------------------------- secrets


def secret_problems(value: Any, where: str = "") -> list[str]:
    """Every place in ``value`` holding something that looks like a secret. Values are never repeated."""
    out: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            here = f"{where}.{str(k)[:40]}" if where else str(k)[:40]
            if redact.secret_key(k) and v not in (None, "", [], {}, False):
                out.append(f"{here} looks like it holds a secret. Projects never keep keys, tokens or passwords.")
                continue
            out += secret_problems(v, here)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out += secret_problems(v, f"{where}[{i}]")
    elif isinstance(value, str) and (redact.secrets(value) != value or _MODEL_KEY.search(value)):
        out.append(f"{where or 'value'} looks like a key, token or password. Projects never keep secrets.")
    return out


def _guard(spec: dict[str, Any]) -> dict[str, Any]:
    specs.check(spec)
    leaks = secret_problems(spec)
    if leaks:
        raise SpecError(leaks)
    if len(json.dumps(spec)) > MAX_SPEC_BYTES:
        raise SpecError([f"The specification is larger than {MAX_SPEC_BYTES // (1024 * 1024)} MB."])
    return spec


# ---------------------------------------------------------------- folder


def _dir(path: str | os.PathLike[str], must_exist: bool = True) -> Path:
    folder = Path(path)
    if not folder.name.endswith(SUFFIX):
        raise ProjectError(f"An App Studio project is a folder whose name ends in {SUFFIX}.")
    if folder.is_symlink():
        raise ProjectError("That project folder is a link to somewhere else, so it isn't used.")
    if must_exist and not (folder / META).is_file():
        raise ProjectError("That folder isn't an App Studio project (it has no kfapp.json).")
    return folder


def _file(folder: Path, name: str) -> Path:
    path = folder / name
    if path.is_symlink():
        raise ProjectError(f"{name} in the project is a link, so it isn't used.")
    return path


def _meta(folder: Path) -> dict[str, Any]:
    meta = read_json(_file(folder, META), None)
    if not isinstance(meta, dict) or meta.get("format") != FORMAT or not isinstance(meta.get("app_id"), str):
        raise ProjectError("kfapp.json isn't an App Studio project file this version can read.")
    meta.setdefault("undo", [])
    meta.setdefault("redo", [])
    return meta


def _write_spec(folder: Path, spec: dict[str, Any]) -> dict[str, Any]:
    write_text(_file(folder, SPEC), json.dumps(spec, indent=2, ensure_ascii=False) + "\n", newline="\n")
    result = {"at": _now(), "valid": True, "problems": []}
    write_json(_file(folder, VALIDATION), result)
    return result


def _history(folder: Path) -> list[dict[str, Any]]:
    path = _file(folder, HISTORY)
    if not path.exists():
        return []
    if path.stat().st_size > MAX_HISTORY_BYTES:
        raise ProjectError("The change history is too large to read; save a version and start a clone.")
    out = []
    for line in read_text(path).splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a torn last line from a crash: the entries before it stand
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _append(folder: Path, entry: dict[str, Any]) -> None:
    with open(_file(folder, HISTORY), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def create(parent: str | os.PathLike[str], name: str, spec: dict[str, Any] | None = None,
           target: str = "powerbi-report") -> Path:
    """A new project folder under ``parent``. Refuses a folder that's already there."""
    doc = spec if spec is not None else specs.new(name, target)
    _guard(doc)
    folder = _dir(Path(parent) / folder_name(name), must_exist=False)
    if folder.exists():
        raise ProjectError(f"{folder.name} already exists. Open it, or choose another name.")
    folder.mkdir(parents=True)
    now = _now()
    write_json(folder / META, {"format": FORMAT, "app_id": str(uuid.uuid4()), "name": doc["name"],
                               "created": now, "modified": now, "undo": [], "redo": []})
    _write_spec(folder, doc)
    (folder / VERSIONS).mkdir()
    return folder


def open_project(path: str | os.PathLike[str]) -> dict[str, Any]:
    """The project's metadata, specification and validation. A specification that no longer validates
    (edited by hand, or a newer format) is returned with its problems instead of being thrown away."""
    folder = _dir(path)
    meta = _meta(folder)
    spec = read_json(_file(folder, SPEC), None)
    problems = specs.validate(spec) + (secret_problems(spec) if isinstance(spec, dict) else [])
    return {"path": str(folder), "meta": meta, "spec": spec, "valid": not problems, "problems": problems,
            "can_undo": bool(meta["undo"]), "can_redo": bool(meta["redo"])}


def _current(folder: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    got = open_project(folder)
    if not got["valid"]:
        raise SpecError(got["problems"])
    return got["meta"], got["spec"]


def change(path: str | os.PathLike[str], ops: list[dict[str, Any]], note: str = "", source: str = "manual") -> dict[str, Any]:
    """Apply a change (patch operations) and save it with its inverse. Nothing is written when it fails."""
    folder = _dir(path)
    if source not in SOURCES:
        raise ProjectError(f"source must be one of: {', '.join(sorted(SOURCES))}.")
    meta, spec = _current(folder)
    done = patches.apply(spec, ops)
    _guard(done["spec"])
    seq = len(_history(folder)) + 1
    _append(folder, {"seq": seq, "at": _now(), "kind": "change", "source": source, "note": redact.one_line(note, 200),
                     "ops": ops, "inverse": done["inverse"], "changed": done["changed"]})
    _write_spec(folder, done["spec"])
    meta.update(undo=[*meta["undo"], seq], redo=[], modified=_now(), name=done["spec"]["name"])
    write_json(folder / META, meta)
    return {"spec": done["spec"], "changed": done["changed"], "seq": seq}


def _step(path: str | os.PathLike[str], back: bool) -> dict[str, Any]:
    folder = _dir(path)
    meta, spec = _current(folder)
    stack, other = ("undo", "redo") if back else ("redo", "undo")
    if not meta[stack]:
        raise ProjectError("There's nothing to undo." if back else "There's nothing to redo.")
    seq = meta[stack][-1]
    entry = next((e for e in _history(folder) if e.get("seq") == seq and e.get("kind") == "change"), None)
    if entry is None:
        raise ProjectError("That change is missing from history.jsonl, so it can't be undone or redone.")
    done = patches.apply(spec, entry["inverse"] if back else entry["ops"])
    _guard(done["spec"])
    _append(folder, {"seq": len(_history(folder)) + 1, "at": _now(), "kind": "undo" if back else "redo", "of": seq,
                     "changed": done["changed"]})
    _write_spec(folder, done["spec"])
    meta[stack] = meta[stack][:-1]
    meta[other] = [*meta[other], seq]
    meta.update(modified=_now(), name=done["spec"]["name"])
    write_json(folder / META, meta)
    return {"spec": done["spec"], "changed": done["changed"], "can_undo": bool(meta["undo"]), "can_redo": bool(meta["redo"])}


def undo(path: str | os.PathLike[str]) -> dict[str, Any]:
    return _step(path, back=True)


def redo(path: str | os.PathLike[str]) -> dict[str, Any]:
    return _step(path, back=False)


# ---------------------------------------------------------------- versions


def versions(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    folder = _dir(path)
    out = []
    vdir = _file(folder, VERSIONS)
    for f in sorted(vdir.glob("*.json")) if vdir.is_dir() else []:
        if _VERSION_FILE.fullmatch(f.name) and not f.is_symlink():
            doc = read_json(f, None)
            if isinstance(doc, dict):
                out.append({"version": doc.get("version"), "label": doc.get("label"), "at": doc.get("at")})
    return out


def save_version(path: str | os.PathLike[str], label: str = "") -> dict[str, Any]:
    """Snapshot the current specification as the next numbered version."""
    folder = _dir(path)
    _meta_, spec = _current(folder)
    have = versions(folder)
    if len(have) >= MAX_VERSIONS:
        raise ProjectError(f"This project already has {MAX_VERSIONS} versions.")
    n = max((v["version"] for v in have if isinstance(v.get("version"), int)), default=0) + 1
    entry = {"version": n, "label": redact.one_line(label, 120) or f"Version {n}", "at": _now(), "spec": spec}
    write_json(_file(folder, VERSIONS) / f"{n:04d}.json", entry)
    return {k: entry[k] for k in ("version", "label", "at")}


def restore_version(path: str | os.PathLike[str], version: int) -> dict[str, Any]:
    """Make version ``version`` the current specification. What was current is saved as a version first,
    so a restore can always be taken back."""
    folder = _dir(path)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ProjectError("Choose a version to restore.")
    doc = read_json(_file(folder, VERSIONS) / f"{version:04d}.json", None)
    if not isinstance(doc, dict) or doc.get("version") != version:
        raise ProjectError(f"Version {version} isn't in this project.")
    if not isinstance(doc.get("spec"), dict):
        raise ProjectError(f"Version {version} has no specification.")
    _guard(doc["spec"])
    kept = save_version(folder, f"Before restoring version {version}")
    meta = _meta(folder)
    _append(folder, {"seq": len(_history(folder)) + 1, "at": _now(), "kind": "restore", "version": version,
                     "kept_as": kept["version"]})
    _write_spec(folder, doc["spec"])
    meta.update(undo=[], redo=[], modified=_now(), name=doc["spec"]["name"])
    write_json(folder / META, meta)
    return {"spec": doc["spec"], "kept_as": kept["version"]}


# ---------------------------------------------------------------- save as, clone, export, import


def save_as(path: str | os.PathLike[str], parent: str | os.PathLike[str], name: str, clone: bool = False) -> Path:
    """Copy the project to a new folder named for ``name``. Save As keeps its history and versions under a new
    app id; Clone starts the copy with no history or versions."""
    folder = _dir(path)
    _meta_, spec = _current(folder)
    spec = {**spec, "name": " ".join(str(name).split())[:120] or spec["name"]}
    dest = create(parent, name, spec)
    if not clone:
        if _file(folder, HISTORY).exists():
            write_text(dest / HISTORY, read_text(_file(folder, HISTORY)), newline="\n")
        for f in sorted(_file(folder, VERSIONS).glob("*.json")):
            if _VERSION_FILE.fullmatch(f.name) and not f.is_symlink():
                write_text(dest / VERSIONS / f.name, read_text(f), newline="\n")
        new_meta = _meta(dest)
        old = _meta(folder)
        new_meta.update(undo=old["undo"], redo=old["redo"])
        write_json(dest / META, new_meta)
    return dest


def export(path: str | os.PathLike[str]) -> bytes:
    """The project as a .zip (its folder's files, nothing else), after the secret scan."""
    folder = _dir(path)
    _current(folder)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in (META, SPEC, HISTORY, VALIDATION):
            f = _file(folder, name)
            if f.is_file():
                z.writestr(name, f.read_bytes())
        for f in sorted(_file(folder, VERSIONS).glob("*.json")):
            if _VERSION_FILE.fullmatch(f.name) and not f.is_symlink():
                z.writestr(f"{VERSIONS}/{f.name}", f.read_bytes())
    return buf.getvalue()


def _member_ok(name: str) -> bool:
    parts = PurePosixPath(name).parts
    if name.startswith("/") or "\\" in name or ".." in parts:
        return False
    return name in (META, SPEC, HISTORY, VALIDATION) or (
        len(parts) == 2 and parts[0] == VERSIONS and bool(_VERSION_FILE.fullmatch(parts[1])))


def import_(data: bytes, parent: str | os.PathLike[str], name: str = "") -> Path:
    """Unpack an exported project into a new folder under ``parent`` (a new app id). Only the project's own
    files are taken; anything else in the zip, a path that leaves the folder, or an oversized file is refused."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ProjectError("That file isn't an exported App Studio project (.zip).") from None
    with z:
        infos = z.infolist()
        bad = [i.filename for i in infos if not i.is_dir() and not _member_ok(i.filename)]
        if bad:
            raise ProjectError(f"The zip has files that aren't part of a project: {', '.join(b[:60] for b in bad[:5])}.")
        if sum(i.file_size for i in infos) > MAX_EXPORT_BYTES:
            raise ProjectError("The zip is larger than an App Studio project can be.")
        files = {i.filename: z.read(i) for i in infos if not i.is_dir()}
    if SPEC not in files or META not in files:
        raise ProjectError("The zip has no app.json or kfapp.json.")
    try:
        spec = json.loads(files[SPEC].decode("utf-8"))
    except ValueError:
        raise ProjectError("app.json in the zip isn't valid JSON.") from None
    _guard(spec)
    dest = create(parent, name or spec["name"], spec)
    if HISTORY in files:
        write_text(dest / HISTORY, files[HISTORY].decode("utf-8", "replace"), newline="\n")
    for fname, body in files.items():
        if fname.startswith(f"{VERSIONS}/"):
            try:
                doc = json.loads(body.decode("utf-8"))
                _guard(doc["spec"])
            except (ValueError, KeyError, TypeError):
                continue  # a damaged or unsafe version is left behind; the current specification is what matters
            write_json(dest / VERSIONS / PurePosixPath(fname).name, doc)
    return dest
