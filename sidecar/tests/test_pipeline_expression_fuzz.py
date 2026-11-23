"""Seeded fuzzing of the pipeline expression reader and evaluator (orchestration.expressions, .evaluate,
.validate) and the Dataflow Gen2 mashup reader (orchestration.mashup). Every input is read, or refused
with ExpressionError: no other exception, no super-linear time, and no junk read as if it were fine.
Stdlib ``random`` only, so each case is reproducible from its seed."""

from __future__ import annotations

import base64
import gc
import json
import random
import time
from datetime import datetime, timezone

import pytest

from fabric_desktop.orchestration import definition, evaluate, expressions, mashup
from fabric_desktop.orchestration.validate import validate

ExpressionError = evaluate.ExpressionError


def _scope() -> evaluate.Scope:
    return evaluate.Scope(parameters={"p": 1, "s": "x"}, variables={"v": "abc", "arr": [1, 2, 3], "obj": {"k": [1]}},
                          activities={"A": {"output": {"rows": [{"x": 1}]}}}, system={"RunId": "r"}, items=[{"x": 2}],
                          now=lambda: datetime(2024, 1, 2, tzinfo=timezone.utc))


def _run(text: str):
    return evaluate.evaluate(text, _scope())


# ---- regressions: each raised something other than ExpressionError, or gave a wrong answer

@pytest.mark.parametrize("text, says", [
    ("@max()", "couldn't work out a value"),                                      # ValueError
    ("@min(createarray())", "couldn't work out a value"),                         # ValueError
    ("@intersection()", "couldn't work out a value"),                             # IndexError
    ("@split('abc', '')", "couldn't work out a value"),                           # ValueError
    ("@skip(json('{}'), 1)", "couldn't work out a value"),                        # KeyError
    ("@float(" + "9" * 400 + ")", "couldn't work out a value"),                   # OverflowError
    ("@mod(" + "9" * 400 + ", 7.5)", "couldn't work out a value"),                # OverflowError
    ("@addDays('2020-01-01', 10000000)", "couldn't work out a value"),            # OverflowError
    ("@formatDateTime('0001-01-01T00:00:00+05:00')", "couldn't work out a value"),
    ("@ticks('0001-01-01T00:00:00+01:00')", "couldn't work out a value"),
    ("@base64('\ud800')", "couldn't work out a value"),                           # UnicodeEncodeError
    ("@uriComponent('\ud800')", "couldn't work out a value"),
    ("@" + "9" * 5000, "more than 4,300 digits"),                                 # ValueError
    ("@{mul(" + "9" * 3000 + ", " + "9" * 3000 + ")}", "more than 4,300 digits"),
    ("@json('{}')[createarray()]", "a property name is text"),                    # TypeError
    ("@json('" + "[" * 100_000 + "')", "nested too deeply"),                      # RecursionError
    ("@" + "concat(" * 5000 + "'a'" + ")" * 5000, "nested too deeply"),
    ("@" + "createarray(0)[" * 3000 + "0" + "]" * 3000, "nested too deeply"),
    ("@concat('a',)", "comma with nothing after it"),                             # read as concat('a')
    ("@add(١, 1)", "isn't expected"),                                        # Arabic-Indic 1 read as 1
    ("@replace('ab', '', 'x')", "can't replace empty text"),                      # gave 'xaxbx'
])
def test_odd_expressions_are_refused_by_name(text, says):
    with pytest.raises(ExpressionError, match=says):
        _run(text)


@pytest.mark.parametrize("text, want", [
    ("@div(9007199254740993, 1)", 9007199254740993),       # was 9007199254740992, through a float
    ("@div(-9007199254740993, 1)", -9007199254740993),
    ("@div(-11, 5)", -2), ("@div(11, -5)", -2), ("@div(11, 5)", 2), ("@div(11, 5.0)", 2.2),
    ("@mod(9007199254740993, 2)", 1),                      # was 0
    ("@mod(-11, 5)", -1), ("@mod(11, -5)", 1), ("@mod(-11, -5)", -1), ("@mod(7.5, 2)", 1.5),
    ("@sub(10.3, .3)", 10.0), ("@concat('a', 'b')", "ab"),
])
def test_whole_numbers_divide_exactly(text, want):
    assert _run(text) == want


def _deep_pipeline(depth: int) -> str:
    deep = '{"a":' * depth + '"@variables(\'nope\')"' + "}" * depth
    return ('{"name":"p","properties":{"activities":[{"name":"A","type":"Wait","typeProperties":{"x":' + deep
            + "}}]}}")


def test_a_deeply_nested_definition_is_validated_and_evaluated_without_recursion_error():
    imp = definition.loads(_deep_pipeline(2500))   # json reads deeper than Python's recursion limit
    found = [d for d in validate(imp) if d.code == "undeclared_variable"]
    assert len(found) == 1 and found[0].path.endswith(".a" * 2500)
    with pytest.raises(ExpressionError, match="nested too deeply"):
        evaluate.evaluate(json.loads(_deep_pipeline(2500)), _scope())


def test_an_escaped_at_in_an_expression_object_is_text_not_an_unreadable_expression():
    node = {"value": "@@{not one}!", "type": "Expression"}
    assert evaluate.evaluate(node, _scope()) == "@{not one}!"
    assert list(expressions.scan(node)) == []   # was "'@' isn't expected in an expression"


def test_scan_keeps_depth_first_document_order():
    node = {"b": ["@variables('x')", {"c": "@{item()}"}], "a": {"value": "@pipeline().RunId", "type": "Expression"}}
    assert [e.path for e in expressions.scan(node)] == ["$.b[0]", "$.b[1].c", "$.a.value"]


# ---- the mashup reader

def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode()


def _definition(m: str, meta: str = "{}") -> dict:
    return {"definition": {"parts": [{"path": "mashup.pq", "payload": _b64(m)},
                                     {"path": "queryMetadata.json", "payload": _b64(meta)}]}}


def test_a_comment_that_starts_with_slash_star_slash_hides_its_query():
    # "/*/" opens a comment; it was read as opened and closed, so the query inside it was listed
    assert [q["name"] for q in mashup.queries("section S;\n/*/ shared Fake = 1; */\nshared Real = 2;")] == ["Real"]
    assert mashup.queries("/*/ shared Fake = 1; */") == []


@pytest.mark.parametrize("meta", ["[" * 100_000, json.dumps({"connections": [{"connectionId": "[" * 100_000}]})])
def test_deeply_nested_query_metadata_is_a_problem_not_a_recursion_error(meta):
    out = mashup.describe(_definition("section S; shared A = 1;", meta))
    assert [q["name"] for q in out["queries"]] == ["A"]


# ---- time: the work must grow with the length, not faster

def _best_time(fn, arg) -> float:
    """CPU time for one call, the best of three samples, each repeating the call for at least 0.1 s, with the
    garbage collector off (as tests/test_spark_sql_fuzz.py::_best_time)."""
    best = float("inf")
    collecting = gc.isenabled()
    gc.disable()
    try:
        for _ in range(3):
            calls, start = 0, time.process_time()
            while (spent := time.process_time() - start) < 0.1 or not calls:
                fn(arg)
                calls += 1
            best = min(best, spent / calls)
    finally:
        if collecting:
            gc.enable()
    return best


def _scan_text(text: str) -> None:
    list(expressions.scan({"a": text}))


@pytest.mark.parametrize("fn, make", [
    (_scan_text, lambda k: "@createarray(" + ",".join(f"f{i}()" for i in range(k)) + ")"),   # was quadratic
    (mashup.queries, lambda k: "//c\n" * k + "shared A = 1;"),                               # was quadratic
    (mashup.queries, lambda k: "/**/" * k + "shared A = 1;"),
], ids=["distinct functions", "line comments", "block comments"])
def test_reading_time_grows_with_the_length(fn, make):
    small, large = _best_time(fn, make(1500)), _best_time(fn, make(12000))
    assert large < 20 * small, f"{small:.4f}s for 1,500, {large:.4f}s for 12,000: grows faster than the length"


# ---- seeded fuzzing: grammar-made expressions and mutations of them

_LITERALS = ["'a'", "''", "'it''s'", "0", "1", "-1", "2.5", ".5", "-.5", "true", "false", "null",
             "'2020-01-01T00:00:00Z'", "'0001-01-01T00:00:00+01:00'", "'9999-12-31T23:59:59-01:00'",
             "'03/15/2018 12:00:00'", "'Day'", "'Month'", "'UTC'", "'Pacific Standard Time'", "'yyyy-MM-dd'", "'%'",
             "'{'", "'}'", "'@{'", "'[1,2]'", "'{\"a\":1}'", "9223372036854775807", "99999999999999999999",
             "'\ud800x'", "'\U0001F600'", "','", "'data:,a%20b'", "'YQ=='", "'%E9'", "100000", "'  5 '", "'1e3'"]
_READS = ["pipeline().parameters.p", "pipeline().RunId", "variables('v')", "variables('arr')", "activity('A').output",
          "activity('A').output.rows[0]", "item()", "item()?.x", "variables('obj')['K']"]
_JUNK = list("@{}()[]'.,?-0 a\\\"\n ١$;+") + ["@{", "}}", "''", "@@", "pipeline()", "concat("]


def _expression(rnd: random.Random, depth: int = 0) -> str:
    c = rnd.random()
    if depth > 4 or c < 0.35:
        return rnd.choice(_LITERALS)
    if c < 0.45:
        return rnd.choice(_READS)
    args = ", ".join(_expression(rnd, depth + 1) for _ in range(rnd.choice([0, 1, 1, 2, 2, 3, 4])))
    text = f"{rnd.choice(evaluate.FUNCTIONS)}({args})"
    return text + rnd.choice(["[0]", ".a", "?.a", "['x']", "[-1]"]) if rnd.random() < 0.1 else text


def _mutate(rnd: random.Random, text: str) -> str:
    chars = list(text)
    for _ in range(rnd.randint(1, 4)):
        i, op = rnd.randint(0, len(chars)), rnd.random()
        if op < 0.4 and chars:
            del chars[min(i, len(chars) - 1)]
        elif op < 0.8:
            chars.insert(i, rnd.choice(_JUNK))
        else:
            j = rnd.randint(0, len(chars))
            chars[i:i] = chars[min(i, j):max(i, j)]
    return "".join(chars)


def _value(rnd: random.Random):
    text = _expression(rnd)
    if rnd.random() < 0.5:
        text = _mutate(rnd, text)
    k = rnd.random()
    if k < 0.4:
        return "@" + text
    if k < 0.8:
        return rnd.choice(["", "x ", "@@{a} "]) + "@{" + text + "}" + rnd.choice(["", " y", "@{'z'}", "}"])
    return {"value": rnd.choice(["@", "@@"]) + text, "type": "Expression"}


@pytest.mark.parametrize("seed", range(4))
def test_fuzzed_expressions_are_evaluated_or_refused_by_name(seed):
    rnd = random.Random(seed)
    for i in range(1500):
        value = _value(rnd)
        try:
            evaluate.evaluate(value, _scope())
            evaluated = True
        except ExpressionError:
            evaluated = False
        found = list(expressions.scan(value))   # never raises
        if evaluated:   # what runs must read without a problem too
            assert not [e.problem for e in found if e.problem], (seed, i, ascii(value))


_MASHUP_JUNK = ['"', '""', "/*", "*/", "//", "\n", ";", "[", "]", "{", "}", "(", ")", "shared ", '#"', "=", " ", "/*/"]
_MASHUP = ('section Section1;\n// shared Fake = 1;\nshared Rates = let\n  Text = "a ""quoted"" string; x"\nin\n  Text;\n'
           '[DataDestinations = {[Definition = [Kind = "Reference", QueryName = "Orders_DataDestination", IsNewTarget = true],'
           ' Settings = [UpdateMethod = [Kind = "Replace"]]]}]\nshared Orders = Sql.Database("erp", "erp");\n'
           'shared Orders_DataDestination = let\n  P = Lakehouse.Contents([]),\n  N = P{[workspaceId = "w"]}[Data],\n'
           '  T = N{[Id = "orders", ItemKind = "Table"]}?[Data]?\nin\n  T;\nshared #"Odd ""name""" = 1;\n')


@pytest.mark.parametrize("seed", range(2))
def test_fuzzed_mashups_are_read_without_an_exception(seed):
    rnd = random.Random(seed)
    meta = json.dumps({"queriesMetadata": {"Rates": {"loadEnabled": False}}, "connections": [{"connectionId": "{}"}]})
    for _ in range(600):
        chars = list(_MASHUP)
        for _ in range(rnd.randint(1, 6)):
            i = rnd.randint(0, len(chars))
            if rnd.random() < 0.5:
                del chars[i:i + rnd.randint(1, 5)]
            else:
                chars.insert(i, rnd.choice(_MASHUP_JUNK))
        out = mashup.describe(_definition("".join(chars), meta))
        assert all(isinstance(q["name"], str) for q in out["queries"])
