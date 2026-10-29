"""Every UDF_Platform function takes the same parameters as the platform operation it calls (#936).

udf.function_app() writes one Fabric function per operation in core.OPERATIONS. A parameter added
to an operation but missed in its function works on the PC's SQLite platform, then fails or is
silently dropped in Fabric. This test reads the generated source with `ast` and compares each
function with `inspect.signature` of its operation.

The rules:
- `db` (the operation's connection) is the function's first parameter, `sqlDB`.
- `by` (who made the change) is never a parameter of the function: `_run` fills it from Fabric's
  `ctx`, never from what the caller says. So the function takes `ctx` exactly when the operation
  takes `by`, and then also carries `@udf.context(argName="ctx")`. Without `ctx` a change would be
  recorded as made by nobody.
- Every other parameter matches by name, both ways.
- Where the operation has a default, the function has the same one. Equivalent: a `None` default
  for an operation default of `None`, `{}` or `[]`, when the function passes `name or {}` (or
  `name or []`) on to `_run`, or passes `None` to an operation whose default is `None`. Fabric
  can't take a mutable default, which is why it writes `None` there. A default on the function where
  the operation has none is a mismatch too: Fabric would accept a call the platform refuses.
- The function calls `_run(sqlDB, "<its own name>", [ctx,] ...)`, and the keywords it passes are
  exactly its own parameters (other than `sqlDB` and `ctx`), each passed as itself (or `or {}`/`[]`).

Deliberate differences go in DELIBERATE with their reason.
"""

import ast
import copy
import inspect

from fabric_desktop.central import core, udf

# function or op -> (the exact problems it is allowed, the reason). Any other problem still fails,
# and an entry whose problems are gone fails too, so the list can't go stale.
_REPORT_BUTTON = ("A report button in the Catalog app (translytical task flow): it takes one field, calls "
                  "save_enrichment with it and returns text for the report, so it is not an operation.")
DELIBERATE: dict[str, tuple[list[str], str]] = {
    "set_description": (["function for no operation"], _REPORT_BUTTON),
    "set_tags": (["function for no operation"], _REPORT_BUTTON),
    "set_classification": (["function for no operation"], _REPORT_BUTTON),
    "end_policy_exception": (["reason is required in Fabric but defaults to '' in the operation"],
                             "The operation refuses an empty reason anyway; Fabric asks for one up front "
                             "(pinned by test_requests_core_four::test_the_function_app_ends_exceptions_as_the_caller)."),
}

_EMPTY = inspect.Parameter.empty


def _literal(node):
    """A default's value, or a marker when it isn't a literal (so it never equals a real default)."""
    try:
        return ast.literal_eval(node)
    except ValueError:
        return ("<not a literal>", ast.unparse(node))


def _passed(value: ast.expr) -> tuple[str | None, object]:
    """What a keyword passes to _run: (name, fill), where fill is {} / [] for `name or {}` / `name or []`."""
    if isinstance(value, ast.Name):
        return value.id, None
    if (isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or) and len(value.values) == 2
            and isinstance(value.values[0], ast.Name) and isinstance(value.values[1], (ast.Dict, ast.List))
            and not getattr(value.values[1], "keys", None) and not getattr(value.values[1], "elts", None)):
        return value.values[0].id, ({} if isinstance(value.values[1], ast.Dict) else [])
    return None, ast.unparse(value)


def _run_call(fn_def: ast.FunctionDef) -> ast.Call | None:
    calls = [n for n in ast.walk(fn_def) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "_run"]
    return calls[0] if len(calls) == 1 else None


def _has_context_decorator(fn_def: ast.FunctionDef) -> bool:
    for d in fn_def.decorator_list:
        if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "context"
                and any(k.arg == "argName" and _literal(k.value) == "ctx" for k in d.keywords)):
            return True
    return False


def parity_problems(source: str, operations: dict) -> dict[str, list[str]]:
    """Every difference between the generated functions in `source` and `operations`, by operation."""
    tree = ast.parse(source)
    wrappers = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name != "_run"}
    problems: dict[str, list[str]] = {}

    for op, impl in operations.items():
        found: list[str] = []
        problems[op] = found
        w = wrappers.get(op)
        if w is None:
            found.append("no function")
            continue
        a = w.args
        if a.posonlyargs or a.vararg or a.kwonlyargs or a.kwarg:
            found.append("takes positional-only, *args, keyword-only or **kwargs parameters")
        names = [x.arg for x in a.args]
        w_defaults = dict(zip(names[len(names) - len(a.defaults):], a.defaults))

        sig = inspect.signature(impl).parameters
        op_names = list(sig)
        if not op_names or op_names[0] != "db":
            found.append("operation's first parameter is not db")
        if not names or names[0] != "sqlDB":
            found.append("function's first parameter is not sqlDB")
        takes_by, takes_ctx = "by" in sig, "ctx" in names
        if takes_by != takes_ctx:
            found.append("operation takes by but function has no ctx" if takes_by
                         else "function takes ctx but operation has no by")
        if takes_ctx and not _has_context_decorator(w):
            found.append('ctx without @udf.context(argName="ctx")')

        op_rest = [n for n in op_names if n not in ("db", "by")]
        w_rest = [n for n in names if n not in ("sqlDB", "ctx")]
        for n in op_rest:
            if n not in w_rest:
                found.append(f"missing parameter {n}")
        for n in w_rest:
            if n not in sig:
                found.append(f"extra parameter {n}")

        # How each parameter reaches _run, for the default rule and the keyword rule.
        call = _run_call(w)
        passed: dict[str, tuple[str | None, object]] = {}
        if call is None:
            found.append("does not call _run exactly once")
        else:
            # Compared as (kind, name or value): ast.dump of a hand-built node differs between Python
            # versions (3.13 fills in ctx=Load(), 3.12 leaves it out).
            want = [("Name", "sqlDB"), ("Constant", op)] + ([("Name", "ctx")] if takes_ctx else [])
            got = [(type(x).__name__, x.id if isinstance(x, ast.Name) else getattr(x, "value", None)) for x in call.args]
            if got != want:
                found.append(f"_run's positional arguments are {[ast.unparse(x) for x in call.args]}")
            for k in call.keywords:
                if k.arg is None:
                    found.append("passes **kwargs to _run")
                    continue
                passed[k.arg] = _passed(k.value)
            if set(passed) != set(w_rest):
                found.append(f"_run gets {sorted(passed)} but the function takes {sorted(w_rest)}")
            for kw, (src, _fill) in passed.items():
                if src != kw:
                    found.append(f"_run's {kw}= is {src!r}, not the parameter itself")

        for n in w_rest:
            if n not in sig:
                continue
            op_default, w_node = sig[n].default, w_defaults.get(n)
            if op_default is _EMPTY:
                if w_node is not None:
                    found.append(f"{n} has a default in Fabric but is required by the operation")
                continue
            if w_node is None:
                found.append(f"{n} is required in Fabric but defaults to {op_default!r} in the operation")
                continue
            w_default = _literal(w_node)
            if w_default == op_default and type(w_default) is type(op_default):
                continue
            fill = passed.get(n, (None, None))[1]
            if w_default is None and fill is not None and fill == op_default and type(fill) is type(op_default):
                continue  # None in Fabric, filled with `or {}`/`or []` to the operation's own default.
            found.append(f"{n} defaults to {ast.unparse(w_node)} in Fabric but {op_default!r} in the operation")

    for name in wrappers:
        if name not in operations:
            problems.setdefault(name, []).append("function for no operation")
    return {op: p for op, p in problems.items() if p}


def test_every_function_takes_its_operations_parameters():
    problems = parity_problems(udf.function_app(), core.OPERATIONS)
    unexplained = {op: p for op, p in problems.items() if p != DELIBERATE.get(op, (None,))[0]}
    assert not unexplained, "\n".join(f"{op}: {'; '.join(p)}" for op, p in sorted(unexplained.items()))
    stale = [op for op in DELIBERATE if op not in problems]
    assert not stale, f"listed as deliberate but no longer differ: {stale}"


def test_report_buttons_call_save_enrichment_as_the_caller():
    """The deliberate extras still go through _run with ctx, so their change is recorded as the caller's."""
    tree = ast.parse(udf.function_app())
    wrappers = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name in [n for n in DELIBERATE if n not in core.OPERATIONS]:
        call = _run_call(wrappers[name])
        assert call is not None and [ast.unparse(a) for a in call.args] == ["sqlDB", "'save_enrichment'", "ctx"]
        assert _has_context_decorator(wrappers[name])
        assert {k.arg for k in call.keywords} == {"target", "fields"}


# ------------------------------------------------------------------- the test catches drift


def _ops(**sigs):
    """Fake operations from source text, so their signatures are exactly what the case needs."""
    ns: dict = {}
    for name, params in sigs.items():
        exec(f"def {name}({params}): pass", ns)  # noqa: S102 - test-only, fixed strings
    return {n: ns[n] for n in sigs}


_SOURCE = '''
def _run(sqlDB, op, ctx=None, **kwargs):
    pass

@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def save_it(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, name: str, note: str = "",
            detail: dict = None) -> dict:
    return _run(sqlDB, "save_it", ctx, name=name, note=note, detail=detail or {})

@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def read_it(sqlDB: fn.FabricSqlConnection, name: str) -> dict:
    return _run(sqlDB, "read_it", name=name)
'''

_GOOD = dict(save_it="db, name, by='', note='', detail={}", read_it="db, name")


def test_drift_check_passes_on_a_matching_pair():
    assert parity_problems(_SOURCE, _ops(**_GOOD)) == {}


def test_drift_check_catches_a_parameter_added_to_the_operation_only():
    problems = parity_problems(_SOURCE, _ops(**{**_GOOD, "read_it": "db, name, env=''"}))
    assert problems == {"read_it": ["missing parameter env"]}


def test_drift_check_catches_a_parameter_added_to_the_function_only():
    source = _SOURCE.replace("name: str) -> dict:", "name: str, env: str) -> dict:")
    source = source.replace('"read_it", name=name)', '"read_it", name=name, env=env)')
    assert parity_problems(source, _ops(**_GOOD)) == {"read_it": ["extra parameter env"]}


def test_drift_check_catches_a_parameter_dropped_on_the_way_to_run():
    source = _SOURCE.replace(", note=note,", ",")
    problems = parity_problems(source, _ops(**_GOOD))
    assert problems == {"save_it": ["_run gets ['detail', 'name'] but the function takes ['detail', 'name', 'note']"]}


def test_drift_check_catches_by_without_ctx_and_ctx_without_by():
    assert parity_problems(_SOURCE, _ops(**{**_GOOD, "read_it": "db, name, by=''"})) == {
        "read_it": ["operation takes by but function has no ctx"]}
    assert parity_problems(_SOURCE, _ops(**{**_GOOD, "save_it": "db, name, note='', detail={}"})) == {
        "save_it": ["function takes ctx but operation has no by"]}


def test_drift_check_catches_defaults_that_differ():
    problems = parity_problems(_SOURCE, _ops(**{**_GOOD, "save_it": "db, name, by='', note='x', detail={}"}))
    assert problems == {"save_it": ["note defaults to '' in Fabric but 'x' in the operation"]}
    problems = parity_problems(_SOURCE, _ops(**{**_GOOD, "save_it": "db, name, by='', note='', detail=[]"}))
    assert problems == {"save_it": ["detail defaults to None in Fabric but [] in the operation"]}
    problems = parity_problems(_SOURCE, _ops(**{**_GOOD, "save_it": "db, name, note, by='', detail={}"}))
    assert problems == {"save_it": ["note has a default in Fabric but is required by the operation"]}


def test_drift_check_catches_a_none_default_passed_on_unfilled():
    source = _SOURCE.replace("detail=detail or {}", "detail=detail")
    assert parity_problems(source, _ops(**_GOOD)) == {
        "save_it": ["detail defaults to None in Fabric but {} in the operation"]}


def test_drift_check_catches_a_drifted_real_operation():
    """The real tree: give one operation a new parameter and the real check reports it."""
    ops = copy.copy(core.OPERATIONS)
    ns: dict = {}
    exec("def issue_keys(db, name, business_keys, env='dev'): pass", ns)  # noqa: S102
    ops["issue_keys"] = ns["issue_keys"]
    problems = parity_problems(udf.function_app(), ops)
    assert problems.get("issue_keys") == ["missing parameter env"]
