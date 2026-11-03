"""Databricks Asset Bundles: validate, plan and deploy with the Databricks CLI (Preview; tested against
a fake CLI, not a live workspace).

Everything runs the person's own Databricks CLI (databricks_auth.find_cli: PATH's absolute folders
only) in the bundle's folder, as the CLI profile the connection names, so the CLI signs in and
KratosForge never handles a token.

- ``validate`` and ``plan`` change nothing in Databricks: ``bundle validate`` checks the files, and
  ``bundle plan`` says what a deploy would create, update, delete or recreate.
- A bundle can make the CLI run commands on this PC: ``experimental.scripts`` hooks, artifact builds
  (a wheel builds even with no ``build`` line) and Python resource code. Which ones fire on which
  command depends on the CLI version (UNVERIFIED), so before any CLI call the bundle and every file it
  includes is read (yaml.safe_load, never run) and any such command is listed. The CLI then runs only
  with ``allow`` equal to the digest of that list: the person saw exactly these commands and said yes.
  An include that matches nothing or points out of the folder refuses.
- ``deploy`` plans again first and stops if the plan differs from the one the person saw (the
  workspace or the files changed since): they look at the new plan instead. A deploy that deletes or
  recreates anything, or goes to a production target, needs the target's name typed back. Only then
  is ``--auto-approve`` passed, which the CLI needs to delete without a terminal prompt.
- Deploying doesn't run a job or a pipeline; their next scheduled run bills as usual.

UNVERIFIED against a live CLI: the JSON shape of ``bundle plan -o json``. The parser accepts the
shapes below and says so plainly when it can't read one, rather than guessing.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

import yaml

from .. import redact
from .databricks_auth import find_cli
from .databricks_discovery import _text

TIMEOUT = 600                 # seconds for a deploy; validate and plan get a tenth of it
_TARGET = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PROFILE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
DESTRUCTIVE = {"delete", "recreate"}
ACTIONS = {"create": "Create", "update": "Update", "delete": "Delete", "recreate": "Recreate", "skip": "No change"}


class BundleError(ValueError):
    """The bundle can't be validated, planned or deployed as asked; nothing changed in Databricks."""


# ---------------------------------------------------------------- the bundle on this PC


def bundle_file(folder: str) -> Path:
    root = Path(folder or "")
    if not folder or not root.is_absolute() or not root.is_dir():
        raise BundleError("Pick the folder that holds databricks.yml.")
    for name in ("databricks.yml", "databricks.yaml"):
        if (root / name).is_file():
            return root / name
    raise BundleError("That folder has no databricks.yml.")


def targets(folder: str) -> dict[str, Any]:
    """The bundle's name and targets from databricks.yml: {name, targets: [{name, mode, default}]}."""
    path = bundle_file(folder)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise BundleError(f"databricks.yml can't be read: {_text(type(exc).__name__)}.") from None
    if not isinstance(data, dict):
        raise BundleError("databricks.yml isn't a bundle definition.")
    found = []
    for name, spec in (data.get("targets") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        if _TARGET.fullmatch(str(name)):
            found.append({"name": str(name), "mode": _text(spec.get("mode")), "default": bool(spec.get("default"))})
    return {"name": _text((data.get("bundle") or {}).get("name")), "file": str(path), "targets": found}


def _load(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise BundleError(f"{path.name} can't be read: {_text(type(exc).__name__)}.") from None


def _bundle_docs(folder: str) -> list[tuple[str, Any]]:
    """databricks.yml and every file its ``include:`` patterns name, as (name, parsed). An include
    that matches nothing, or a file outside the bundle folder, refuses: the CLI would read files
    this check didn't."""
    main = bundle_file(folder)
    root = main.parent.resolve()
    first = _load(main)
    docs = [(main.name, first)]
    for pattern in (first.get("include") or []) if isinstance(first, dict) else []:
        pattern = str(pattern)
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise BundleError(f"The include {_text(pattern)!r} points out of the bundle folder.")
        found = sorted(p for p in root.glob(pattern) if p.is_file())
        if not found:
            raise BundleError(f"The include {_text(pattern)!r} matches no file, so the bundle can't be checked.")
        for path in found:
            real = path.resolve()
            if not real.is_relative_to(root):
                raise BundleError(f"The include {_text(pattern)!r} points out of the bundle folder.")
            docs.append((str(path.relative_to(root)), _load(real)))
    return docs


def _commands_in(where: str, doc: Any) -> list[str]:
    if not isinstance(doc, dict):
        return []
    found: list[str] = []
    scopes = [("", doc)] + [(f"targets.{n}.", t) for n, t in (doc.get("targets") or {}).items() if isinstance(t, dict)]
    for prefix, scope in scopes:
        exp: dict[str, Any] = scope["experimental"] if isinstance(scope.get("experimental"), dict) else {}
        for hook, cmd in (exp.get("scripts") or {}).items() if isinstance(exp.get("scripts"), dict) else []:
            found.append(f"{where}: {prefix}experimental.scripts.{_text(hook)}: {_text(cmd, 300)}")
        for key in ("python", "pydabs"):
            if exp.get(key):
                found.append(f"{where}: {prefix}experimental.{key}: runs Python code from the bundle")
        if scope.get("python"):
            found.append(f"{where}: {prefix}python: runs Python code from the bundle")
        for name, art in (scope.get("artifacts") or {}).items() if isinstance(scope.get("artifacts"), dict) else []:
            art = art if isinstance(art, dict) else {}
            build = art.get("build") or f"the CLI's default build for a {_text(art.get('type') or 'whl')} artifact"
            found.append(f"{where}: {prefix}artifacts.{_text(name)}.build: {_text(build, 300)}")
    return found


def local_commands(folder: str) -> dict[str, Any]:
    """Commands the bundle could make the CLI run on this PC, and their digest (empty when none)."""
    found = [line for where, doc in _bundle_docs(folder) for line in _commands_in(where, doc)]
    return {"commands": found, "digest": hashlib.sha256("\n".join(found).encode()).hexdigest()[:16] if found else ""}


def _gate(folder: str, allow: str) -> dict[str, Any] | None:
    """None when the CLI may run; otherwise the needs_confirm answer listing the commands."""
    found = local_commands(folder)
    if found["commands"] and allow != found["digest"]:
        return {"ok": False, "needs_confirm": True, "local_commands": found["commands"], "commands_digest": found["digest"],
                "error": "This bundle runs commands on this PC. Check them before the CLI runs."}
    return None


def production(folder: str, target: str) -> bool:
    """A target that is production by its mode or by its name."""
    spec = next((t for t in targets(folder)["targets"] if t["name"] == target), None)
    return target.lower() in ("prod", "production", "prd") or bool(spec and spec["mode"] == "production")


# ---------------------------------------------------------------- running the CLI


Runner = Callable[..., Any]


def _run(args: list[str], folder: str, profile: str, run: Runner, timeout: int) -> Any:
    if not _PROFILE.fullmatch(profile or ""):
        raise BundleError("Bundles run as a Databricks CLI profile: pick a connection that uses one.")
    cli = find_cli()
    if not cli:
        raise BundleError("The Databricks CLI isn't installed or isn't on PATH. Install it, then sign in with "
                          "databricks auth login.")
    try:
        return run([cli, "bundle", *args, "--profile", profile], cwd=str(Path(folder)), capture_output=True, text=True,
                   timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise BundleError(f"The Databricks CLI didn't finish within {timeout // 60} minutes.") from None
    except OSError as exc:
        raise BundleError(f"The Databricks CLI couldn't be started ({type(exc).__name__}).") from None


def _tail(text: str, lines: int = 20) -> str:
    kept = (text or "").strip().splitlines()[-lines:]
    return redact.secrets("\n".join("".join(c if c.isprintable() else " " for c in ln) for ln in kept))


def _check_target(folder: str, target: str) -> None:
    if not _TARGET.fullmatch(target or "") or target not in {t["name"] for t in targets(folder)["targets"]}:
        raise BundleError("Pick one of the targets in databricks.yml.")


def validate(folder: str, target: str, profile: str, run: Runner = subprocess.run, allow: str = "") -> dict[str, Any]:
    """``bundle validate``: problems and warnings in the files. Changes nothing in Databricks."""
    _check_target(folder, target)
    if (held := _gate(folder, allow)) is not None:
        return held
    done = _run(["validate", "--target", target, "--output", "json"], folder, profile, run, TIMEOUT // 10)
    if done.returncode != 0:
        return {"ok": False, "error": _tail(done.stderr or done.stdout)}
    warnings = [ln for ln in _tail(done.stderr, 50).splitlines() if ln.lower().startswith(("warning", "recommendation"))]
    return {"ok": True, "warnings": warnings}


# ---------------------------------------------------------------- plan


def _rows(plan: Any) -> list[dict[str, str]]:
    """Plan rows from the JSON ``bundle plan`` prints: {"plan": {"resources.jobs.x": {"action": ...}}},
    or a list of {"resource"|"key", "action"}. Anything else is BundleError."""
    entries: list[tuple[str, Any]] = []
    body = plan.get("plan") if isinstance(plan, dict) and "plan" in plan else plan
    if isinstance(body, dict):
        entries = list(body.items())
    elif isinstance(body, list):
        entries = [(str(e.get("resource") or e.get("key") or ""), e) for e in body if isinstance(e, dict)]
    else:
        raise BundleError("The CLI's plan isn't in a shape this version reads. Update the Databricks CLI.")
    rows = []
    for key, spec in entries:
        action = str((spec or {}).get("action") if isinstance(spec, dict) else spec or "").lower()
        if action not in ACTIONS or not key:
            raise BundleError("The CLI's plan isn't in a shape this version reads. Update the Databricks CLI.")
        parts = key.split(".")
        kind = parts[1] if len(parts) >= 3 and parts[0] == "resources" else ""
        rows.append({"resource": _text(key), "kind": _text(kind.rstrip("s")), "name": _text(parts[-1]), "action": action})
    rows.sort(key=lambda r: (r["action"] not in DESTRUCTIVE, r["resource"]))
    return rows


def digest(rows: list[dict[str, str]]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16]


def plan(folder: str, target: str, profile: str, run: Runner = subprocess.run, allow: str = "") -> dict[str, Any]:
    """What deploying ``target`` would change. Changes nothing in Databricks."""
    _check_target(folder, target)
    if (held := _gate(folder, allow)) is not None:
        return held
    done = _run(["plan", "--target", target, "--output", "json"], folder, profile, run, TIMEOUT // 10)
    if done.returncode != 0:
        return {"ok": False, "error": _tail(done.stderr or done.stdout)}
    try:
        rows = _rows(json.loads(done.stdout or "null"))
    except ValueError as exc:   # json's, or BundleError
        message = str(exc) if isinstance(exc, BundleError) else "The CLI's plan wasn't JSON. Update the Databricks CLI."
        return {"ok": False, "error": message}
    changes = [r for r in rows if r["action"] != "skip"]
    destructive = [r for r in rows if r["action"] in DESTRUCTIVE]
    prod = production(folder, target)
    return {"ok": True, "target": target, "rows": changes, "digest": digest(rows), "destructive": len(destructive),
            "production": prod, "needs_typed_confirm": bool(destructive) or prod}


# ---------------------------------------------------------------- deploy


def deploy(folder: str, target: str, profile: str, seen_digest: str, typed: str = "",
           run: Runner = subprocess.run, allow: str = "") -> dict[str, Any]:
    """Deploy ``target`` if its plan is still the one the person saw (``seen_digest``). A plan that
    deletes or recreates, or a production target, needs ``typed`` == the target's name. A bundle that
    runs commands on this PC needs ``allow`` == their digest (see local_commands)."""
    now = plan(folder, target, profile, run, allow)
    if not now.get("ok"):
        return now
    if now["digest"] != seen_digest:
        return {"ok": False, "replanned": now, "error": "The plan changed since you looked at it. Check the new plan, then deploy."}
    if now["needs_typed_confirm"] and typed != target:
        raise BundleError(f"Type {target} to deploy: this plan deletes or rebuilds something, or the target is production.")
    args = ["deploy", "--target", target]
    if now["destructive"]:
        args.append("--auto-approve")   # the CLI asks on a terminal before it deletes; the person typed the target here
    done = _run(args, folder, profile, run, TIMEOUT)
    if done.returncode != 0:
        return {"ok": False, "error": _tail(done.stderr or done.stdout)}
    return {"ok": True, "target": target, "changed": len(now["rows"]), "log": _tail(done.stderr or done.stdout, 10)}
