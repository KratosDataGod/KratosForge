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
- Every other parameter matches by name, both ways, as udf.fabric_name writes it: Fabric refuses an
  input parameter with an underscore in its name, so the operation's business_key is the function's
  businessKey (live run 6, 2026-11-04).
- Where the operation has a default, the function has the same one. A dict or list parameter is the
  exception: Fabric refuses a default for one (live run 7, 2026-11-07: "Default values are not
  allowed for 'dict' type parameters"), so it has none, and the function takes an empty one as not
  given: it passes `name or None` on to an operation whose default is `None`, or `name or {}` (`[]`)
  to one whose default is `None` or that empty value. The app sends an empty one for a parameter it
  leaves out (udf.left_out_as_empty). A default on the function where the operation has none is a
  mismatch too: Fabric would accept a call the platform refuses.
- The function calls `_run(sqlDB, "<its own name>", [ctx,] ...)`, and the keywords it passes are
  exactly the operation's names for its own parameters (other than `sqlDB` and `ctx`), each passed
  as that parameter (or `or {}`/`[]`): `business_key=businessKey`.

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
                             ("The operation refuses an empty reason anyway; Fabric asks for one up front "
                             "(pinned by test_requests_core_four::test_the_function_app_ends_exceptions_as_the_caller).")),
}

_EMPTY = inspect.Parameter.empty
_OR_NONE = ("<or None>",)  # what _passed gives as the fill for `name or None`


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
    if (isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or) and len(value.values) == 2
            and isinstance(value.values[0], ast.Name) and isinstance(value.values[1], ast.Constant)
            and value.values[1].value is None):
        return value.values[0].id, _OR_NONE
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
    wrappers = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}  # helpers, as functions.json skips them
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
        kinds = {x.arg: ast.unparse(x.annotation) if x.annotation else "" for x in a.args}
        w_defaults = dict(zip(names[len(names) - len(a.defaults):], a.defaults, strict=True))

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
        op_of = {udf.fabric_name(n): n for n in op_rest}  # the function's name -> the operation's
        for n in names:
            if "_" in n:
                found.append(f"parameter {n} has an underscore, which Fabric refuses")
        for n in op_rest:
            if udf.fabric_name(n) not in w_rest:
                found.append(f"missing parameter {n}")
        for n in w_rest:
            if n not in op_of:
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
            if set(passed) != {op_of.get(n, n) for n in w_rest}:
                found.append(f"_run gets {sorted(passed)} but the function takes {sorted(w_rest)}")
            for kw, (src, _fill) in passed.items():
                if src != udf.fabric_name(kw):
                    found.append(f"_run's {kw}= is {src!r}, not the parameter itself")

        for n in w_rest:
            if n not in op_of:
                continue
            op_default, w_node = sig[op_of[n]].default, w_defaults.get(n)
            container = kinds.get(n) in ("dict", "list")
            if container and w_node is not None:
                found.append(f"{n} has a default in Fabric, which refuses one for a {kinds[n]}")
                continue
            if op_default is _EMPTY:
                if w_node is not None:
                    found.append(f"{n} has a default in Fabric but is required by the operation")
                continue
            fill = passed.get(op_of[n], (None, None))[1]
            if w_node is None and container and op_default in (None, {}, []) and (
                    (fill is _OR_NONE and op_default is None)
                    or (isinstance(fill, (dict, list)) and fill == (op_default if op_default is not None else fill)
                        and (op_default is None or type(fill) is type(op_default)))):
                continue  # required in Fabric; an empty one is not given (udf.left_out_as_empty)
            if w_node is None:
                found.append(f"{n} is required in Fabric but defaults to {op_default!r} in the operation")
                continue
            w_default = _literal(w_node)
            if w_default == op_default and type(w_default) is type(op_default):
                continue
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


def test_no_dict_or_list_parameter_has_a_default():
    """Fabric refuses a default for a dict parameter (live run 7), and the list ones go the same way."""
    tree = ast.parse(udf.function_app())
    found = []
    for fn_def in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        args = fn_def.args.args
        for arg in args[len(args) - len(fn_def.args.defaults):]:
            if arg.annotation is not None and ast.unparse(arg.annotation) in ("dict", "list"):
                found.append(f"{fn_def.name}.{arg.arg}")
    assert not found


def test_the_app_sends_an_empty_value_for_a_left_out_dict_or_list():
    empty = udf.left_out_as_empty()
    assert empty["register_link"] == {"columns": {}}
    assert empty["call_once"] == {"args": {}}
    assert empty["policy_snapshot"] == {"policies": []}
    assert empty["log_shortcut_change"] == {"before": {}, "after": {}}
    required = {"register_link": "reads", "log_shortcut_change": "shortcut", "record_endorsements": "items"}
    for fn_name, name in required.items():  # passed as they are: the operation needs them
        assert name not in empty[fn_name]


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
def save_it(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, name: str, detail: dict,
            note: str = "") -> dict:
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
    assert problems == {"save_it": ["detail is required in Fabric but defaults to [] in the operation"]}
    problems = parity_problems(_SOURCE, _ops(**{**_GOOD, "save_it": "db, name, note, by='', detail={}"}))
    assert problems == {"save_it": ["note has a default in Fabric but is required by the operation"]}


def test_drift_check_catches_an_empty_dict_passed_on_unfilled():
    source = _SOURCE.replace("detail=detail or {}", "detail=detail")
    assert parity_problems(source, _ops(**_GOOD)) == {
        "save_it": ["detail is required in Fabric but defaults to {} in the operation"]}


def test_drift_check_takes_an_empty_dict_as_none_for_a_none_default():
    source = _SOURCE.replace("detail=detail or {}", "detail=detail or None")
    assert parity_problems(source, _ops(**{**_GOOD, "save_it": "db, name, by='', note='', detail=None"})) == {}
    assert parity_problems(source, _ops(**_GOOD)) == {
        "save_it": ["detail is required in Fabric but defaults to {} in the operation"]}


def test_drift_check_catches_a_default_on_a_dict_parameter():
    source = _SOURCE.replace('detail: dict,\n            note: str = ""', 'note: str = "", detail: dict = None')
    assert source != _SOURCE
    assert parity_problems(source, _ops(**_GOOD)) == {
        "save_it": ["detail has a default in Fabric, which refuses one for a dict"]}


def test_drift_check_catches_a_drifted_real_operation():
    """The real tree: give one operation a new parameter and the real check reports it."""
    ops = copy.copy(core.OPERATIONS)
    ns: dict = {}
    exec("def issue_keys(db, name, business_keys, by='', env='dev'): pass", ns)  # noqa: S102
    ops["issue_keys"] = ns["issue_keys"]
    problems = parity_problems(udf.function_app(), ops)
    assert problems.get("issue_keys") == ["missing parameter env"]


def test_drift_check_catches_a_parameter_with_an_underscore():
    source = _SOURCE.replace("name: str) -> dict:", "name: str, env_name: str) -> dict:")
    source = source.replace('"read_it", name=name)', '"read_it", name=name, env_name=env_name)')
    problems = parity_problems(source, _ops(**{**_GOOD, "read_it": "db, name, env_name"}))
    assert problems == {"read_it": ["parameter env_name has an underscore, which Fabric refuses",
                                    "missing parameter env_name", "extra parameter env_name",
                                    "_run's env_name= is 'env_name', not the parameter itself"]}


def test_drift_check_takes_a_camel_case_parameter_for_its_operations_name():
    source = _SOURCE.replace("name: str) -> dict:", "name: str, envName: str) -> dict:")
    source = source.replace('"read_it", name=name)', '"read_it", name=name, env_name=envName)')
    assert parity_problems(source, _ops(**{**_GOOD, "read_it": "db, name, env_name"})) == {}


def test_no_function_takes_a_parameter_with_an_underscore():
    """Fabric's Python code validation refuses it, which refused the whole item in live run 6."""
    for f in udf._functions():
        names = [p["name"] for p in f["fabricProperties"]["fabricFunctionParameters"]]
        assert not [n for n in names if "_" in n], f["name"]


def test_fabric_names():
    assert [udf.fabric_name(n) for n in ("name", "business_keys", "call_id", "product_id")] == [
        "name", "businessKeys", "callId", "productId"]
