"""Every engine function stays annotated.

sidecar/pyproject.toml turns on mypy's disallow_untyped_defs and disallow_incomplete_defs. The files
written before that were listed in a [[tool.mypy.overrides]] section that switched both off; all of
them are annotated now and the list is gone. No file may switch the checks off again.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

# Files allowed to switch the checks off: none, since every file on the 2026-11-07 list was annotated.
ALLOWED: frozenset[str] = frozenset()


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
    assert not added, (f"{added} can't switch off mypy's annotation checks in pyproject.toml: annotate the "
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
