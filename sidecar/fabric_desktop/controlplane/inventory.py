"""The service registry and one lakehouse's inventory (CP-015, docs/product/central-control-plane-backlog.md).

Pure functions over what the app already knows: the registered table families (optimize/schema.FAMILIES)
and what the app read from a lakehouse (its table names and optimize/setup.inspect per family). Reads
only: nothing here talks to Fabric, and nothing is written or deleted.

The registry says which feature owns which tables. The inventory says, for one lakehouse, how each
family's tables are, which tables look like KratosForge's but belong to no family this app knows
("unclaimed": maybe from an older or newer app, never called garbage), and how many other tables there
are (counted, never named: they aren't KratosForge's to list). FEA snapshots and ESI reporting (CP-017)
show up here once they are registered families; nothing is special about them.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from ..optimize import schema

# fd + 2 to 8 lowercase letters + _ at the start of a name: the shape of a family prefix (schema.PREFIX_RULE).
_PREFIX = re.compile(r"^(fd[a-z]{2,8}_)")
UNCLAIMED = "Not KratosForge's, or from another version"


def registry() -> list[dict[str, Any]]:
    """Every registered family: its prefix, owner, schema, latest version and the tables it expects."""
    return [{"prefix": f.prefix, "title": f.title, "schema": f.schema, "latest_version": f.latest(),
             "tables": sorted(f.expected())}
            for f in sorted(schema.FAMILIES.values(), key=lambda f: f.prefix)]


def _unclaimed_prefix(name: str) -> str:
    """The family-like prefix of a table no registered family claims, or "" when it doesn't look like one."""
    m = _PREFIX.match(name)
    return m.group(1) if m and schema.family_of(name) is None else ""


def inventory(table_names: Iterable[str], inspections: list[dict[str, Any]]) -> dict[str, Any]:
    """One lakehouse's inventory. ``table_names``: every table the app sees in it; ``inspections``:
    setup.inspect per registered family, each with ``family`` (its prefix)."""
    names = sorted(set(table_names))
    by_family = {i.get("family"): i for i in inspections}
    families = []
    for f in sorted(schema.FAMILIES.values(), key=lambda f: f.prefix):
        i = by_family.get(f.prefix) or {}
        families.append({"prefix": f.prefix, "title": f.title, "state": i.get("state"),
                         "message": i.get("message", ""), "schema_version": i.get("schema_version"),
                         "app_schema_version": f.latest(), "writable": i.get("writable"),
                         "tables": [n for n in names if schema.family_of(n) is f],
                         "missing": list(i.get("missing") or [])})
    unclaimed = [{"table": n, "prefix": p, "label": UNCLAIMED} for n in names if (p := _unclaimed_prefix(n))]
    others = sum(1 for n in names if schema.family_of(n) is None and not _unclaimed_prefix(n))
    return {"families": families, "unclaimed": unclaimed, "other_tables": others}
