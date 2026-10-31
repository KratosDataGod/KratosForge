"""Adapters that run control-flow activities on this PC (ORCH-003).

Each adapter keeps its ORCH-002 static checks (``StaticAdapter``) and adds ``execute``, which gets
the activity and an ``ActivityContext`` from the runner (runner.py) and returns the activity's
output, or raises ``ActivityFailed``. Adapters never touch data, files or the network: they set
variables, choose branches, loop, wait, fail, filter, and run a child pipeline whose definition is
on this PC. Behaviour follows Microsoft's Data Factory documentation and is not compared with
Fabric yet. The Notebook adapter only reads its parameters; the run's notebook runner
(``NotebookRunner``, given by the engine) runs the notebook. Lookup and Get Metadata only evaluate
their dataset settings; the run's data reader (``DataReader``, given by the engine) reads the
lakehouse.
"""

from __future__ import annotations

import json
import keyword
from typing import Any, Callable, Protocol

from .definition import Activity
from .evaluate import MAX_ITEMS, ExpressionError, Scope, evaluate
from .registry import StaticAdapter

FOREACH_DEFAULT_BATCH = 20     # Data Factory's default when batchCount is left out
UNTIL_DEFAULT_TIMEOUT = 7 * 24 * 3600   # Data Factory's default: 7 days


class ActivityFailed(Exception):
    """The activity ends Failed with this error. The runner adds ``target`` and ``details``, so the
    error has the fields Fabric's ``activity('X').error`` has."""

    def __init__(self, message: str, code: str = "LocalActivityFailed", output: Any = None,
                 failure_type: str = "UserError"):
        super().__init__(message)
        self.error = {"errorCode": code, "message": message, "failureType": failure_type}
        self.output = output


class ActivityContext(Protocol):
    """What the runner gives an adapter for one activity run."""

    scope: Scope

    def run_list(self, activities: list[Activity], scope: Scope, label: str, until: float | None = None) -> str: ...

    def wait(self, seconds: float) -> None: ...

    def set_variable(self, name: str, value: Any, append: bool = False) -> None: ...

    def set_return_value(self, key: str, value: Any) -> None: ...

    def run_child(self, activity: Activity, key: str, parameters: dict[str, Any], wait: bool = True) -> dict[str, Any]: ...

    def run_notebook(self, activity: Activity, parameters: dict[str, Any]) -> dict[str, Any]: ...

    def read_data(self, activity: Activity, properties: dict[str, Any]) -> dict[str, Any]: ...

    def note(self, **fields: Any) -> None: ...

    def iterate(self, items: list[Any], batch: int, body: Callable[[int, Any], str]) -> list[str]: ...

    @property
    def wait_scale(self) -> float: ...

    def clock(self) -> float: ...


def _eval(value: Any, scope: Scope, what: str) -> Any:
    try:
        return evaluate(value, scope)
    except ExpressionError as exc:
        raise ActivityFailed(f"{what}: {exc}", "LocalExpressionError") from None


class LocalAdapter(StaticAdapter):
    can_execute = True
    container = False   # containers don't take one of the run's parallel slots

    def execute(self, activity: Activity, ctx: ActivityContext) -> Any:  # type: ignore[override]
        raise NotImplementedError


class SetVariable(LocalAdapter):
    def execute(self, activity: Activity, ctx: ActivityContext) -> Any:
        tp = activity.type_properties
        if tp.get("setSystemVariable"):
            # Fabric's pipeline return value: a list of {key, value: {type, content}}.
            entries = tp.get("value") if isinstance(tp.get("value"), list) else []
            out = {}
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("key"), str):
                    v = entry.get("value")
                    content = v.get("content") if isinstance(v, dict) else v
                    out[entry["key"]] = _eval(content, ctx.scope, f"return value {entry['key']}")
                    ctx.set_return_value(entry["key"], out[entry["key"]])
            return {"name": tp.get("variableName", "pipelineReturnValue"), "value": out}
        name = tp.get("variableName")
        value = _eval(tp.get("value"), ctx.scope, f"value of {name}")
        ctx.set_variable(name, value)
        return {"name": name, "value": value}


class AppendVariable(LocalAdapter):
    def execute(self, activity: Activity, ctx: ActivityContext) -> Any:
        tp = activity.type_properties
        name = tp.get("variableName")
        ctx.set_variable(name, _eval(tp.get("value"), ctx.scope, f"value for {name}"), append=True)
        return {}


class IfCondition(LocalAdapter):
    container = True

    def execute(self, activity: Activity, ctx: ActivityContext) -> Any:
        result = _eval(activity.type_properties.get("expression"), ctx.scope, "the condition")
        if not isinstance(result, bool):
            raise ActivityFailed(f"The condition returned {result!r}, not true or false.", "LocalExpressionError")
        branch = "ifTrueActivities" if result else "ifFalseActivities"
        ctx.note(branch=branch)
        outcome = ctx.run_list(activity.children.get(branch, []), ctx.scope, branch)
        if outcome == "failed":
            raise ActivityFailed(f"An activity in {branch} failed.", "LocalInnerActivityFailed")
        return {"ifExpressionResult": result}


class Switch(LocalAdapter):
    container = True

    def execute(self, activity: Activity, ctx: ActivityContext) -> Any:
        tp = activity.type_properties
        on = _eval(tp.get("on"), ctx.scope, "the switch value")
        on_text = on if isinstance(on, str) else str(on)
        branch = "defaultActivities"
        for i, case in enumerate(tp.get("cases") or []):
            if isinstance(case, dict) and case.get("value") == on_text:
                branch = f"cases[{i}]"
                break
        ctx.note(branch=branch)
        outcome = ctx.run_list(activity.children.get(branch, []), ctx.scope, branch)
        if outcome == "failed":
            raise ActivityFailed(f"An activity in {branch} failed.", "LocalInnerActivityFailed")
        return {}


class ForEach(LocalAdapter):
    container = True

    def execute(self, activity: Activity, ctx: ActivityContext) -> Any:
        tp = activity.type_properties
        items = _eval(tp.get("items"), ctx.scope, "items")
        if not isinstance(items, list):
            raise ActivityFailed(f"items returned {type(items).__name__}, not an array.", "LocalExpressionError")
        if len(items) > MAX_ITEMS:
            raise ActivityFailed(f"items has {len(items):,} entries; ForEach takes at most {MAX_ITEMS:,}.",
                                 "LocalForEachTooManyItems")
        batch = 1 if tp.get("isSequential") is True else tp.get("batchCount") or FOREACH_DEFAULT_BATCH
        batch = batch if isinstance(batch, int) and not isinstance(batch, bool) else FOREACH_DEFAULT_BATCH
        inner = activity.children.get("activities", [])
        outcomes = ctx.iterate(items, max(1, min(batch, 50)),
                               lambda i, item: ctx.run_list(inner, ctx.scope.child(item), f"iteration {i}"))
        ctx.note(iterations=len(items))
        failed = sum(1 for o in outcomes if o == "failed")
        if failed:
            raise ActivityFailed(f"{failed} of {len(items)} iterations failed.", "LocalInnerActivityFailed")
        return {}


class Until(LocalAdapter):
    container = True

    def __init__(self, *args: Any, max_iterations: int = 10_000, **kw: Any):
        super().__init__(*args, **kw)
        self.max_iterations = max_iterations

    def execute(self, activity: Activity, ctx: ActivityContext) -> Any:
        tp = activity.type_properties
        inner = activity.children.get("activities", [])
        timeout = _timespan(tp.get("timeout")) if isinstance(tp.get("timeout"), str) else UNTIL_DEFAULT_TIMEOUT
        deadline = ctx.clock() + timeout   # timeouts are real time; wait_scale shortens waits only
        for i in range(self.max_iterations):
            scope = ctx.scope.layer()
            outcome = ctx.run_list(inner, scope, f"iteration {i}", until=deadline)
            if ctx.clock() >= deadline:  # what was running in this iteration was cancelled at the timeout (#1316)
                ctx.note(iterations=i + 1)
                raise ActivityFailed("The Until loop reached its timeout.", "LocalUntilTimeout")
            if outcome == "failed":
                ctx.note(iterations=i + 1)
                raise ActivityFailed(f"An activity failed in iteration {i + 1}.", "LocalInnerActivityFailed")
            if outcome == "cancelled":
                ctx.note(iterations=i + 1)
                return {}
            done = _eval(tp.get("expression"), scope, "the Until condition")
            if not isinstance(done, bool):
                raise ActivityFailed(f"The Until condition returned {done!r}, not true or false.", "LocalExpressionError")
            if done:
                ctx.note(iterations=i + 1)
                return {}
            if ctx.clock() >= deadline:
                ctx.note(iterations=i + 1)
                raise ActivityFailed("The Until loop reached its timeout.", "LocalUntilTimeout")
        raise ActivityFailed(f"The Until loop ran {self.max_iterations} times without its condition becoming true.",
                             "LocalUntilTooManyIterations")


class Wait(LocalAdapter):
    def execute(self, activity: Activity, ctx: ActivityContext) -> Any:
        seconds = _eval(activity.type_properties.get("waitTimeInSeconds"), ctx.scope, "waitTimeInSeconds")
        if isinstance(seconds, str) and seconds.strip().isdigit():
            seconds = int(seconds)
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
            raise ActivityFailed(f"waitTimeInSeconds is {seconds!r}, not a number of seconds.", "LocalExpressionError")
        ctx.wait(seconds)
        return {}


class Fail(LocalAdapter):
    def execute(self, activity: Activity, ctx: ActivityContext) -> Any:
        tp = activity.type_properties
        message = _eval(tp.get("message"), ctx.scope, "message")
        code = _eval(tp.get("errorCode"), ctx.scope, "errorCode")
        raise ActivityFailed(str(message), str(code))


class Filter(LocalAdapter):
    def execute(self, activity: Activity, ctx: ActivityContext) -> Any:
        tp = activity.type_properties
        items = _eval(tp.get("items"), ctx.scope, "items")
        if not isinstance(items, list):
            raise ActivityFailed("items didn't return an array.", "LocalExpressionError")
        kept = []
        for item in items:
            keep = _eval(tp.get("condition"), ctx.scope.child(item), "the condition")
            if keep is True:
                kept.append(item)
            elif keep is not False:
                raise ActivityFailed(f"The condition returned {keep!r}, not true or false.", "LocalExpressionError")
        return {"ItemsCount": len(items), "FilteredItemsCount": len(kept), "Value": kept}


def child_key(activity: Activity) -> str:
    """How a child pipeline is named: ExecutePipeline's referenceName, InvokePipeline's pipelineId."""
    tp = activity.type_properties
    ref = tp.get("pipeline")
    if isinstance(ref, dict) and isinstance(ref.get("referenceName"), str):
        return ref["referenceName"]
    return tp.get("pipelineId") if isinstance(tp.get("pipelineId"), str) else ""


def notebook_key(activity: Activity) -> str:
    """How a Notebook activity names its notebook: the notebookId it runs."""
    value = activity.type_properties.get("notebookId")
    return value if isinstance(value, str) else ""


def notebook_value(name: str, value: Any, typ: Any) -> Any:
    """A Notebook activity parameter as the notebook gets it: Fabric's string, int, float or bool."""
    typ = str(typ or "string").lower()
    try:
        if typ == "string":
            return value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        if typ == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in ("true", "false"):
                return value.lower() == "true"
            raise ValueError(value)
        if typ == "int":
            if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                raise ValueError(value)
            return int(value)
        if typ == "float":
            if isinstance(value, bool):
                raise ValueError(value)
            return float(value)
    except (TypeError, ValueError):
        raise ActivityFailed(f"Parameter {name}: {value!r} isn't a valid {typ}.", "LocalExpressionError") from None
    raise ActivityFailed(f"Parameter {name}: type {typ!r} isn't one of string, int, float or bool.",
                         "LocalExpressionError")


class Notebook(LocalAdapter):
    """Runs a notebook on this PC through the run's notebook runner (the engine's clean kernel)."""

    def execute(self, activity: Activity, ctx: ActivityContext) -> Any:
        given = activity.type_properties.get("parameters") or {}
        if not isinstance(given, dict):
            raise ActivityFailed("parameters must be an object.", "LocalExpressionError")
        params = {}
        for name, spec in given.items():
            spec = spec if isinstance(spec, dict) else {"value": spec}
            if not name.isidentifier() or keyword.iskeyword(name):
                raise ActivityFailed(f"Parameter {name!r} isn't a Python name, so a notebook can't use it.",
                                     "LocalExpressionError")
            params[name] = notebook_value(name, _eval(spec.get("value"), ctx.scope, f"parameter {name}"), spec.get("type"))
        return ctx.run_notebook(activity, params)


class ReadData(LocalAdapter):
    """Lookup, Get Metadata and Script: their dataset settings and scripts are evaluated here (a
    ForEach often names the table with item()); the run's data reader does the reading."""

    def execute(self, activity: Activity, ctx: ActivityContext) -> Any:
        tp = activity.type_properties
        props = {k: _eval(tp.get(k), ctx.scope, k) for k in ("datasetSettings", "source", "firstRowOnly", "fieldList",
                                                             "scripts", "scriptBlockExecutionTimeout") if k in tp}
        if "linkedService" in activity.raw:   # Script names its connection on the activity itself
            props["linkedService"] = _eval(activity.raw["linkedService"], ctx.scope, "linkedService")
        return ctx.read_data(activity, props)


class ExecutePipeline(LocalAdapter):
    container = True

    def execute(self, activity: Activity, ctx: ActivityContext) -> Any:
        params = _eval(activity.type_properties.get("parameters") or {}, ctx.scope, "parameters")
        if not isinstance(params, dict):
            raise ActivityFailed("parameters must be an object.", "LocalExpressionError")
        wait = activity.type_properties.get("waitOnCompletion") is not False
        if not wait:
            ctx.note(wait_on_completion=False)
        return ctx.run_child(activity, child_key(activity), params, wait)


def _timespan(text: str) -> float:
    """d.hh:mm:ss or hh:mm:ss in seconds."""
    days, _, rest = text.rpartition(".") if text.count(":") == 2 and "." in text.split(":")[0] else ("", "", text)
    h, m, s = (int(x) for x in rest.split(":"))
    return (int(days) if days else 0) * 86400 + h * 3600 + m * 60 + s


def timespan(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return _timespan(value)
    except ValueError:
        return None

