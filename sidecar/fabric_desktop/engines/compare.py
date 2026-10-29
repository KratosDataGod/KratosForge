"""Run one query on two engines and compare the answers (KUDP-06).

``compare(left, right, request)`` takes two sessions the caller already opened (opening a remote one
already needed its authorization, authorization.py), runs the request on each with ``result_rows``
set, and compares the two result tables with verify.compare: the same rules, levels and limits as
Prove. Nothing is written anywhere; both tables live in memory and go when the report is returned.

Refused before anything runs:

- an engine that doesn't claim the ``result`` capability (the report quotes its note);
- a remote engine, unless ``copy_confirmed``. Returning a remote result copies the platform's data
  onto this PC, and copying across platforms is never implicit: the caller passes
  ``copy_confirmed=True`` only after a confirm made for that copy, naming the engine and target, and
  recorded in the audit log; never from a stored setting. Opening the session is not it.

The left side runs first; if it fails, the right side doesn't run (it may be billed). verify names
the two sides ``pc`` and ``fabric``; in this report ``pc`` is the left engine and ``fabric`` the right.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from .capabilities import EngineDescriptor
from .contract import CellRequest, CellResult, EngineSession, Unsupported

SCHEMA = "engine-compare@1"
DEFAULT_ROWS = 1_000_000
NOTES = ("Both answers were read into memory on this PC and are not kept.",
         "verify calls the sides pc and fabric: here pc is the left engine and fabric the right.")


class CopyNotConfirmed(PermissionError):
    """A remote engine's result would come onto this PC without the caller's confirm."""


def _descriptor(session: EngineSession) -> EngineDescriptor:
    from . import registry
    return registry.get(session.engine)


def refusal(engine: EngineDescriptor, copy_confirmed: bool) -> str | None:
    """Why ``engine`` can't take part in a comparison, or None when it can. Static: nothing runs."""
    result = engine.capability("result")
    if not result.available:
        return f"{engine.label} can't return a whole result to compare." + (f" {result.note}" if result.note else "")
    if engine.remote and not copy_confirmed:
        return (f"Comparing with {engine.label} copies its result onto this PC. Confirm that copy first; "
                "starting the session didn't.")
    return None


def _side(engine: EngineDescriptor, result: CellResult | None) -> dict[str, Any]:
    out: dict[str, Any] = {"engine": engine.id, "label": engine.label}
    if result is None:
        return out | {"status": "not_run"}
    out |= {"status": result.status, "rows": result.rows, "seconds": round(result.seconds, 4)}
    errors = [o for o in result.outputs if o.get("type") == "error"]
    if errors:
        out["error"] = f"{errors[-1].get('ename', 'Error')}: {errors[-1].get('evalue', '')}"
    return out


def compare(left: EngineSession, right: EngineSession, request: CellRequest,
            right_request: CellRequest | None = None, rules: dict[str, Any] | None = None, *,
            rows: int = DEFAULT_ROWS, copy_confirmed: bool = False) -> dict[str, Any]:
    """Run ``request`` on ``left`` and ``right_request`` (or the same request) on ``right``; compare the answers.

    ``rows`` is the most rows either side may return; more is an error on that side, never a cut
    table. ``rules`` are verify's comparison rules (keys, tolerances...)."""
    from .. import verify
    if left is right:
        raise ValueError("Pick two different sessions to compare.")
    if isinstance(rows, bool) or not isinstance(rows, int) or rows <= 0:
        raise ValueError(f"rows is a whole number above 0, not {rows!r}.")
    checked_rules = verify.rules(rules)   # a bad rule is refused before anything runs
    engines = (_descriptor(left), _descriptor(right))
    for engine in engines:
        reason = refusal(engine, copy_confirmed)
        if reason:
            if engine.remote and engine.capability("result").available:
                raise CopyNotConfirmed(reason)
            raise Unsupported(reason)
    requests = (dataclasses.replace(request, result_rows=rows),
                dataclasses.replace(right_request or request, result_rows=rows))
    report: dict[str, Any] = {"schema": SCHEMA, "notes": list(NOTES)}
    first = left.execute(requests[0])
    second = right.execute(requests[1]) if first.status == "ok" and first.table is not None else None
    report["left"], report["right"] = _side(engines[0], first), _side(engines[1], second)
    for name, res in (("left", first), ("right", second)):
        if res is None or res.status != "ok" or res.table is None:
            report["result"] = "not_compared"
            report["problem"] = (f"The {name} engine ({report[name]['label']}) "
                                 + ("returned no rows to compare." if res is not None and res.status == "ok"
                                    else "didn't give an answer.")
                                 + (f" {report[name]['error']}" if report[name].get("error") else ""))
            return report
    assert first.table is not None and second is not None and second.table is not None
    try:
        comparison = verify.compare(first.table, second.table, checked_rules)
    except ValueError as exc:   # a key column only one side has
        return report | {"result": "not_compared", "problem": str(exc)}
    return report | {"result": comparison["result"], "level": comparison["level"], "comparison": comparison}
