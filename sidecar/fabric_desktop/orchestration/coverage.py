"""What a local pipeline run does with each Data Factory activity type and expression function.

``docs/orchestration/coverage.md`` is ``markdown(coverage())``. tests/test_pipeline_coverage.py
fails when the page is stale, so a change to the runner's registry or its expression functions
shows up there.

Activities come from the runner's own registry (activities.default_registry), one row per type:

    runs locally   a tested adapter runs it on this PC (registry ``local``)
    simulated      it runs against fixtures or a stand-in, with no real side effects
    refused        a local run can't run it: it is checked (structure, required properties) and
                   needs a mock (registry ``static``), or its type is unknown here
    Fabric only    it only runs in Fabric or the Azure service it calls (registry ``cloud``)

Expression functions are every function in Microsoft's pipeline expression reference (FUNCTIONS
below, as the Fabric and Azure Data Factory pages list them), plus the scope functions. Each
function the evaluator has gets the reference's own example run through ``evaluate`` and compared
with the value the reference gives:

    runs           it gives the reference's value for the example
    differs        it runs and gives another value: check results that use it in Fabric
    not checked    its value changes from run to run (guid, rand, utcNow), so there is no value check
    refused        the evaluator stops the activity with "<name>() isn't supported in local runs yet"
"""

from __future__ import annotations

import json
from typing import Any

from . import evaluate

# Microsoft's expression reference: https://learn.microsoft.com/fabric/data-factory/expression-language
# (ms.date 2024-12-18) and https://learn.microsoft.com/azure/data-factory/control-flow-expression-language-functions
# (ms.date 2025-02-13). The two list the same functions; the Fabric page's table leaves out json but
# documents it. Read 2026-11-05.
REFERENCE = "https://learn.microsoft.com/fabric/data-factory/expression-language"
FUNCTIONS: dict[str, tuple[str, ...]] = {
    "String": ("concat", "endsWith", "guid", "indexOf", "lastIndexOf", "replace", "split", "startsWith",
               "substring", "toLower", "toUpper", "trim"),
    "Collection": ("contains", "empty", "first", "intersection", "join", "last", "length", "skip", "take",
                   "union"),
    "Logical": ("and", "equals", "greater", "greaterOrEquals", "if", "less", "lessOrEquals", "not", "or"),
    "Conversion": ("array", "base64", "base64ToBinary", "base64ToString", "binary", "bool", "coalesce",
                   "createArray", "dataUri", "dataUriToBinary", "dataUriToString", "decodeBase64",
                   "decodeDataUri", "decodeUriComponent", "encodeUriComponent", "float", "int", "json", "string",
                   "uriComponent", "uriComponentToBinary", "uriComponentToString", "xml", "xpath"),
    "Math": ("add", "div", "max", "min", "mod", "mul", "rand", "range", "sub"),
    "Date": ("addDays", "addHours", "addMinutes", "addSeconds", "addToTime", "convertFromUtc", "convertTimeZone",
             "convertToUtc", "dayOfMonth", "dayOfWeek", "dayOfYear", "formatDateTime", "getFutureTime",
             "getPastTime", "startOfDay", "startOfHour", "startOfMonth", "subtractFromTime", "ticks", "utcNow"),
    # Not in the reference's tables: they read the run itself (pipeline().parameters, a ForEach's item(),
    # an activity's output, a variable).
    "Scope": ("activity", "item", "pipeline", "variables"),
}

# The reference's example for a function and the value it gives, where the value doesn't change from
# run to run. A function the evaluator has but that has no example here is "not checked".
EXAMPLES: dict[str, tuple[str, Any]] = {
    "concat": ("concat('Hello', 'World')", "HelloWorld"),
    "endsWith": ("endsWith('hello world', 'world')", True),
    "indexOf": ("indexOf('hello world', 'world')", 6),
    "lastIndexOf": ("lastIndexOf('hello world', 'world')", 6),
    "replace": ("replace('the old string', 'old', 'new')", "the new string"),
    "split": ("split('a_b_c', '_')", ["a", "b", "c"]),
    "startsWith": ("startsWith('hello world', 'hello')", True),
    "substring": ("substring('hello world', 6, 5)", "world"),
    "toLower": ("toLower('Hello World')", "hello world"),
    "toUpper": ("toUpper('Hello World')", "HELLO WORLD"),
    "trim": ("trim(' Hello World  ')", "Hello World"),
    "contains": ("contains('hello world', 'world')", True),
    "empty": ("empty('')", True),
    "first": ("first('hello')", "h"),
    "intersection": ("intersection(createArray(1, 2, 3), createArray(101, 2, 1, 10), createArray(6, 8, 1, 2))",
                     [1, 2]),
    "join": ("join(createArray('a', 'b', 'c'), '.')", "a.b.c"),
    "last": ("last('abcd')", "d"),
    "length": ("length('abcd')", 4),
    "skip": ("skip(createArray(0, 1, 2, 3), 1)", [1, 2, 3]),
    "take": ("take('abcde', 3)", "abc"),
    "union": ("union(createArray(1, 2, 3), createArray(1, 2, 10, 101))", [1, 2, 3, 10, 101]),
    "and": ("and(true, true)", True),
    "equals": ("equals(true, 1)", True),
    "greater": ("greater(10, 5)", True),
    "greaterOrEquals": ("greaterOrEquals(5, 5)", True),
    "if": ("if(equals(1, 1), 'yes', 'no')", "yes"),
    "less": ("less(5, 10)", True),
    "lessOrEquals": ("lessOrEquals(5, 10)", True),
    "not": ("not(false)", True),
    "or": ("or(false, true)", True),
    "array": ("array('hello')", ["hello"]),
    "bool": ("bool(1)", True),
    "coalesce": ("coalesce(null, true, false)", True),
    "createArray": ("createArray('h', 'e', 'l', 'l', 'o')", ["h", "e", "l", "l", "o"]),
    "float": ("float('10.333')", 10.333),
    "int": ("int('10')", 10),
    "json": ("json('[1, 2, 3]')", [1, 2, 3]),
    "string": ("string(10)", "10"),
    "add": ("add(1, 1.5)", 2.5),
    "div": ("div(11, 5)", 2),
    "max": ("max(1, 2, 3)", 3),
    "min": ("min(1, 2, 3)", 1),
    "mod": ("mod(3, 2)", 1),
    "mul": ("mul(1, 2)", 2),
    "range": ("range(1, 4)", [1, 2, 3, 4]),
    "sub": ("sub(10.3, .3)", 10),
    "addDays": ("addDays('2018-03-15T00:00:00Z', 10)", "2018-03-25T00:00:00.0000000Z"),
    "addHours": ("addHours('2018-03-15T00:00:00Z', 10)", "2018-03-15T10:00:00.0000000Z"),
    "addMinutes": ("addMinutes('2018-03-15T00:10:00Z', 10)", "2018-03-15T00:20:00.0000000Z"),
    "addSeconds": ("addSeconds('2018-03-15T00:00:00Z', 10)", "2018-03-15T00:00:10.0000000Z"),
    "formatDateTime": ("formatDateTime('03/15/2018 12:00:00', 'yyyy-MM-ddTHH:mm:ss')", "2018-03-15T12:00:00"),
    "startOfDay": ("startOfDay('2018-03-15T13:30:30Z')", "2018-03-15T00:00:00.0000000Z"),
}
# Their value changes from run to run, so there is nothing to compare.
VARYING = frozenset({"guid", "rand", "utcNow"})

ACTIVITY_STATUS = {"local": "runs locally", "simulated": "simulated", "static": "refused", "cloud": "Fabric only",
                   "unsupported": "refused"}
FUNCTION_STATUSES = ("runs", "differs", "not checked", "refused")


def _canonical(value: Any) -> str:
    """A value as JSON, with a whole float written as an int (Fabric prints 2 for div(11, 5))."""
    def norm(v: Any) -> Any:
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, list):
            return [norm(x) for x in v]
        if isinstance(v, dict):
            return {k: norm(x) for k, x in v.items()}
        return v
    return json.dumps(norm(value), sort_keys=True)


def _function_row(category: str, name: str) -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "category": category, "example": "", "expected": None, "got": None}
    if name.lower() not in evaluate.FUNCTIONS:
        row["status"] = "refused"
        return row
    if category == "Scope" or name in VARYING or name not in EXAMPLES:
        row["status"] = "not checked"
        return row
    example, expected = EXAMPLES[name]
    row["example"], row["expected"] = example, expected
    try:
        got: Any = evaluate.evaluate("@" + example, evaluate.Scope())
    except evaluate.ExpressionError as exc:
        got = f"error: {exc}"
    row["got"] = got
    row["status"] = "runs" if _canonical(got) == _canonical(expected) else "differs"
    return row


def functions() -> list[dict[str, Any]]:
    return [_function_row(category, name) for category, names in FUNCTIONS.items() for name in names]


def activities() -> list[dict[str, Any]]:
    from .activities import default_registry
    rows = []
    for e in sorted(default_registry().entries(), key=lambda e: e.type.lower()):
        rows.append({"type": e.type, "title": e.title, "aliases": list(e.aliases),
                     "classification": e.classification, "status": ACTIVITY_STATUS[e.classification],
                     "evidence": e.evidence, "planned": e.planned.stage if e.planned else ""})
    return rows


def coverage() -> dict[str, list[dict[str, Any]]]:
    return {"activities": activities(), "functions": functions()}


def _cell(value: Any) -> str:
    return "" if value is None else json.dumps(value).replace("|", "\\|")


def markdown(cov: dict[str, list[dict[str, Any]]]) -> str:
    acts, fns = cov["activities"], cov["functions"]

    def count(rows: list[dict[str, Any]], statuses: tuple[str, ...]) -> str:
        return ", ".join(f"{sum(r['status'] == s for r in rows)} {s}" for s in statuses)

    out = [
        "# Local pipeline coverage (Preview)",
        "",
        "This page lists every Data Factory pipeline activity type the local runner knows and every function "
        f"in Microsoft's [pipeline expression reference]({REFERENCE}), with what a local run does with each. "
        "It is generated from the runner's activity registry and expression evaluator "
        "(`sidecar/fabric_desktop/orchestration/coverage.py`), and a test fails when it is out of date. "
        "Nothing here has been compared with a run in Fabric yet.",
        "",
        "## Activities",
        "",
        f"{len(acts)} activity types: {count(acts, ('runs locally', 'simulated', 'refused', 'Fabric only'))}. "
        "A type the registry doesn't know is refused too: it is kept in the pipeline as it is, but not checked "
        "or run. See [activity compatibility](activity-registry.md) for each type's properties, sign-in and "
        "side effects.",
        "",
        "- **runs locally:** a tested adapter runs it on this PC.",
        "- **simulated:** it runs against fixtures or a stand-in, with no real side effects.",
        "- **refused:** a local run checks it (structure, required properties) but can't run it; give it a mock.",
        "- **Fabric only:** it only runs in Fabric or the Azure service it calls, and always uses capacity.",
        "",
        "| Activity | Type | Status | Evidence | Planned |",
        "| --- | --- | --- | --- | --- |",
    ]
    for a in acts:
        types = ", ".join(f"`{t}`" for t in [a["type"], *a["aliases"]])
        evidence = {"unit_tests": "unit tests", "fabric_tested": "checked in Fabric"}.get(a["evidence"], "none")
        out.append(f"| {a['title']} | {types} | {a['status']} | {evidence} | {a['planned']} |")
    out += [
        "",
        "## Expression functions",
        "",
        f"{len(fns)} functions: {count(fns, FUNCTION_STATUSES)}.",
        "",
        "- **runs:** it gives the reference's value for the reference's own example.",
        "- **differs:** it runs but gives another value for that example, so check results that use it in Fabric.",
        "- **not checked:** it runs, but its value changes from run to run, or it reads the run itself, so there "
        "is no value to compare.",
        "- **refused:** the activity stops with `<name>() isn't supported in local runs yet`. Nothing runs with "
        "a guessed value.",
        "",
    ]
    for category in FUNCTIONS:
        rows = [r for r in fns if r["category"] == category]
        out += [f"### {category}", "", "| Function | Status | Example | Reference value | Value here |",
                "| --- | --- | --- | --- | --- |"]
        for r in rows:
            example = f"`{r['example']}`" if r["example"] else ""
            here = "" if r["status"] in ("runs", "refused", "not checked") else _cell(r["got"])
            out.append(f"| `{r['name']}` | {r['status']} | {example} | {_cell(r['expected'])} | {here} |")
        out.append("")
    return "\n".join(out)
