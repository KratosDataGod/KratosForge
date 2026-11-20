"""Inspecting, initializing, upgrading and checking access to the central lakehouse.

Each call works on one table family (schema.Family; Optimize's ``fdopt_`` tables by default).
``inspect`` reads only: which of the family's tables exist, their columns, and its schema version.
It never looks inside any other table. ``initialize`` creates what is missing and runs the family's
migrations; it never writes to a table outside the family (and Optimize's shared installations,
audit and config tables), never replaces a table, and refuses when one of the family's tables has
columns it doesn't expect. ``check_access`` proves write access by
registering this installation (a row it would write anyway), so it needs no workspace admin
rights to read role assignments.
"""

from __future__ import annotations

import time
from functools import partial
from typing import Any, Callable

import polars as pl

from .. import retrying
from . import schema
from .installation import Installation, now, os_family

Family = schema.Family
CORE = schema.OPTIMIZE  # holds the installations, audit and config every family shares

# What a lakehouse holds, as inspect() reports it.
READY = "ready"            # Optimize's tables at this app's schema version
EMPTY = "empty"            # no tables at all: can be initialized
UNRELATED = "unrelated"    # other tables, no Optimize ones: Optimize's can be added beside them
INCOMPLETE = "incomplete"  # an earlier setup or upgrade stopped part way: running it again finishes it
UPGRADE = "upgrade"        # an older schema version this app can upgrade
NEWER = "newer"            # a newer schema than this app knows: read-only until the app is updated
CONFLICT = "conflict"      # an fdopt_ table with columns Optimize doesn't expect: left alone

CAN_INITIALIZE = {EMPTY, UNRELATED, INCOMPLETE, UPGRADE}
MESSAGES = {
    READY: "Ready.",
    EMPTY: "This lakehouse is empty. Optimize can create its tables here.",
    UNRELATED: "This lakehouse has other tables. Optimize can add its own beside them without touching them.",
    INCOMPLETE: "An earlier setup didn't finish. Finishing it adds only what is missing and keeps every row.",
    UPGRADE: "This lakehouse uses an older Optimize schema. Upgrading adds tables and columns and keeps every row.",
    NEWER: "Another installation upgraded this lakehouse to a newer Optimize schema. Update KratosForge "
           "to write to it; until then you can read it.",
    CONFLICT: "Some tables named fdopt_ here aren't Optimize's, so Optimize won't write to this lakehouse. "
              "Pick another lakehouse, or create a new one.",
}


class SchemaMismatch(PermissionError):
    """A write refused because the lakehouse's schema version doesn't fit this app: it is newer
    (update the app) or older and lacks the table (upgrade the lakehouse). A person has to act, so
    the outbox shows it as a conflict (ESI-105). ``state`` is NEWER or UPGRADE."""

    def __init__(self, state: str, family: Family = CORE):
        super().__init__(message(state, family))
        self.state = state


def _type_ok(have: pl.DataType, want: schema.Dtype) -> bool:
    if have == want:
        return True
    # Strings may come back as large strings or views, depending on the writer.
    return want == pl.String and have in (pl.String, pl.Utf8)


def message(state: str, family: Family = CORE) -> str:
    """MESSAGES[state], naming the family's owner instead of Optimize."""
    text = MESSAGES[state]
    return text if family is CORE else text.replace("Optimize", family.title).replace("fdopt_", family.prefix)


def applied_versions(lake: Any, family: Family = CORE) -> list[int]:
    df = lake.rows(family.table("schema_version"))
    if df is None or not len(df):
        return []
    return sorted({int(v) for v in df["version"].drop_nulls().to_list()})


def inspect(lake: Any, family: Family = CORE) -> dict[str, Any]:
    names = set(lake.table_names())
    ours = sorted(n for n in names if n.startswith(family.prefix))
    applied = applied_versions(lake, family) if family.table("schema_version") in names else []
    version = max(applied, default=0)
    latest = family.latest()
    want = family.expected(min(version, latest) or latest)
    conflicts = []
    for name in ours:
        if name not in want:
            continue  # a table from a newer version this app doesn't know: not ours to judge
        have = lake.columns_of(name)
        bad = [c for c, t in want[name].items() if c in have and not _type_ok(have[c], t)]
        if bad:
            conflicts.append({"table": name, "columns": bad})
    missing = sorted(n for n in want if n not in names)
    if conflicts:
        state = CONFLICT
    elif version > latest:
        state = NEWER
    elif not ours:
        state = EMPTY if not names else UNRELATED
    elif version < latest and version:
        state = UPGRADE
    elif missing or not version:
        state = INCOMPLETE
    else:
        state = READY
    # What initializing or upgrading would create (nothing for a newer lakehouse: it is read-only).
    adds = sorted(n for n in family.expected() if n not in names) if version <= latest else []
    # Tables of other registered families are the central lakehouse's own, not "other tables".
    others = sum(1 for n in names if schema.family_of(n) is None)
    return {"state": state, "message": message(state, family), "family": family.prefix, "schema_version": version or None, "app_schema_version": latest,
            "missing": missing, "adds": adds, "conflicts": conflicts, "other_tables": others,
            "writable": state not in (NEWER, CONFLICT)}


def retry(fn: Callable[[], Any], attempts: int = 5, backoff: float = 0.5, sleep: Callable[[float], None] = time.sleep) -> Any:
    """Run ``fn`` again after a lost commit race, throttling or a network blip: bounded, with backoff."""
    return retrying.retry_call(fn, retrying.is_retryable, attempts,
                               lambda n: min(backoff * 2 ** (n - 1), 8.0), sleep)


def _rows(name: str, rows: list[dict[str, Any]], family: Family = CORE, version: int | None = None) -> pl.DataFrame:
    tables = family.expected(version)
    if family.table(name) not in tables:
        # A table a later version adds, in a lakehouse not upgraded to it yet.
        raise SchemaMismatch(UPGRADE, family)
    cols = tables[family.table(name)]
    return pl.DataFrame([{c: r.get(c) for c in cols} for r in rows], schema=cols)


def core_version(lake: Any) -> int | None:
    """The Optimize schema version the lakehouse is at, for rows of the shared tables (installations,
    audit, config) that any family writes: their provenance names the version they went into."""
    return max(applied_versions(lake), default=0) or None


def write(lake: Any, installation: Installation, name: str, rows: list[dict[str, Any]], version: int | None = None,
          family: Family = CORE) -> None:
    """Insert rows into ``<prefix><name>``, each with provenance; rows already there are skipped.
    ``version`` is the family's schema version the lakehouse is at (default: this app's): rows get only
    that version's columns, so an app that knows a later version still writes to a lakehouse nobody
    has upgraded yet, and a table that version doesn't have is refused with the upgrade message."""
    version = min(version or family.latest(), family.latest())
    base = installation.provenance(version)
    df = _rows(name, [base | r for r in rows], family, version)
    retry(lambda: lake.insert_new(family.table(name), df))


def initialize(lake: Any, installation: Installation, approve: bool = False, family: Family = CORE) -> dict[str, Any]:
    """Create the family's missing tables, run every migration of it not yet recorded, and register
    this installation. Another family first initializes Optimize's shared tables (installations,
    audit, config), under the same approval. Safe to run again, and safe when two installations run
    it at once."""
    if family is not CORE:
        core = inspect(lake)
        # An older core schema already has the shared tables, which every version keeps the same;
        # upgrading Optimize's own tables is Optimize's own step, not another family's.
        if core["state"] not in (READY, UPGRADE):
            initialize(lake, installation, approve)
    before = inspect(lake, family)
    if before["state"] == READY:
        register(lake, installation)
        return before
    if before["state"] not in CAN_INITIALIZE:
        raise PermissionError(before["message"])
    if not approve:
        raise PermissionError(f"{before['message']} Confirm to continue.")
    done = set(applied_versions(lake, family)) if before["schema_version"] else set()
    for m in family.migrations:
        for name in m.tables:
            full, cols = family.table(name), family.expected(m.version)[family.table(name)]
            if not retry(partial(lake.create, full, cols)):
                retry(partial(lake.add_columns, full, cols))
        for step in m.steps:
            retry(partial(step, lake))
        if m.version not in done:
            write(lake, installation, "schema_version",
                  [{"record_id": f"v{m.version}", "version": m.version, "description": m.description,
                    "applied_at": now()}], m.version, family)
    if family.default_config:
        # Another family's keys carry its prefix, so two families' settings never share a row.
        keys = [(k if family is CORE else family.prefix + k, v) for k, v in family.default_config]
        write(lake, installation, "config", [{"record_id": f"default:{k}", "key": k, "value": v, "at": now()}
                                             for k, v in keys], core_version(lake))
    action = "upgrade" if before["state"] == UPGRADE else "initialize"
    write(lake, installation, "audit", [{"record_id": f"{installation.id}:{action}:{family.prefix}:{now()}",
                                         "at": now(), "action": action,
                                         "target": f"{family.prefix} schema {family.latest()}", "outcome": "ok",
                                         "message": f"from {before['state']}"}], core_version(lake))
    register(lake, installation)
    return inspect(lake, family)


def register(lake: Any, installation: Installation) -> None:
    """Record this installation once (record_id is its id, so connecting again adds nothing)."""
    write(lake, installation, "installations",
          [{"record_id": installation.id, "label": installation.label, "os": os_family(), "registered_at": now()}],
          core_version(lake))


def denied(exc: BaseException) -> bool:
    """Whether an error means "you don't have permission" rather than something going wrong."""
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if code in (401, 403):
        return True
    text = str(exc)
    return any(s in text for s in ("403", "Forbidden", "AuthorizationPermissionMismatch", "AuthorizationFailure",
                                   "PermissionDenied", "Unauthorized", "401"))


def check_access(lake: Any, installation: Installation) -> dict[str, Any]:
    """Whether this person can read and write the lakehouse, found by doing it: listing the tables
    and reading the schema version, then registering this installation."""
    try:
        state = inspect(lake)
    except Exception as exc:
        if denied(exc):
            return {"read": False, "write": False, "state": None,
                    "message": "You can't read this lakehouse. Ask its owner for Contributor on the workspace, "
                               "or for the lakehouse's Read all data permission."}
        raise
    if state["state"] != READY:
        return {"read": True, "write": False, "state": state, "message": state["message"]}
    try:
        register(lake, installation)
    except Exception as exc:
        if denied(exc):
            return {"read": True, "write": False, "state": state,
                    "message": "You can read this lakehouse but not write to it, so Optimize opens it read-only. "
                               "To add your results, ask for Contributor on its workspace."}
        raise
    return {"read": True, "write": True, "state": state, "message": "You can read and write this lakehouse."}
