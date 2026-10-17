"""Checks on a pipeline definition, each problem with the activity and JSON path it is about.

Severity:
    error    Fabric would refuse the definition or a run would fail (a dependency on an activity
             that doesn't exist, a cycle, a variable that isn't declared)
    warning  probably a mistake, but the definition still loads (an expression that refers to an
             activity that may not have run yet, an expression that can't be read)
    info     worth knowing (inactive activities, duplicate JSON keys)

Unknown activity types are never a problem here: the compatibility registry (ORCH-002) says what
Fabric Desktop can do with each type. Nothing is evaluated or run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import expressions
from .definition import CHILD_LISTS, CONDITIONS, Activity, ImportedPipeline, Pipeline

PARAMETER_TYPES = {"string", "int", "float", "bool", "array", "object", "securestring"}
VARIABLE_TYPES = {"string", "bool", "boolean", "array", "integer"}
SYSTEM_VARIABLES = {"pipelineReturnValue"}  # Fabric's Set variable can set the pipeline's return value


@dataclass
class Diagnostic:
    code: str
    severity: str
    message: str
    path: str
    activity: str | None = None
    hint: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "message": self.message, "path": self.path,
                "activity": self.activity, "hint": self.hint}


def validate(target: ImportedPipeline | Pipeline) -> list[Diagnostic]:
    imp = target if isinstance(target, ImportedPipeline) else None
    p = target.pipeline if isinstance(target, ImportedPipeline) else target
    out: list[Diagnostic] = [Diagnostic(e.code, "error", str(e), e.path, e.activity, e.hint) for e in p.problems]
    if imp is not None and imp.duplicate_keys:
        out.append(Diagnostic("duplicate_json_key", "warning",
                              f"The JSON repeats {', '.join(repr(k) for k in imp.duplicate_keys[:10])}; only the last "
                              "value of each is used.", "$",
                              hint="Remove the repeated key so the definition says one thing."))
    if not p.properties:
        return out
    _declarations(p, out)
    acts = p.all_activities()
    _names(acts, out)
    by_container = _containers(p)
    for siblings in by_container.values():
        _dependencies(siblings, out)
    _references(p, acts, by_container, out)
    for a in acts:
        if a.inactive:
            out.append(Diagnostic("inactive_activity", "info", f"{a.name} is deactivated, so a run skips it "
                                  f"and marks it {a.raw.get('onInactiveMarkAs') or 'Succeeded'}.", f"{a.path}.state", a.name))
    order = {"error": 0, "warning": 1, "info": 2}
    out.sort(key=lambda d: order.get(d.severity, 3))
    return out


def _declarations(p: Pipeline, out: list[Diagnostic]) -> None:
    for kind, decls, types in (("parameter", p.properties.get("parameters"), PARAMETER_TYPES),
                               ("variable", p.properties.get("variables"), VARIABLE_TYPES)):
        path = f"$.properties.{kind}s"
        if decls is None:
            continue
        if not isinstance(decls, dict):
            out.append(Diagnostic(f"invalid_{kind}s", "error", f"{kind}s must be an object of name to declaration.", path))
            continue
        for name, decl in decls.items():
            dpath = f"{path}{expressions.key_path(name)}"
            if not isinstance(decl, dict) or not isinstance(decl.get("type"), str):
                out.append(Diagnostic(f"invalid_{kind}", "error", f"The {kind} {name} has no type.", dpath,
                                      hint='Give it a type, e.g. {"type": "String"}.'))
            elif decl["type"].lower() not in types:
                out.append(Diagnostic(f"unknown_{kind}_type", "warning", f"The {kind} {name} has type {decl['type']!r}, "
                                      "which this reader doesn't know.", f"{dpath}.type"))


def _names(acts: list[Activity], out: list[Diagnostic]) -> None:
    seen: dict[str, Activity] = {}
    for a in acts:
        if not a.name:
            out.append(Diagnostic("missing_name", "error", "An activity has no name.", f"{a.path}.name",
                                  hint="Every activity needs a name that no other activity in the pipeline has."))
        elif a.name in seen:
            first = seen[a.name]
            out.append(Diagnostic("duplicate_activity_name", "error",
                                  f"Two activities are called {a.name} (the other is at {first.path}).", f"{a.path}.name",
                                  a.name, hint="Activity names must be unique in the whole pipeline, nested ones included."))
        else:
            seen[a.name] = a
        if not a.type:
            out.append(Diagnostic("missing_type", "error", f"{a.name or 'An activity'} has no type.", f"{a.path}.type",
                                  a.name or None))


def _containers(p: Pipeline) -> dict[tuple[str | None, str | None], list[Activity]]:
    """Activities grouped by the list they're in: dependencies only work between siblings."""
    groups: dict[tuple[str | None, str | None], list[Activity]] = {(None, None): list(p.activities)}
    for a in p.all_activities():
        for branch, kids in a.children.items():
            groups[(a.name, branch)] = kids
    return groups


def _dependencies(siblings: list[Activity], out: list[Diagnostic]) -> None:
    names = {a.name for a in siblings if a.name}
    for a in siblings:
        for d in a.depends_on:
            if d.activity == a.name:
                out.append(Diagnostic("self_dependency", "error", f"{a.name} depends on itself.", f"{d.path}.activity",
                                      a.name, hint="Remove this dependency."))
            elif d.activity not in names:
                where = f" inside {a.container}" if a.container else ""
                out.append(Diagnostic("missing_dependency", "error",
                                      f"{a.name} depends on {d.activity}, which isn't an activity{where} at this level.",
                                      f"{d.path}.activity", a.name,
                                      hint="An activity can only depend on activities in the same list "
                                           "(the same pipeline, or the same branch of a container)."))
            if not d.conditions:
                out.append(Diagnostic("missing_dependency_condition", "error",
                                      f"{a.name}'s dependency on {d.activity} has no condition.",
                                      f"{d.path}.dependencyConditions", a.name,
                                      hint=f"Use one or more of {', '.join(CONDITIONS)}."))
            for k, cond in enumerate(d.conditions):
                if cond not in CONDITIONS:
                    out.append(Diagnostic("unknown_dependency_condition", "error",
                                          f"{a.name} waits for {d.activity} to be {cond!r}, which isn't a condition.",
                                          f"{d.path}.dependencyConditions[{k}]", a.name,
                                          hint=f"Use one of {', '.join(CONDITIONS)}."))
    for cycle in _cycles(siblings):
        first = next(a for a in siblings if a.name == cycle[0])
        out.append(Diagnostic("dependency_cycle", "error", "These activities wait for each other, so none can start: "
                              + " -> ".join(cycle + [cycle[0]]) + ".", f"{first.path}.dependsOn", cycle[0],
                              hint="Remove one of the dependencies in the loop."))


def _cycles(siblings: list[Activity]) -> list[list[str]]:
    """Each dependency loop once (self-dependencies are reported separately)."""
    graph = {a.name: [d.activity for d in a.depends_on if d.activity != a.name] for a in siblings if a.name}
    state: dict[str, int] = {}
    stack: list[str] = []
    found: list[list[str]] = []
    keys: set[frozenset[str]] = set()

    def visit(n: str) -> None:
        state[n] = 1
        stack.append(n)
        for m in graph.get(n, []):
            if m not in graph:
                continue
            if state.get(m) == 1:
                loop = stack[stack.index(m):]
                if frozenset(loop) not in keys:
                    keys.add(frozenset(loop))
                    found.append(list(reversed(loop)))
            elif m not in state:
                visit(m)
        stack.pop()
        state[n] = 2

    for n in graph:
        if n not in state:
            visit(n)
    return found


def _upstream(a: Activity, siblings: dict[str, Activity]) -> set[str]:
    seen: set[str] = set()
    todo = [d.activity for d in a.depends_on]
    while todo:
        n = todo.pop()
        if n in seen or n not in siblings:
            continue
        seen.add(n)
        todo += [d.activity for d in siblings[n].depends_on]
    return seen


def _references(p: Pipeline, acts: list[Activity], groups: dict[tuple[str | None, str | None], list[Activity]],
                out: list[Diagnostic]) -> None:
    params, variables = set(p.parameters), set(p.variables)
    by_name = {a.name: a for a in acts if a.name}
    sibling_maps = {key: {a.name: a for a in kids if a.name} for key, kids in groups.items()}

    def visible(a: Activity) -> set[str]:
        """Activities whose output ``a`` can read: everything upstream of it and of each container
        it's in, plus what it contains itself (an Until's condition reads its own activities)."""
        names = {d.name for d in a.walk()} - {a.name}
        cur: Activity | None = a
        while cur is not None:
            names |= _upstream(cur, sibling_maps.get((cur.container, cur.branch), {}))
            cur = by_name.get(cur.container) if cur.container else None
        return names

    for a in acts:
        tp = a.raw.get("typeProperties")
        if a.type in ("SetVariable", "AppendVariable") and isinstance(tp, dict):
            var = tp.get("variableName")
            system = tp.get("setSystemVariable") is True
            if not isinstance(var, str) or not var:
                out.append(Diagnostic("missing_variable_name", "error", f"{a.name} doesn't say which variable to set.",
                                      f"{a.path}.typeProperties.variableName", a.name))
            elif var not in variables and not (system and var in SYSTEM_VARIABLES):
                out.append(Diagnostic("undeclared_variable", "error", f"{a.name} sets {var}, which isn't declared.",
                                      f"{a.path}.typeProperties.variableName", a.name,
                                      hint=f"Add {var} under the pipeline's variables."))
        can_see: set[str] | None = None
        for expr in expressions.scan(own_json(a), a.path):
            if expr.problem:
                out.append(Diagnostic("unreadable_expression", "warning", f"An expression in {a.name} can't be read: "
                                      f"{expr.problem}.", expr.path, a.name))
            for ref in expr.references:
                if ref.kind == "activity":
                    if ref.name not in by_name:
                        out.append(Diagnostic("unknown_activity_reference", "error",
                                              f"{a.name} reads the output of {ref.name}, which isn't in the pipeline.",
                                              expr.path, a.name))
                        continue
                    can_see = visible(a) if can_see is None else can_see
                    if ref.name not in can_see:
                        out.append(Diagnostic("activity_not_upstream", "warning",
                                              f"{a.name} reads the output of {ref.name}, but doesn't wait for it, so "
                                              "it may not have run yet.", expr.path, a.name,
                                              hint=f"Make {a.name} depend on {ref.name}."))
                elif ref.kind == "variable" and ref.name not in variables:
                    out.append(Diagnostic("undeclared_variable", "error",
                                          f"{a.name} reads the variable {ref.name}, which isn't declared.",
                                          expr.path, a.name, hint=f"Add {ref.name} under the pipeline's variables."))
                elif ref.kind == "parameter" and ref.name not in params:
                    out.append(Diagnostic("undeclared_parameter", "error",
                                          f"{a.name} reads the parameter {ref.name}, which isn't declared.",
                                          expr.path, a.name, hint=f"Add {ref.name} under the pipeline's parameters."))
                elif ref.kind == "item" and not _in_foreach(a, by_name):
                    out.append(Diagnostic("item_outside_foreach", "warning",
                                          f"{a.name} uses item(), which only has a value inside a ForEach.",
                                          expr.path, a.name))


def own_json(a: Activity) -> dict[str, Any]:
    """The activity's JSON without the activities it contains (they're checked on their own), with
    list positions kept so JSON paths stay right."""
    own = {k: v for k, v in a.raw.items() if k != "typeProperties"}
    tp = a.raw.get("typeProperties")
    if isinstance(tp, dict):
        inner = {k: v for k, v in tp.items() if k not in CHILD_LISTS and k != "cases"}
        if isinstance(tp.get("cases"), list):
            inner["cases"] = [{k: v for k, v in c.items() if k != "activities"} if isinstance(c, dict) else c
                              for c in tp["cases"]]
        own["typeProperties"] = inner
    return own


def _in_foreach(a: Activity, by_name: dict[str, Activity]) -> bool:
    if a.type == "ForEach":  # its own items expression can't use item(), but its children can
        return False
    cur = by_name.get(a.container) if a.container else None
    while cur is not None:
        if cur.type == "ForEach":
            return True
        cur = by_name.get(cur.container) if cur.container else None
    return False


def summary(diagnostics: list[Diagnostic]) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for d in diagnostics:
        counts[d.severity] = counts.get(d.severity, 0) + 1
    return counts
