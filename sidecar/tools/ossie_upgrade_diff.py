"""Converter-upgrade regression (OSSIE-012c): the Ossie fixture corpus through the vendored converter and through a
candidate, and what changed per file.

A developer tool, run by hand before a vendor update; the engine never imports it, and it never goes online.

    cd sidecar
    python tools/ossie_upgrade_diff.py path/to/ossie_microsoft           # a candidate package folder
    python tools/ossie_upgrade_diff.py candidate.py --only hostile/      # or one module; only some files
    python tools/ossie_upgrade_diff.py candidate.py --json               # the rows as JSON

The candidate is a module file or a package folder that defines ``convert_ossie_to_semantic_model`` and
``convert_semantic_model_to_ossie`` (and may define ``ConversionError`` and ``OSSIE_VERSION``), as the vendored
``fabric_desktop._vendor.ossie_microsoft`` does. Loading it runs its code, so only point it at code you'd run.
It is loaded under its own name, then swapped into ``sys.modules`` in place of the vendored package and into
``model_ossie``'s converter names for the run; both are always put back afterwards, also when the run fails.

Every file of ``tests/fixtures/ossie_corpus`` runs through every entry point (``tests/ossie_corpus.py``) with each
converter. A row is printed for each entry point whose outcome changed and for each object whose fidelity class
changed, marked worse, better or changed. Worse is a lower outcome (ok, then refused, then an error), a lower
fidelity class or an object gone, or an outcome that matched the manifest and no longer does (a hostile file
the vendored converter refuses and the candidate takes is worse, not better).

Exit codes: 0 nothing got worse, 1 something got worse, 2 the candidate can't be used (it doesn't import, or it
lacks a function).
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

SIDECAR = Path(__file__).resolve().parents[1]
for _p in (SIDECAR, SIDECAR / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import ossie_corpus as oc  # noqa: E402  # the corpus loader beside the tests
from fabric_desktop import model_ossie as mo  # noqa: E402
from fabric_desktop import semantic_parity  # noqa: E402

VENDOR = "fabric_desktop._vendor.ossie_microsoft"
CANDIDATE = "_ossie_upgrade_candidate"  # the name the candidate loads under, and the prefix of its submodules
REQUIRED = ("convert_ossie_to_semantic_model", "convert_semantic_model_to_ossie")
OPTIONAL = ("ConversionError", "OSSIE_VERSION")
NO_REGRESSION, REGRESSION, BAD_CANDIDATE = 0, 1, 2
# Lower is better. A word not listed ranks as an error.
_RANKS = {
    oc.OK: 0, oc.REFUSED: 1, "same": 0, "differs": 1, "not run": 1, "valid": 0, "invalid": 1,
    "Valid": 0, "Unverified": 1, "Invalid": 1, "Not checked": 1,
    semantic_parity.CEILING: 0, semantic_parity.UNSUPPORTED: 1, semantic_parity.DIFFERENT: 2,
    semantic_parity.TEST_FAILED: 3,
}
_ERROR_RANK = 4
_CLASS_RANKS = {"fully_preserved": 0, "preserved_through_extensions": 1, "unverified": 2, "partially_converted": 3,
                "unsupported": 4}
_GONE = "(gone)"


class BadCandidate(Exception):
    """The candidate can't be used: it doesn't import, or it lacks a function."""


def _rank(word: str) -> int:
    return _RANKS.get(word, _ERROR_RANK)


def _class_rank(cls: str) -> int:
    return _CLASS_RANKS.get(cls, len(_CLASS_RANKS))


def load_candidate(path: Path) -> ModuleType:
    """The candidate module or package at ``path``, loaded as ``CANDIDATE`` (left in ``sys.modules``: the caller
    removes it). Raises ``BadCandidate`` with the reason."""
    package = path.is_dir()
    file = path / "__init__.py" if package else path
    if not file.is_file():
        raise BadCandidate(f"there's no module at {path}" + (" (a package folder needs __init__.py)" if package else ""))
    spec = importlib.util.spec_from_file_location(CANDIDATE, file,
                                                  submodule_search_locations=[str(path)] if package else None)
    if spec is None or spec.loader is None:
        raise BadCandidate(f"{path} can't be loaded as a Python module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[CANDIDATE] = module  # before it runs, so its relative imports find it
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001  # narrow: any failure importing the candidate is the reason it's unusable
        raise BadCandidate(f"it doesn't import: {type(exc).__name__}: {mo._short(exc)}") from exc
    missing = [name for name in REQUIRED if not callable(getattr(module, name, None))]
    if missing:
        raise BadCandidate(f"it lacks {', '.join(missing)}")
    return module


@contextlib.contextmanager
def candidate_swapped(path: Path) -> Iterator[ModuleType]:
    """The candidate loaded and standing in for the vendored converter, in ``sys.modules`` and in ``model_ossie``;
    afterwards, however the block ends, both are as they were and none of the candidate's modules is left."""
    mo.require()  # the vendored converter loaded first, so the names put back are the vendored ones
    saved_module = sys.modules.get(VENDOR)
    saved_names = {name: getattr(mo, name) for name in (*REQUIRED, *OPTIONAL)}
    try:
        module = load_candidate(path)
        sys.modules[VENDOR] = module
        for name in (*REQUIRED, *OPTIONAL):
            if hasattr(module, name):
                setattr(mo, name, getattr(module, name))
        yield module
    finally:
        for name, value in saved_names.items():
            setattr(mo, name, value)
        if saved_module is None:
            sys.modules.pop(VENDOR, None)
        else:
            sys.modules[VENDOR] = saved_module
        for key in [k for k in sys.modules if k == CANDIDATE or k.startswith(CANDIDATE + ".")]:
            del sys.modules[key]


def run_corpus(entries: list[dict[str, Any]]) -> dict[str, dict[str, tuple[str, dict[str, Any]]]]:
    """Each entry's outcomes with the converter in place now: path -> point -> (word, detail)."""
    return {e["path"]: oc.outcomes(e) for e in entries}


def _row(path: str, what: str, before: str, after: str, change: str, why: str = "") -> dict[str, str]:
    return {"path": path, "what": what, "before": before, "after": after, "change": change, "why": why}


def compare(entries: list[dict[str, Any]], base: dict[str, Any], cand: dict[str, Any]) -> list[dict[str, str]]:
    """One row per entry point whose outcome changed and per object whose fidelity class changed."""
    rows = []
    for e in entries:
        path = e["path"]
        for point, (before, b_detail) in base[path].items():
            after, a_detail = cand[path][point]
            expected = e["expect"].get(point)
            if before != after:
                if before == expected and after != expected:
                    change, why = "worse", f"the manifest expects {expected}"
                elif after == expected and before != expected:
                    change, why = "better", f"the manifest expects {expected}"
                else:
                    change = "worse" if _rank(after) > _rank(before) else \
                        "better" if _rank(after) < _rank(before) else "changed"
                    why = ""
                rows.append(_row(path, point, before, after, change, why))
            b_fid, a_fid = b_detail.get("fidelity"), a_detail.get("fidelity")
            if b_fid is None or a_fid is None:
                continue
            for obj in list(b_fid) + [o for o in a_fid if o not in b_fid]:
                was, now = b_fid.get(obj, _GONE), a_fid.get(obj, _GONE)
                if was == now:
                    continue
                change = "changed" if was == _GONE else "worse" if now == _GONE or _class_rank(now) > _class_rank(was) \
                    else "better" if _class_rank(now) < _class_rank(was) else "changed"
                rows.append(_row(path, f"{point} fidelity {mo._short(obj)}", was, now, change))
    return rows


def diff(candidate: Path, only: list[str] | None = None) -> list[dict[str, str]]:
    """The rows of ``compare`` for the corpus (or the files whose path starts with one of ``only``), the vendored
    converter against ``candidate``. Raises ``BadCandidate``."""
    entries = [e for e in oc.manifest() if not only or any(e["path"].startswith(o) for o in only)]
    if not entries:
        raise ValueError(f"no corpus file starts with {', '.join(only or [])}")
    base = run_corpus(entries)
    with candidate_swapped(candidate):
        cand = run_corpus(entries)
    return compare(entries, base, cand)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare the Ossie corpus through the vendored converter and a "
                                                 "candidate (exit 0 no regressions, 1 worse, 2 bad candidate).")
    parser.add_argument("candidate", type=Path, help="a candidate module file or package folder")
    parser.add_argument("--only", action="append", help="only the corpus files whose path starts with this")
    parser.add_argument("--json", action="store_true", help="print the rows as JSON")
    args = parser.parse_args(argv)
    logging.getLogger("ossie_microsoft").addHandler(logging.NullHandler())  # its warnings are in the report already
    try:
        rows = diff(args.candidate, args.only)
    except ValueError as exc:
        parser.error(str(exc))  # exits 2
    except BadCandidate as exc:
        print(f"The candidate can't be used: {exc}", file=sys.stderr)
        return BAD_CANDIDATE
    worse = [r for r in rows if r["change"] == "worse"]
    if args.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    else:
        for r in rows:
            why = f" ({r['why']})" if r["why"] else ""
            print(f"{r['change']:7} {r['path']}  {r['what']}: {r['before']} -> {r['after']}{why}")
        counts = {c: sum(1 for r in rows if r["change"] == c) for c in ("worse", "better", "changed")}
        print(f"{len(rows)} differences: {counts['worse']} worse, {counts['better']} better, "
              f"{counts['changed']} changed.")
    return REGRESSION if worse else NO_REGRESSION


if __name__ == "__main__":
    sys.exit(main())
