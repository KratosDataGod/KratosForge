"""Run a Fabric pipeline on this PC (ORCH-003): a DAG runner over the ORCH-001 model.

A run first checks that every activity it could reach runs here: its registry entry is ``local``
(an adapter that executes, with tests) or the caller gave a mock for it. Anything else (Copy,
cloud-only and unknown activities) refuses the whole run before it starts, so nothing is sent to
Fabric. Notebook activities run only when the engine gives the run a ``NotebookRunner``
(pipeline_notebooks.py) that knows which notebook on this PC each one runs. Mocks stand in for
activities with a status and output the caller chooses, and the record says which activities
were mocked.

Inside each list of activities, an activity starts when every activity it depends on has
finished and each one's status matches the dependency's conditions (Succeeded, Failed, Skipped,
Completed); otherwise it is skipped. Independent activities run in parallel, up to
``max_parallel`` at once (containers don't take a slot). A list's outcome follows Data Factory's
documented rule: every leaf activity must succeed, and a skipped leaf is judged by the activities
it depends on. States: pending, running, succeeded, failed, skipped, cancelled.

Activity run ids are derived from the run id, so they are stable for a run. Records are written
by ``runs.RunStore``; a run that was going when the app stopped is marked interrupted there and
never resumed automatically.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from ..redact import describe
from .activities import default_registry
from .adapters import ActivityFailed, child_key, notebook_key, timespan
from .definition import DEFINITION_VERSION, Activity, DefinitionError, ImportedPipeline, loads
from .evaluate import Scope
from .registry import ActivityRegistry, CheckContext
from .validate import validate

_log = logging.getLogger(__name__)

MAX_DEPTH = 8            # child pipelines inside child pipelines
FINAL = ("succeeded", "failed", "skipped", "cancelled")
FABRIC_STATUS = {"succeeded": "Succeeded", "failed": "Failed", "skipped": "Skipped", "cancelled": "Cancelled",
                 "running": "InProgress", "pending": "Queued"}
HIDDEN = "(hidden)"
_TYPE_DEFAULTS = {"string": "", "securestring": "", "bool": False, "boolean": False, "array": [], "int": 0,
                  "integer": 0, "float": 0.0, "object": {}}
CHILD_TYPES = ("ExecutePipeline", "InvokePipeline")
NOTEBOOK_TYPE = "TridentNotebook"
DATA_TYPES = ("Lookup", "GetMetadata", "Script")


class NotebookRunner(Protocol):
    """Runs a pipeline's Notebook activities on this PC (the engine's pipeline_notebooks.py)."""

    def check(self, activity: Activity) -> list[dict[str, Any]]:
        """Why this activity's notebook can't run here; empty when it can. Runs nothing."""
        ...

    def run(self, activity: Activity, parameters: dict[str, Any], cancelled: Callable[[], bool],
            deadline: float | None) -> dict[str, Any]:
        """Run the notebook to the end and return the activity's output, or raise ActivityFailed."""
        ...

    def notes(self) -> list[str]:
        """What the run record should say about the notebooks that ran (where they read and wrote)."""
        ...

    def finish(self) -> None:
        """Clean up after the run's notebooks (their sandbox folders). Called once every step has
        ended and before the run reports its final state; calling it again does nothing."""
        ...


class DataReader(Protocol):
    """Runs a pipeline's Lookup and Get Metadata activities on this PC (the engine's pipeline_data.py)."""

    def check(self, activity: Activity) -> list[dict[str, Any]]:
        """Why this activity can't read its dataset here; empty when it can. Reads nothing."""
        ...

    def lookup(self, activity: Activity, properties: dict[str, Any]) -> dict[str, Any]:
        """Lookup's output from its evaluated typeProperties, or raise ActivityFailed."""
        ...

    def metadata(self, activity: Activity, properties: dict[str, Any]) -> dict[str, Any]:
        """Get Metadata's output from its evaluated typeProperties, or raise ActivityFailed."""
        ...

    def script(self, activity: Activity, properties: dict[str, Any], cancelled: Callable[[], bool],
               deadline: float | None) -> dict[str, Any]:
        """Script's output from its evaluated scripts and connection, or raise ActivityFailed."""
        ...


class RunRefused(Exception):
    """The run can't start; ``problems`` says why, activity by activity."""

    def __init__(self, problems: list[dict[str, Any]]):
        super().__init__("; ".join(p["message"] for p in problems))
        self.problems = problems


class _Cancelled(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _problem(code: str, message: str, activity: str | None = None, path: str = "$") -> dict[str, Any]:
    return {"code": code, "message": message, "activity": activity, "path": path}


def coerce(value: Any, declared: Any) -> Any:
    """A parameter value given as text (the CLI) turned into its declared type."""
    typ = str((declared or {}).get("type", "")).lower() if isinstance(declared, dict) else ""
    if not isinstance(value, str):
        return value
    try:
        if typ in ("int", "integer"):
            return int(value)
        if typ == "float":
            return float(value)
        if typ in ("bool", "boolean"):
            if value.lower() in ("true", "false"):
                return value.lower() == "true"
            raise ValueError(value)
        if typ in ("array", "object"):
            import json
            return json.loads(value)
    except ValueError:
        raise RunRefused([_problem("bad_parameter", f"{value!r} isn't a valid {typ}.")]) from None
    return value


def _mock_status(m: Mapping[str, Any]) -> str:
    return str(m.get("status", "Succeeded")).lower()


class PipelineRun:
    def __init__(self, imp: ImportedPipeline, *, parameters: dict[str, Any] | None = None,
                 mocks: dict[str, dict[str, Any]] | None = None, children: dict[str, Any] | None = None,
                 max_parallel: int = 4, wait_scale: float = 1.0, timeout_seconds: float | None = None,
                 run_id: str | None = None, registry: ActivityRegistry | None = None,
                 now: Callable[[], datetime] | None = None,
                 on_change: Callable[["PipelineRun"], None] | None = None,
                 depth: int = 0, cancel: threading.Event | None = None,
                 notebooks: NotebookRunner | None = None, detached: list[threading.Thread] | None = None,
                 data: DataReader | None = None):
        self.imp, self.pipeline = imp, imp.pipeline
        self.given = dict(parameters or {})
        self.mocks = {k: v for k, v in (mocks or {}).items() if isinstance(v, dict)}
        self.children = dict(children or {})
        self.notebooks = notebooks
        self.data = data
        self.max_parallel = max(1, min(int(max_parallel), 32))
        self.wait_scale = max(0.0, float(wait_scale))
        self.timeout_seconds = timeout_seconds
        self.id = run_id or str(uuid.uuid4())
        self.registry = registry or default_registry()
        self.on_change = on_change
        self.depth = depth
        self._cancel = cancel or threading.Event()
        # Child pipelines started with waitOnCompletion false, shared with every child run so the
        # top-level run waits for them all before it cleans up and ends.
        self._detached = detached if detached is not None else []
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._slots = threading.BoundedSemaphore(self.max_parallel)
        self.state, self.stop_reason = "pending", ""
        self.started = self.ended = ""
        self._t0 = 0.0
        self.seconds = 0.0
        self.records: list[dict[str, Any]] = []
        self.return_value: dict[str, Any] = {}
        self.problems: list[dict[str, Any]] = []
        self.parameters: dict[str, Any] = {}
        self.variables: dict[str, Any] = {}
        self.scope = Scope(self.parameters, self.variables, {}, {}, [], now or (lambda: datetime.now(timezone.utc)))
        try:
            self._ns = uuid.UUID(self.id)
        except ValueError:
            self._ns = uuid.uuid5(uuid.NAMESPACE_URL, self.id)

    # ---- before the run

    def _parameters(self) -> list[dict[str, Any]]:
        problems = []
        declared = self.pipeline.parameters
        for name in self.given:
            if name not in declared:
                problems.append(_problem("unknown_parameter", f"The pipeline has no parameter {name!r}."))
        for name, decl in declared.items():
            if name in self.given:
                try:
                    self.parameters[name] = coerce(self.given[name], decl)
                except RunRefused as exc:
                    problems += [p | {"message": f"Parameter {name}: {p['message']}"} for p in exc.problems]
            elif isinstance(decl, dict) and "defaultValue" in decl:
                self.parameters[name] = decl["defaultValue"]
            else:
                problems.append(_problem("missing_parameter", f"Parameter {name!r} has no default; give a value.",
                                         path=f"$.properties.parameters.{name}"))
        for name, decl in self.pipeline.variables.items():
            decl = decl if isinstance(decl, dict) else {}
            typ = str(decl.get("type", "String")).lower()
            default = decl.get("defaultValue", _TYPE_DEFAULTS.get(typ, None))
            self.variables[name] = list(default) if isinstance(default, list) else default
        return problems

    def check(self) -> list[dict[str, Any]]:
        """Why this run can't start; empty when it can. Nothing runs."""
        problems = [_problem(d.code, d.message, d.activity, d.path) for d in validate(self.imp) if d.severity == "error"]
        problems += self._parameters()
        names = {a.name for a in self.pipeline.all_activities()}
        for name in self.mocks:
            if name not in names:
                problems.append(_problem("unknown_mock", f"There is no activity {name!r} to mock."))
        ctx = CheckContext(self.pipeline.parameters, self.pipeline.variables)

        def visit(acts: list[Activity]) -> None:
            for a in acts:
                if a.inactive:
                    continue
                if a.name in self.mocks:
                    if _mock_status(self.mocks[a.name]) not in ("succeeded", "failed"):
                        problems.append(_problem("bad_mock", f"The mock for {a.name} must say Succeeded or Failed.",
                                                 a.name, a.path))
                    continue   # a mocked container's children don't run either
                entry = self.registry.get(a.type)
                if entry.classification != "local" or not getattr(entry.adapter, "can_execute", False):
                    why = {"cloud": "only runs in Fabric", "unsupported": "isn't known to this version"}.get(
                        entry.classification, "can't run on this PC yet")
                    problems.append(_problem("not_local", f"{a.name} ({a.type}) {why}. Give a mock for it, or run "
                                             "the pipeline in Fabric.", a.name, a.path))
                    continue
                for d in entry.adapter.validate(a, ctx):
                    if d.severity == "error":
                        problems.append(_problem(d.code, d.message, a.name, d.path))
                if a.type in CHILD_TYPES:
                    problems.extend(self._check_child(a))
                if entry.type == NOTEBOOK_TYPE:
                    if self.notebooks is None:
                        problems.append(_problem("notebook_needs_engine", f"{a.name} runs notebook "
                                                 f"{notebook_key(a) or '(no notebookId)'}. Notebook steps run when "
                                                 "you start the run and name the notebook (fabric-desktop pipeline "
                                                 "run --notebook); give a mock for it here.", a.name, a.path))
                    else:
                        problems.extend(self.notebooks.check(a))
                if entry.type in DATA_TYPES:
                    if self.data is None:
                        problems.append(_problem("data_needs_engine", f"{a.name} ({a.type}) reads a lakehouse or a "
                                                 "local data project. It runs when you start the run with lakehouse "
                                                 "reads on (fabric-desktop pipeline run --read-lakehouse) or name its "
                                                 "project (--sql-project); give a mock for it here.",
                                                 a.name, a.path))
                    else:
                        problems.extend(self.data.check(a))
                for kids in a.children.values():
                    visit(kids)

        visit(self.pipeline.activities)
        self.problems = problems
        return problems

    def _child_definition(self, key: str) -> ImportedPipeline | None:
        found = self.children.get(key)
        if isinstance(found, ImportedPipeline):
            return found
        if isinstance(found, str):
            return loads(found)
        return None

    def _check_child(self, a: Activity) -> list[dict[str, Any]]:
        key = child_key(a)
        if self.depth + 1 >= MAX_DEPTH:
            return [_problem("too_deep", f"{a.name}: child pipelines are nested more than {MAX_DEPTH} deep.", a.name, a.path)]
        try:
            child = self._child_definition(key)
        except DefinitionError as exc:
            return [_problem("child_definition", f"{a.name}: the definition given for {key!r} can't be read: {exc}",
                             a.name, a.path)]
        if child is None:
            return [_problem("child_definition_missing", f"{a.name} runs pipeline {key!r}; give its definition to run "
                             "it here, or a mock for this activity.", a.name, a.path)]
        # Parameters are expressions evaluated at run time, so only the child's activities are checked here.
        probe = PipelineRun(child, mocks=self.mocks, children=self.children, registry=self.registry,
                            depth=self.depth + 1, notebooks=self.notebooks, data=self.data)
        probe.given = {k: None for k in child.pipeline.parameters}
        return [p | {"message": f"In child pipeline {key}: {p['message']}"} for p in probe.check()
                if p["code"] not in ("missing_parameter", "bad_parameter", "unknown_mock")]

    # ---- running

    def run(self) -> "PipelineRun":
        """Run to the end in this thread. Raises RunRefused if ``check`` finds problems."""
        problems = self.check()
        if problems:
            self.state, self.stop_reason = "refused", "checks"
            raise RunRefused(problems)
        self.scope.system.update({"RunId": self.id, "Pipeline": self.pipeline.name, "PipelineName": self.pipeline.name,
                                  "TriggerTime": _now(), "TriggerType": "Manual", "TriggerId": self.id,
                                  "TriggerName": "KratosForge (local run)", "GroupId": self.id})
        self.state, self.started, self._t0 = "running", _now(), time.monotonic()
        timer = None
        if self.timeout_seconds:
            timer = threading.Timer(self.timeout_seconds, self._time_up)
            timer.daemon = True
            timer.start()
        self._changed()
        try:
            outcome = self._run_list(self.pipeline.activities, self.scope, "", None)
            if self.depth == 0:
                self._join_detached()
        finally:
            if timer:
                timer.cancel()
        if self.notebooks is not None and self.depth == 0:
            # Every step has ended; clean up before the final state, so a caller that sees the run
            # end also sees its folders gone. Child pipelines share the top-level run's notebooks and
            # folder, so only the top level cleans up. (On an error the caller stops the steps, then finishes.)
            self.notebooks.finish()
        with self._lock:
            if self.stop_reason == "timed_out":
                self.state = "failed"
            elif self._cancel.is_set():
                self.state, self.stop_reason = "cancelled", self.stop_reason or "cancelled"
            else:
                self.state = "succeeded" if outcome == "succeeded" else "failed"
            self.ended, self.seconds = _now(), round(time.monotonic() - self._t0, 3)
        self._changed()
        return self

    def _join_detached(self) -> None:
        """Wait for the child runs that nothing waited on (a detached child may start more)."""
        while self._detached:
            self._detached.pop(0).join()

    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            if not self._cancel.is_set():
                self.stop_reason = self.stop_reason or reason
            self._cancel.set()
            self._cond.notify_all()

    def _time_up(self) -> None:
        self.cancel("timed_out")

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change(self)
            except Exception as exc:  # noqa: BLE001 - a failed save must not stop the run
                _log.warning("Couldn't save the run's progress; the run goes on: %s", describe(exc))

    def _run_list(self, acts: list[Activity], scope: Scope, label: str, parent: str | None,
                  until: float | None = None) -> str:
        """Run one list of sibling activities; returns succeeded, failed or cancelled. ``until`` is an
        enclosing Until's deadline (time.monotonic()): at it, what's running is cancelled and what
        hasn't started doesn't start (#1316)."""
        names = {a.name for a in acts}
        status: dict[str, str] = {}
        threads: list[threading.Thread] = []
        with self._cond:
            while True:
                for a in acts:
                    if a.name in status:
                        continue
                    deps = [d for d in a.depends_on if d.activity in names]
                    if any(status.get(d.activity) in (None, "running") for d in deps):
                        continue
                    if self.cancelled or _past(until):
                        status[a.name] = "cancelled"
                        self._record(a, label, parent, "cancelled", executed_by="not_started")
                        self._cond.notify_all()
                    elif any(status[d.activity] == "cancelled" for d in deps):
                        status[a.name] = "cancelled"
                        self._record(a, label, parent, "cancelled", executed_by="not_started")
                    elif not all(_matches(status[d.activity], d.conditions) for d in deps):
                        status[a.name] = "skipped"
                        self._record(a, label, parent, "skipped", executed_by="not_started")
                        self._cond.notify_all()
                    else:
                        status[a.name] = "running"
                        t = threading.Thread(target=self._execute, args=(a, scope, label, parent, status, until),
                                             name=f"pipeline-{a.name}", daemon=True)
                        threads.append(t)
                        t.start()
                if len(status) == len(acts) and "running" not in status.values():
                    break
                self._cond.wait(0.2)
        for t in threads:
            t.join()
        if any(v == "cancelled" for v in status.values()) and self.cancelled:
            return "cancelled"
        return "succeeded" if _leaves_succeed(acts, status) else "failed"

    def _execute(self, a: Activity, scope: Scope, label: str, parent: str | None, status: dict[str, str],
                 until: float | None = None) -> None:
        rec = self._record(a, label, parent, "running")
        final = "failed"
        try:
            final = self._attempts(a, scope, rec, until)
        except Exception as exc:  # noqa: BLE001 - an adapter bug fails the activity, not the run
            rec["error"] = {"errorCode": "LocalAdapterError", "message": f"{type(exc).__name__}: {exc}",
                            "failureType": "SystemError"}
        finally:
            if isinstance(rec.get("error"), dict):
                rec["error"] = {"failureType": "UserError", **rec["error"], "target": a.name, "details": ""}
            with self._cond:
                rec["state"], rec["status"] = final, FABRIC_STATUS[final]
                rec["ended"] = _now()
                rec["seconds"] = round(time.monotonic() - rec.pop("_t0"), 3)
                result = {"status": FABRIC_STATUS[final], "output": rec.get("output"), "error": rec.get("error"),
                          "activityRunId": rec["id"]}
                scope.activities[a.name] = result
                status[a.name] = final
                self._cond.notify_all()
            self._changed()

    def _attempts(self, a: Activity, scope: Scope, rec: dict[str, Any], until: float | None = None) -> str:
        if a.inactive:
            mark = str(a.raw.get("onInactiveMarkAs", "Succeeded")).lower()
            rec["executed_by"] = "inactive"
            return mark if mark in ("succeeded", "failed", "skipped") else "succeeded"
        mock = self.mocks.get(a.name)
        adapter = self.registry.get(a.type).adapter
        leaf = mock is not None or not getattr(adapter, "container", False)
        rec["executed_by"] = "mock" if mock is not None else "adapter"
        policy = a.raw.get("policy") if isinstance(a.raw.get("policy"), dict) else {}
        retries = policy.get("retry", 0) if leaf and isinstance(policy.get("retry"), int) else 0
        interval = policy.get("retryIntervalInSeconds", 30) if isinstance(policy.get("retryIntervalInSeconds"), int) else 30
        limit = timespan(policy.get("timeout")) if leaf else None
        if leaf:
            while not self._slots.acquire(timeout=0.2):
                if self.cancelled:
                    return "cancelled"
        try:
            for attempt in range(retries + 1):
                rec["attempts"] = attempt + 1
                deadline = None if limit is None else time.monotonic() + limit
                if until is not None:  # an enclosing Until's timeout stops this activity too
                    deadline = until if deadline is None else min(deadline, until)
                ctx = _Context(self, a, scope, rec, deadline, until)
                try:
                    output = _run_mock(mock, ctx) if mock is not None else adapter.execute(a, ctx)
                    rec["output"] = HIDDEN if policy.get("secureOutput") is True else output
                    rec.pop("error", None)
                    return "succeeded"
                except ActivityFailed as exc:
                    if _past(until):  # stopped by the Until's timeout, not by a failure of its own
                        rec.pop("error", None)
                        return "cancelled"
                    rec["error"] = exc.error
                    if exc.output is not None:
                        rec["output"] = exc.output
                    if attempt < retries and not self.cancelled:
                        try:
                            _Context(self, a, scope, rec, until, until).wait(interval)
                        except _Cancelled:
                            return "cancelled"
                        continue
                    return "failed"
                except _Cancelled:
                    return "cancelled"
            return "failed"
        finally:
            if leaf:
                self._slots.release()

    def _record(self, a: Activity, label: str, parent: str | None, state: str, executed_by: str = "") -> dict[str, Any]:
        rid = str(uuid.uuid5(self._ns, f"{parent or ''}|{label}|{a.path}"))
        rec: dict[str, Any] = {"id": rid, "name": a.name, "type": a.type, "path": a.path, "parent": parent,
                               "label": label, "state": state, "status": FABRIC_STATUS.get(state, state),
                               "executed_by": executed_by, "started": _now() if state == "running" else "",
                               "ended": "" if state == "running" else _now(), "attempts": 0}
        if state == "running":
            rec["_t0"] = time.monotonic()
        with self._lock:
            self.records.append(rec)
        if state != "running":
            self._changed()
        return rec

    # ---- the record

    def to_json(self, values: bool = True) -> dict[str, Any]:
        """The run record. ``values=False`` leaves out outputs, variables and parameter values (for
        callers that may only read metadata)."""
        secure = {n for n, d in self.pipeline.parameters.items()
                  if isinstance(d, dict) and str(d.get("type", "")).lower() == "securestring"}
        with self._lock:
            acts = []
            for r in self.records:
                row = {k: v for k, v in r.items() if not k.startswith("_")}
                if not values:
                    row.pop("output", None)
                    row.pop("child_run", None)
                    if isinstance(row.get("error"), dict):
                        row["error"] = {"errorCode": row["error"].get("errorCode")}
                acts.append(row)
            out: dict[str, Any] = {
                "run_id": self.id, "pipeline": self.pipeline.name, "state": self.state, "stop_reason": self.stop_reason,
                "started": self.started, "ended": self.ended, "seconds": self.seconds,
                "where": "this_pc", "verified_against_fabric": False,
                "registry_version": self.registry.version, "definition_version": DEFINITION_VERSION,
                "options": {"max_parallel": self.max_parallel, "wait_scale": self.wait_scale,
                            "timeout_seconds": self.timeout_seconds, "mocked": sorted(self.mocks),
                            "children": sorted(self.children)},
                "counts": {s: sum(1 for r in self.records if r["state"] == s) for s in FINAL + ("running",)},
                "activities": acts, "problems": list(self.problems),
                "notes": (self.notebooks.notes() if self.notebooks is not None else [])
                or ["Ran on this PC; no Fabric job was started."],
            }
            if any(r["type"] in DATA_TYPES and r.get("executed_by") == "adapter" and r.get("started") for r in self.records):
                out["notes"].append("Lookup, Get Metadata and Script steps ran here; those that read a lakehouse "
                                    "read OneLake, which uses a little capacity.")
            out["notes"].append("Behaviour follows Microsoft's Data Factory documentation and hasn't been compared "
                                "with Fabric.")
            if values:
                out["parameters"] = {k: HIDDEN if k in secure else v for k, v in self.parameters.items()}
                out["variables"] = dict(self.variables)
                out["return_value"] = dict(self.return_value)
            else:
                out["parameters"] = sorted(self.parameters)
        return out


def _matches(state: str, conditions: list[str]) -> bool:
    wanted = {str(c).lower() for c in conditions} or {"succeeded"}
    if state == "succeeded":
        return bool(wanted & {"succeeded", "completed"})
    if state == "failed":
        return bool(wanted & {"failed", "completed"})
    if state == "skipped":
        return "skipped" in wanted
    return False


def _leaves_succeed(acts: list[Activity], status: dict[str, str]) -> bool:
    """Data Factory's rule: every leaf must succeed; a skipped leaf is judged by its parents."""
    names = {a.name for a in acts}
    by_name = {a.name: a for a in acts}
    depended = {d.activity for a in acts for d in a.depends_on if d.activity in names}

    def ok(name: str, seen: frozenset[str]) -> bool:
        s = status.get(name)
        if s == "succeeded":
            return True
        if s == "skipped" and name not in seen:
            parents = [d.activity for d in by_name[name].depends_on if d.activity in names]
            return all(ok(p, seen | {name}) for p in parents) if parents else True
        return False

    return all(ok(a.name, frozenset()) for a in acts if a.name not in depended)


def _past(until: float | None) -> bool:
    """Whether an enclosing Until's deadline has passed."""
    return until is not None and time.monotonic() >= until


def _run_mock(mock: Mapping[str, Any], ctx: "_Context") -> Any:
    seconds = mock.get("seconds", 0)
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0:
        ctx.wait(seconds)
    if _mock_status(mock) == "failed":
        given = mock.get("error")
        error: dict[str, Any] = given if isinstance(given, dict) else {}
        raise ActivityFailed(str(error.get("message", "Failed (mock)")), str(error.get("errorCode", "Mock")),
                             mock.get("output"), str(error.get("failureType", "UserError")))
    return mock.get("output", {})


class _Context:
    """What one activity run can do (see adapters.ActivityContext)."""

    def __init__(self, run: PipelineRun, activity: Activity, scope: Scope, rec: dict[str, Any], deadline: float | None,
                 until: float | None = None):
        self.run, self.activity, self.scope, self.rec, self.deadline = run, activity, scope, rec, deadline
        self.until = until  # an enclosing Until's deadline, which this activity's own run_list keeps

    @property
    def wait_scale(self) -> float:
        return self.run.wait_scale

    def clock(self) -> float:
        return time.monotonic()

    def wait(self, seconds: float) -> None:
        end = time.monotonic() + max(0.0, seconds) * self.run.wait_scale
        timed_out = self.deadline is not None and self.deadline < end
        end = min(end, self.deadline) if self.deadline is not None else end
        while True:
            left = end - time.monotonic()
            if self.run.cancelled or _past(self.until):
                raise _Cancelled()
            if left <= 0:
                break
            self.run._cancel.wait(min(left, 0.2))
        if timed_out:
            raise ActivityFailed("The activity reached its policy timeout.", "LocalActivityTimeout")

    def note(self, **fields: Any) -> None:
        with self.run._lock:
            self.rec.update(fields)

    def run_list(self, activities: list[Activity], scope: Scope, label: str, until: float | None = None) -> str:
        if self.run.cancelled:
            raise _Cancelled()
        if self.until is not None:
            until = self.until if until is None else min(until, self.until)
        outcome = self.run._run_list(activities, scope, label, self.rec["id"], until)
        if outcome == "cancelled":
            raise _Cancelled()
        return outcome

    def iterate(self, items: list[Any], batch: int, body: Callable[[int, Any], str]) -> list[str]:
        outcomes: list[str] = ["cancelled"] * len(items)
        gate = threading.BoundedSemaphore(batch)
        threads = []

        def one(i: int, item: Any) -> None:
            try:
                outcomes[i] = body(i, item)
            except _Cancelled:
                outcomes[i] = "cancelled"
            finally:
                gate.release()

        for i, item in enumerate(items):
            while not gate.acquire(timeout=0.2):
                if self.run.cancelled:
                    break
            if self.run.cancelled:
                break
            t = threading.Thread(target=one, args=(i, item), daemon=True, name=f"foreach-{i}")
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        if self.run.cancelled:
            raise _Cancelled()
        return outcomes

    def set_variable(self, name: str, value: Any, append: bool = False) -> None:
        with self.run._lock:
            if name not in self.run.variables:
                raise ActivityFailed(f"Variable {name!r} isn't declared.", "LocalUndeclaredVariable")
            if append:
                current = self.run.variables[name]
                if not isinstance(current, list):
                    raise ActivityFailed(f"Variable {name!r} isn't an array, so nothing can be appended.",
                                         "LocalVariableType")
                current.append(value)
            else:
                self.run.variables[name] = value

    def set_return_value(self, key: str, value: Any) -> None:
        with self.run._lock:
            self.run.return_value[key] = value

    def run_child(self, activity: Activity, key: str, parameters: dict[str, Any], wait: bool = True) -> dict[str, Any]:
        child_imp = self.run._child_definition(key)
        if child_imp is None:
            raise ActivityFailed(f"No definition was given for child pipeline {key!r}.", "LocalChildMissing")
        child = PipelineRun(child_imp, parameters=parameters, mocks=self.run.mocks, children=self.run.children,
                            max_parallel=self.run.max_parallel, wait_scale=self.run.wait_scale,
                            registry=self.run.registry, now=self.run.scope.now, depth=self.run.depth + 1,
                            cancel=self.run._cancel, notebooks=self.run.notebooks, detached=self.run._detached,
                            data=self.run.data,
                            run_id=str(uuid.uuid5(self.run._ns, f"child|{self.rec['id']}")))
        if not wait:
            return self._start_child(child, key)
        try:
            child.run()
        except RunRefused as exc:
            raise ActivityFailed(f"Child pipeline {key} can't run: {exc}", "LocalChildRefused") from None
        finally:
            self.note(child_run=child.to_json())
        if child.state == "cancelled":
            raise _Cancelled()
        output = {"pipelineRunId": child.id, "pipelineReturnValue": dict(child.return_value)}
        if child.state != "succeeded":
            raise ActivityFailed(f"Child pipeline {key} failed.", "LocalChildFailed", output)
        return output

    def _start_child(self, child: PipelineRun, key: str) -> dict[str, Any]:
        """waitOnCompletion false: start the child and succeed at once, as Fabric does. The child's
        result goes in this activity's record when it ends and doesn't change this run's result."""
        problems = child.check()
        if problems:
            raise ActivityFailed(f"Child pipeline {key} can't run: {RunRefused(problems)}", "LocalChildRefused")

        def go() -> None:
            try:
                child.run()
            except Exception as exc:  # noqa: BLE001 - nothing waits on this child; its record says what happened
                _log.warning("Child pipeline %s stopped: %s", key, describe(exc))
            finally:
                self.note(child_run=child.to_json())
                self.run._changed()

        t = threading.Thread(target=go, name=f"pipeline-child-{child.id[:8]}", daemon=True)
        t.start()
        self.run._detached.append(t)
        self.note(child_run=child.to_json())
        return {"pipelineRunId": child.id}

    def read_data(self, activity: Activity, properties: dict[str, Any]) -> dict[str, Any]:
        if self.run.data is None:
            raise ActivityFailed(f"{activity.name} can't read a lakehouse without the engine.", "LocalDataUnsupported")
        if activity.type == "Script":
            try:
                output = self.run.data.script(activity, properties, lambda: self.run.cancelled, self.deadline)
            except ActivityFailed:
                if self.run.cancelled:
                    raise _Cancelled() from None
                raise
            if self.run.cancelled:
                raise _Cancelled()
            return output
        read = self.run.data.lookup if activity.type == "Lookup" else self.run.data.metadata
        return read(activity, properties)

    def run_notebook(self, activity: Activity, parameters: dict[str, Any]) -> dict[str, Any]:
        if self.run.notebooks is None:
            raise ActivityFailed(f"Notebook {notebook_key(activity)} can't run without the engine.", "LocalNotebookMissing")
        try:
            output = self.run.notebooks.run(activity, parameters, lambda: self.run.cancelled, self.deadline)
        except ActivityFailed:
            if self.run.cancelled:
                raise _Cancelled() from None
            raise
        if self.run.cancelled:
            raise _Cancelled()
        return output
