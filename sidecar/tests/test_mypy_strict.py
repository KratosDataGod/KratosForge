"""The list of files allowed to keep unannotated functions only shrinks.

sidecar/pyproject.toml turns on mypy's disallow_untyped_defs and disallow_incomplete_defs, so new
code annotates every function. Files written before that are listed in one [[tool.mypy.overrides]]
section that switches both off. Annotate a file and take it off the list; never add one.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

# The list when it was made (2026-11-07). Entries may be removed from pyproject.toml, never added:
# remove a file here too once it is off the list, so it can't come back.
ALLOWED = frozenset({
    "fabric_desktop.academy._bootstrap",
    "fabric_desktop.auth",
    "fabric_desktop.best_practice_checkers",
    "fabric_desktop.central.service",
    "fabric_desktop.costs",
    "fabric_desktop.engines.runtime_worker",
    "fabric_desktop.fabric_api",
    "fabric_desktop.jobs",
    "fabric_desktop.localdata.delta",
    "fabric_desktop.localdata.discovery",
    "fabric_desktop.localdata.engine",
    "fabric_desktop.localdata.projects",
    "fabric_desktop.meta.engine",
    "fabric_desktop.meta.project",
    "fabric_desktop.meta.release",
    "fabric_desktop.meta.testing",
    "fabric_desktop.notebook",
    "fabric_desktop.onelake",
    "fabric_desktop.orchestration.activities",
    "fabric_desktop.orchestration.definition",
    "fabric_desktop.pipeline_service",
    "fabric_desktop.proof_runtime",
    "fabric_desktop.proof_spark",
    "fabric_desktop.schema_cache",
    "fabric_desktop.spark_hash",
    "fabric_desktop.sql_templates_service",
    "fabric_desktop.sqlcompat.rules",
    "fabric_desktop.xray_service",
})


def _config() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["mypy"]


def _unannotated() -> set[str]:
    """Modules whose overrides switch off disallow_untyped_defs or disallow_incomplete_defs."""
    out: set[str] = set()
    for override in _config().get("overrides", []):
        if override.get("disallow_untyped_defs") is False or override.get("disallow_incomplete_defs") is False:
            modules = override["module"]
            out.update([modules] if isinstance(modules, str) else modules)
    return out


def test_new_code_must_annotate_every_function():
    config = _config()
    assert config.get("disallow_untyped_defs") is True
    assert config.get("disallow_incomplete_defs") is True


def test_the_list_of_unannotated_files_only_shrinks():
    added = sorted(_unannotated() - ALLOWED)
    assert not added, (f"{added} can't be added to the unannotated list in pyproject.toml: annotate the "
                       "functions mypy reports instead")


def test_no_wildcards_on_the_list():
    assert not [m for m in _unannotated() if "*" in m], "list files one by one, so the list can only shrink"


def test_no_file_switches_the_checks_off_inline():
    """An inline ``# mypy:`` setting for untyped or incomplete defs (``allow-untyped-defs``,
    ``disallow-untyped-defs=False``, ...) would get around the list; the config is the one place for it."""
    package = PYPROJECT.parent / "fabric_desktop"
    found = [str(p.relative_to(package.parent)) for p in package.rglob("*.py")
             if re.search(r"#\s*mypy:.*(untyped|incomplete)[-_]defs", p.read_text(encoding="utf-8"))]
    assert not found, f"{found} switch mypy's annotation checks off inline: annotate the functions instead"
