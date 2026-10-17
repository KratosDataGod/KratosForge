"""Extensions: capabilities that come from somewhere other than the core (PR-AI-012).

An ``Extension`` is a named, versioned set of capabilities with the permission categories it
declares. ``ExtensionHost`` is the one interface that adds them to the registry, so an extension
reaches MCP, the local API and Settings > Integrations the way a core capability does, with the
same permission checks, approvals and audit: every call still goes through ``Registry.invoke``.

What the host enforces when an extension is enabled, all or nothing:

- its id is a valid extension id and not ``core``; its capability ids start with its own prefix
  (``notebook-outline`` -> ``notebook_outline_...``) and never with ``fabric_desktop_``;
- every capability's permission is one the extension declared, and its source is the extension;
- a resource, if any, is under ``fabric-desktop://ext/<extension id>/``, so it can't stand in for
  a core resource;
- everything the registry checks for any capability (schemas, read-only means a read permission,
  destructive means approval, ...).

Extensions are off until the person turns each one on in Settings > Integrations, and the IT
policy key ``integrations_extensions`` (a list of extension ids) limits which ones may be turned
on; an unreadable value allows none. The enabled set is kept in the integration store, and
``sync()`` makes a registry match it, so the MCP and HTTP server processes pick up a change
within seconds and tell connected tools their lists changed.

Where extensions come from is not decided here. This module has the built-in sample below; the
manifest loader, signing and running an extension in its own process belong to the Git-native
development program (Phase 4), which calls ``ExtensionHost.add``.
"""

from __future__ import annotations

import ast
import re
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from . import policy
from .core import CallContext
from .registry import PERMISSIONS, SOURCE_PATTERN, URI_SCHEME, Capability, Registry, schema

SETTING = "extensions"
CORE_PREFIX = "fabric_desktop_"
VERSION_PATTERN = re.compile(r"^\d+\.\d+(\.\d+)?([-+][0-9A-Za-z.\-]+)?$")


@dataclass(frozen=True)
class Extension:
    id: str
    name: str
    version: str
    publisher: str
    description: str
    permissions: tuple[str, ...]
    # Builds the capabilities when the extension is enabled; called again after every enable.
    capabilities: Callable[[], list[Capability]] = field(compare=False)
    built_in: bool = False

    @property
    def prefix(self) -> str:
        return re.sub(r"[.\-]", "_", self.id) + "_"

    @property
    def resource_root(self) -> str:
        return f"{URI_SCHEME}ext/{self.id}/"


def check(ext: Extension) -> list[Capability]:
    """The extension's capabilities, after checking the extension is well formed and they keep to
    what it declared. Raises ValueError naming the problem."""
    if not isinstance(ext.id, str) or not SOURCE_PATTERN.match(ext.id) or ext.id == "core":
        raise ValueError(f"extension id {ext.id!r} must be lower case letters, digits, '.', '-' and '_' (2-64), not 'core'")
    if ext.prefix.startswith(CORE_PREFIX):
        raise ValueError(f"{ext.id}: an extension id can't start with fabric_desktop")
    for what in ("name", "publisher", "description"):
        if not str(getattr(ext, what) or "").strip():
            raise ValueError(f"{ext.id}: {what} is required")
    if not VERSION_PATTERN.match(ext.version or ""):
        raise ValueError(f"{ext.id}: version {ext.version!r} must look like 1.2.3")
    if not ext.permissions or any(p not in PERMISSIONS for p in ext.permissions):
        raise ValueError(f"{ext.id}: permissions must be one or more of {', '.join(PERMISSIONS)}")
    caps = list(ext.capabilities())
    if not caps:
        raise ValueError(f"{ext.id}: an extension needs at least one capability")
    seen: set[str] = set()
    for cap in caps:
        if not isinstance(cap, Capability):
            raise ValueError(f"{ext.id}: capabilities must be Capability objects")
        if cap.source != ext.id:
            raise ValueError(f"{cap.id}: source must be {ext.id!r}")
        if not cap.id.startswith(ext.prefix) or cap.id.startswith(CORE_PREFIX):
            raise ValueError(f"{cap.id}: an extension's capability ids start with {ext.prefix}")
        if cap.id in seen:
            raise ValueError(f"{cap.id}: listed twice")
        seen.add(cap.id)
        if cap.permission not in ext.permissions:
            raise ValueError(f"{cap.id}: needs {cap.permission}, which {ext.id} doesn't declare")
        if cap.resource and not cap.resource.startswith(ext.resource_root):
            raise ValueError(f"{cap.id}: resources of {ext.id} go under {ext.resource_root}")
    return caps


class ExtensionHost:
    """Keeps one registry's extension capabilities matching what the person enabled and the policy
    allows. Safe to call from several threads."""

    def __init__(self, registry: Registry, store: Any, catalog: list[Extension] | None = None):
        self.registry = registry
        self.store = store
        self._catalog: dict[str, Extension] = {}
        self._stale: set[str] = set()  # replaced by a new version while registered: reload
        self.errors: dict[str, str] = {}
        self._lock = threading.RLock()
        for ext in (BUILT_IN if catalog is None else catalog):
            self.add(ext)

    def add(self, ext: Extension) -> None:
        """Make an extension available to turn on (the manifest loader's entry point). Checks it
        now, so a broken extension is refused before anyone can enable it."""
        check(ext)
        with self._lock:
            if ext.id in self._catalog and self._catalog[ext.id].built_in and not ext.built_in:
                raise ValueError(f"{ext.id} is a built-in extension")
            if ext.id in self._catalog and self._catalog[ext.id].version != ext.version:
                self._stale.add(ext.id)
            self._catalog[ext.id] = ext

    def catalog(self) -> list[Extension]:
        with self._lock:
            return sorted(self._catalog.values(), key=lambda e: e.id)

    # ---- what is on

    def switches(self) -> dict[str, bool]:
        value = self.store.settings().get(SETTING) or {}
        return {k: v is True for k, v in value.items()} if isinstance(value, dict) else {}

    def allowed(self, ext_id: str) -> tuple[bool, str]:
        rules = policy.current()
        allowed = rules.get("extensions")
        if allowed is not None and ext_id not in allowed:
            return False, "Your organization doesn't allow this extension."
        return True, ""

    def wanted(self) -> set[str]:
        on = self.switches()
        with self._lock:
            ids = set(self._catalog)
        return {i for i in ids if on.get(i) and self.allowed(i)[0]}

    def set_enabled(self, ext_id: str, enabled: bool) -> dict[str, Any]:
        """The person's switch for one extension. Turning one on that the policy doesn't allow is
        refused, and so is one whose capabilities don't pass the checks."""
        with self._lock:
            ext = self._catalog.get(ext_id)
        if ext is None:
            raise ValueError(f"There is no extension {ext_id!r}.")
        if enabled:
            ok, reason = self.allowed(ext_id)
            if not ok:
                raise PermissionError(reason)
            check(ext)
        switches = self.switches()
        switches[ext_id] = bool(enabled)
        self.store.set_setting(SETTING, switches)
        self.sync()
        if enabled and ext_id in self.errors:
            raise ValueError(self.errors[ext_id])
        return self.describe(ext_id)

    def sync(self) -> bool:
        """Register what should be on and remove what shouldn't. True when anything changed. Reads
        what is registered from the registry itself, so two hosts on one registry agree."""
        want = self.wanted()
        changed = False
        with self._lock:
            present = {c.source for c in self.registry.all() if c.source in self._catalog}
            for ext_id in sorted(present):
                if ext_id not in want or ext_id in self._stale:
                    self.registry.unregister_source(ext_id)
                    self._stale.discard(ext_id)
                    present.discard(ext_id)
                    changed = True
            for ext_id in sorted(want - present):
                ext = self._catalog[ext_id]
                try:
                    for cap in check(ext):
                        self.registry.register(cap)
                except Exception as exc:  # noqa: BLE001 - one bad extension mustn't stop the rest
                    self.registry.unregister_source(ext_id)  # all or nothing
                    self.errors[ext_id] = str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}: {exc}"
                    print(f"extensions: {ext_id} not loaded: {self.errors[ext_id]}", file=sys.stderr)
                    continue
                self.errors.pop(ext_id, None)
                changed = True
        return changed

    def describe(self, ext_id: str | None = None) -> Any:
        on = self.switches()
        present = {c.source for c in self.registry.all()}
        rows = []
        for ext in self.catalog():
            allowed, reason = self.allowed(ext.id)
            try:
                caps = [{"id": c.id, "title": c.title, "permission": c.permission} for c in check(ext)]
            except Exception as exc:  # noqa: BLE001 - shown, not raised
                caps, reason = [], str(exc)
            rows.append({"id": ext.id, "name": ext.name, "version": ext.version, "publisher": ext.publisher,
                         "description": ext.description, "permissions": list(ext.permissions), "built_in": ext.built_in,
                         "enabled": bool(on.get(ext.id)) and allowed, "loaded": ext.id in present,
                         "allowed": allowed, "reason": reason, "error": self.errors.get(ext.id, ""), "capabilities": caps})
        if ext_id is not None:
            return next(r for r in rows if r["id"] == ext_id)
        return rows


# ---- the built-in sample: a notebook outline, read from the notebook the existing service loads

OUTLINE_ID = "notebook-outline"
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


def _definitions(source: str) -> dict[str, list[str]]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):  # Spark SQL, magics or a half-written cell
        return {"functions": [], "classes": []}
    return {"functions": [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))],
            "classes": [n.name for n in tree.body if isinstance(n, ast.ClassDef)]}


def notebook_outline(svc: Any, ctx: CallContext, name: str) -> dict[str, Any]:
    """Headings and top-level definitions per cell. Names only: no code, no outputs."""
    nb = svc.notebooks.load(name)  # a missing notebook is not_found, as for fabric_desktop_get_notebook
    cells = []
    for number, cell in enumerate(nb.get("cells") or [], 1):
        ctx.check()
        source = cell.get("source") or ""
        row: dict[str, Any] = {"number": number, "type": cell.get("type", "code"), "lines": len(source.splitlines())}
        if row["type"] == "markdown":
            row["headings"] = [{"level": len(m.group(1)), "text": m.group(2)[:200]}
                               for m in map(_HEADING.match, source.splitlines()) if m]
        else:
            row |= _definitions(source)
        cells.append(row)
    return {"name": name, "cells": cells}


def _outline_capabilities() -> list[Capability]:
    cell = schema({"number": {"type": "integer"}, "type": {"type": "string"}, "lines": {"type": "integer"},
                   "headings": {"type": "array"}, "functions": {"type": "array"}, "classes": {"type": "array"}},
                  ["number", "type", "lines"], extra=True)
    return [Capability(
        "notebook_outline_get", "Get notebook outline",
        "A notebook's outline: the headings in its markdown cells and the functions and classes each code cell "
        "defines. Names only, never code or outputs. From the built-in Notebook outline extension.",
        schema({"name": {"type": "string", "minLength": 1, "maxLength": 200,
                         "description": "From fabric_desktop_list_notebooks."}}, ["name"]),
        schema({"name": {"type": "string"}, "cells": {"type": "array", "items": cell}}, ["name", "cells"]),
        "READ_METADATA", notebook_outline, artifact_types=("Notebook",), timeout=30, source=OUTLINE_ID,
        resource=f"{URI_SCHEME}ext/{OUTLINE_ID}/notebooks/{{name}}")]


BUILT_IN: list[Extension] = [Extension(
    OUTLINE_ID, "Notebook outline", "1.0.0", "KratosBI",
    "Lets tools read a notebook's outline (headings, functions and classes) without its code. A sample of how "
    "extensions add capabilities.",
    ("READ_METADATA",), _outline_capabilities, built_in=True)]
