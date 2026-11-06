"""``notebookutils.notebook.runMultiple`` on this PC: the DAG's order, retries and timeouts.

Shared by the notebooks' notebookutils (notebookutils_local) and the Academy's PySpark exercises
(academy/notebookutils_lab), which is why it imports only the standard library: it is copied into
the learner's ``kf_learner`` package (academy/learner_pkg.py).

The notebooks run one at a time. Each activity gets its ``args``, runs after its ``dependencies`` and
is retried ``retry`` times; an activity whose dependency failed is skipped. Every result is kept; if
any activity failed, a RuntimeError lists them after all have run, with the results on its
``results`` attribute, as Fabric's runMultiple does.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any, Callable, ContextManager

ACTIVITY_KEYS = {"name", "path", "args", "dependencies", "retry", "retryIntervalInSeconds", "timeoutPerCellInSeconds"}
# run(path, timeout_seconds, args, cell_timeout) -> the notebook's exit value. timeout_seconds 0: no limit.
Run = Callable[[str, float, dict[str, Any], "float | None"], Any]


def activities(dag: Any) -> list[dict[str, Any]]:
    """runMultiple's activities in an order that runs each after its dependencies. Anything the
    PC can't honor (an unknown key, a missing or circular dependency) is an error before any run."""
    if isinstance(dag, (list, tuple)):
        if len(set(dag)) != len(dag):
            # Results are keyed by name, so a repeat would keep only its last run's result.
            raise ValueError("runMultiple: a notebook is listed twice; use a DAG with a unique name for each run.")
        return [{"name": p, "path": p, "args": {}, "dependencies": [], "retry": 0, "retry_interval": 0,
                 "cell_timeout": None} for p in dag]
    if not isinstance(dag, dict) or not isinstance(dag.get("activities"), list):
        raise TypeError("runMultiple takes a list of notebook names or a DAG: {\"activities\": [...]}.")
    unknown = set(dag) - {"activities", "timeoutInSeconds", "concurrency"}
    if unknown:
        raise NotImplementedError(f"runMultiple on this PC doesn't support {', '.join(sorted(unknown))}.")
    out: dict[str, dict[str, Any]] = {}
    for a in dag["activities"]:
        if not isinstance(a, dict):
            raise TypeError("Each runMultiple activity is a dict with a name and a path.")
        extra = set(a) - ACTIVITY_KEYS
        if extra:
            raise NotImplementedError(f"runMultiple on this PC doesn't support {', '.join(sorted(extra))} in an activity.")
        name: Any = a.get("name") or a.get("path")
        if not a.get("path") or name in out:
            raise ValueError(f"Every runMultiple activity needs a path and a unique name ({name!r}).")
        out[name] = {"name": name, "path": a["path"], "args": dict(a.get("args") or {}),
                     "dependencies": list(a.get("dependencies") or []), "retry": int(a.get("retry") or 0),
                     "retry_interval": float(a.get("retryIntervalInSeconds") or 0),
                     "cell_timeout": float(a.get("timeoutPerCellInSeconds") or 0) or None}
    ordered: list[dict[str, Any]] = []
    done: set[str] = set()
    while len(ordered) < len(out):
        ready = [a for a in out.values() if a["name"] not in done and all(d in done for d in a["dependencies"])]
        if not ready:
            missing = sorted({d for a in out.values() for d in a["dependencies"] if d not in out})
            raise ValueError(f"runMultiple: unknown dependency {', '.join(missing)}." if missing
                             else "runMultiple: the dependencies form a cycle.")
        for a in ready:
            ordered.append(a)
            done.add(a["name"])
    return ordered


def run_multiple(dag: Any, run: Run, timeout_seconds: float | None = None,
                 task: Callable[[str], ContextManager[Any]] | None = None) -> dict[str, Any]:
    """Run ``dag``'s notebooks through ``run``, in order; ``task(name)`` wraps each attempt (the
    notebooks' host uses it to know which job task is running). Returns ``{name: {"exitVal",
    "exception"}}``, or raises the RuntimeError described above."""
    ordered = activities(dag)
    if isinstance(dag, dict) and float(dag.get("concurrency") or 0) > 1:
        print("Note: on this PC the notebooks run one at a time, so concurrency doesn't apply.")
    whole = float((dag.get("timeoutInSeconds") if isinstance(dag, dict) else None) or timeout_seconds or 0)
    ends = time.monotonic() + whole if whole else None
    results: dict[str, Any] = {}
    failed: list[str] = []
    for a in ordered:
        blocked = [d for d in a["dependencies"] if d in failed]
        left = ends - time.monotonic() if ends is not None else None
        if blocked or (left is not None and left <= 0):
            why = f"{', '.join(blocked)} failed" if blocked else f"the DAG's timeoutInSeconds ({whole:g}) ran out"
            results[a["name"]] = {"exitVal": None, "exception": f"Skipped: {why}."}
            failed.append(a["name"])
            continue
        for attempt in range(a["retry"] + 1):
            left = ends - time.monotonic() if ends is not None else None
            if left is not None and left <= 0:
                # A run given no time left would run with no limit at all.
                results[a["name"]] = {"exitVal": None,
                                      "exception": f"Stopped: the DAG's timeoutInSeconds ({whole:g}) ran out."}
                break
            try:
                with task(a["name"]) if task else contextlib.nullcontext():
                    value = run(a["path"], left if left is not None else 0, a["args"], a["cell_timeout"])
                results[a["name"]] = {"exitVal": value, "exception": None}
                break
            except Exception as exc:  # noqa: BLE001 - recorded per activity, as Fabric does
                results[a["name"]] = {"exitVal": None, "exception": f"{type(exc).__name__}: {exc}"}
                if attempt < a["retry"]:
                    rest = ends - time.monotonic() if ends is not None else a["retry_interval"]
                    time.sleep(max(0.0, min(a["retry_interval"], rest)))
        if results[a["name"]]["exception"]:
            failed.append(a["name"])
    if failed:
        error = RuntimeError("runMultiple: " + "; ".join(f"{n}: {results[n]['exception']}" for n in failed))
        error.results = results  # type: ignore[attr-defined]
        raise error
    return results
