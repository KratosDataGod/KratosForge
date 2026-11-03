"""Pipeline and orchestration exercises (ACD-012): the ``pipeline_lab`` adapter.

The learner's answer is a pipeline definition (``pipeline.json``, Fabric or Data Factory JSON) or a
runMultiple DAG (``dag.json``) in the workspace. Both are parsed as data with a size cap; nothing in
them is imported, ``exec``'d or ``eval``'d.

A pipeline runs on the local runner (orchestration/runner.py) in the engine, with no notebook runner
and no lakehouse reader, waits that take no time and a run time limit. Only control-flow activities
run (``ALLOWED``). Any other activity runs only when the exercise gives a mock for it by name; the
exercise's mocks and child pipelines come from its own folder, never from the workspace. A pipeline
holding anything else (Copy, Web, a notebook, a script) fails with a message before it starts, so
nothing reaches Fabric, the network or the person's files.

A runMultiple DAG is checked with the same rules as notebookutils on this PC (unknown keys, missing
or circular dependencies) and against the exercise's ordering rules; no notebook runs.

Checks (``validation``):

    pipeline_outcome  the run ended with ``status`` and the given ``activities``, ``outputs`` and
                      ``return_value`` (validators.pipeline_outcome)
    dag_matches       the DAG is valid, runs ``notebooks`` (and no others with ``exact``), runs each
                      ``after`` pair in that order, and caps ``concurrency`` at most ``max_concurrency``
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .. import jsonfile
from ..notebookutils_local import _activities as _dag_order
from ..orchestration.definition import DefinitionError, loads
from ..orchestration.runner import FABRIC_STATUS, PipelineRun, RunRefused
from . import validators
from . import workspace as ws

ADAPTER = "pipeline_lab"
CHECKS = ("pipeline_outcome", "dag_matches")
# Control flow only: these touch nothing outside the run. Everything else needs the exercise's mock.
ALLOWED = ("SetVariable", "AppendVariable", "IfCondition", "Switch", "ForEach", "Until", "Wait", "Fail", "Filter",
           "ExecutePipeline", "InvokePipeline")
TIME_LIMIT = 20.0
MAX_ACTIVITIES = 200


def _no(kind: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "passed": False, "detail": detail}


def check_problems(c: dict[str, Any]) -> list[str]:
    """What's wrong with one check's fields, in words (for exercises.problems)."""
    kind = c.get("kind")
    out = []
    if kind == "pipeline_outcome" and not isinstance(c.get("status", "Succeeded"), str):
        out.append("check pipeline_outcome's status is Succeeded, Failed or Cancelled")
    if kind == "dag_matches" and not c.get("notebooks"):
        out.append("check dag_matches needs notebooks")
    return out


def _not_allowed(imp: Any, mocks: dict[str, Any]) -> list[str]:
    return [f"{a.name} ({a.type})" for a in imp.pipeline.all_activities()
            if a.type not in ALLOWED and a.name not in mocks and not a.inactive]


def run_pipeline(work: Path, rel: str, *, parameters: dict[str, Any] | None = None,
                 mocks: dict[str, dict[str, Any]] | None = None, children: dict[str, str] | None = None,
                 time_limit: float = TIME_LIMIT) -> dict[str, Any]:
    """Run the learner's pipeline file. ``{ok, detail, run}``; ``run`` is the runner's record with
    Fabric's status words, or None when it didn't start."""
    mocks = dict(mocks or {})
    try:
        imp = loads(ws.read_bytes(work, rel, ws.MAX_JSON_BYTES).decode("utf-8-sig"), name_hint=Path(rel).stem)
    except ws.Refused as exc:
        return {"ok": False, "detail": str(exc), "run": None}
    except (DefinitionError, UnicodeDecodeError, RecursionError) as exc:
        return {"ok": False, "detail": f"{rel} isn't a pipeline the app can read: {str(exc)[:300]}", "run": None}
    if len(imp.pipeline.all_activities()) > MAX_ACTIVITIES:
        return {"ok": False, "detail": f"The pipeline has more than {MAX_ACTIVITIES} activities.", "run": None}
    blocked = _not_allowed(imp, mocks)
    if blocked:
        return {"ok": False, "run": None,
                "detail": "This exercise runs control-flow activities only, and the exercise doesn't stand in for "
                          f"{', '.join(blocked[:5])}" + (" and more" if len(blocked) > 5 else "") + "."}
    for key, text in (children or {}).items():   # a child pipeline from the exercise is held to the same rule
        if _not_allowed(loads(text), mocks):
            return {"ok": False, "run": None, "detail": f"Child pipeline {key} holds activities the exercise can't run."}
    run = PipelineRun(imp, parameters=parameters, mocks=mocks, children=dict(children or {}), wait_scale=0.0,
                      timeout_seconds=time_limit, max_parallel=4)
    try:
        run.run()
    except RunRefused as exc:
        said = "; ".join(p["message"] for p in exc.problems[:3])
        return {"ok": False, "detail": f"The pipeline couldn't start: {said}", "run": None}
    record = run.to_json()
    record["state"] = FABRIC_STATUS.get(record["state"], record["state"])
    return {"ok": True, "detail": "", "run": record}


def dag_matches(dag: Any, notebooks: list[str], after: list[list[str]] | None = None, exact: bool = False,
                max_concurrency: int | None = None) -> dict[str, Any]:
    """A runMultiple DAG: valid on this PC, runs ``notebooks`` (by activity name), runs each
    ``[first, then]`` pair in that order through its dependencies, and caps concurrency."""
    kind = "dag_matches"
    try:
        ordered = _dag_order(dag)
    except (TypeError, ValueError, NotImplementedError, AttributeError) as exc:
        return _no(kind, f"The DAG isn't valid: {str(exc)[:300]}")
    by_name = {a["name"]: a for a in ordered}
    missing = [n for n in notebooks if n not in by_name]
    if missing:
        return _no(kind, f"The DAG doesn't run {', '.join(missing[:5])}.")
    extra = [n for n in by_name if n not in notebooks]
    if exact and extra:
        return _no(kind, f"The DAG also runs {', '.join(extra[:5])}, which the exercise doesn't ask for.")

    def upstream(name: str) -> set[str]:
        seen: set[str] = set()
        stack = list(by_name[name]["dependencies"])
        while stack:
            d = stack.pop()
            if d not in seen:
                seen.add(d)
                stack.extend(by_name[d]["dependencies"])
        return seen

    for first, then in after or []:
        if first in by_name and then in by_name and first not in upstream(then):
            return _no(kind, f"{then} can start before {first} finishes; make it depend on {first}.")
    if max_concurrency is not None:
        got = dag.get("concurrency") if isinstance(dag, dict) else None
        if not isinstance(got, int) or not 1 <= got <= max_concurrency:
            return _no(kind, f"The DAG's concurrency is {got if got is not None else 'not set'}; the exercise needs "
                             f"1 to {max_concurrency}.")
    return {"kind": kind, "passed": True, "detail": f"The DAG runs {len(by_name)} notebooks in a valid order."}


def run_check(work: Path, c: dict[str, Any], ex: dict[str, Any], children: dict[str, str] | None = None) -> dict[str, Any]:
    kind = c["kind"]
    if kind == "dag_matches":
        try:
            dag = ws.read_json(work, c.get("path") or ex.get("starter") or "dag.json")
        except ws.Refused as exc:
            return _no(kind, str(exc))
        return dag_matches(dag, list(c["notebooks"]), c.get("after"), bool(c.get("exact")), c.get("max_concurrency"))
    if kind == "pipeline_outcome":
        got = run_pipeline(work, c.get("path") or ex.get("starter") or "pipeline.json",
                           parameters=ex.get("parameters"), mocks=ex.get("mocks"), children=children)
        if not got["ok"]:
            return _no(kind, got["detail"])
        return validators.pipeline_outcome(got["run"], c.get("status", "Succeeded"), c.get("activities"),
                                           c.get("outputs"), c.get("return_value"))
    return _no(kind, f"A pipeline exercise can't check {kind}.")


def grade(work: Path, ex: dict[str, Any], folder: Path | None = None) -> dict[str, Any]:
    """Every check of ``ex`` on the learner's files in ``work``. Child pipelines named in the
    exercise (``children``: key -> file) are read from its own ``folder``."""
    children = {k: (folder / name).read_text(encoding="utf-8") for k, name in (ex.get("children") or {}).items()} \
        if folder is not None else {}
    results = [run_check(Path(work), c, ex, children) for c in ex["validation"]]
    return {"passed": bool(results) and all(r["passed"] for r in results), "results": results}


def self_test(folder: Path, grading: Path, ex: dict[str, Any]) -> dict[str, Any]:
    """The reference (``grading/<id>/solution.json``) must pass and every ``wrong-*.json`` fail,
    each written as the starter file in a fresh workspace of its own."""
    def one(source: Path) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="kf-academy-selftest-") as tmp:
            work = Path(tmp)
            jsonfile.write_text(work / ex["starter"], source.read_text(encoding="utf-8"))
            return grade(work, ex, folder)

    return {"solution": one(grading / "solution.json"),
            "wrong": {p.name: one(p) for p in sorted(grading.glob("wrong-*.json"))}}

