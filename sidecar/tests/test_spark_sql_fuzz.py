"""Spark SQL fuzzed: every query is read and run on this PC, or refused with a ValueError that says why.

Seeded and deterministic (the standard library's random, no new package): valid queries cut, flipped and
filled with stray brackets, quotes, comments, control characters and Unicode, through the three ways Spark
SQL reaches DuckDB here (sql.parse and duckdb_sql, sql.run, a local data project's engine). None may stop
with an error a person can't read (AttributeError, IndexError, RecursionError, sqlglot's own TokenError...)
or take long. Queries made from a small grammar must give the answer worked out here in Python.

The cases fuzzing found are kept below by name: each crashed or ran for minutes before it was fixed.
"""

from __future__ import annotations

import gc
import random
import re
import time

import duckdb
import pytest

from fabric_desktop import spark_sql_names, sql, sql_dialects
from fabric_desktop.localdata import LocalData

T = "(SELECT * FROM VALUES (1, 'a', 2.5), (2, 'b', NULL), (3, NULL, -1.0) AS t(a, b, c))"
CORPUS = [
    "SELECT 1",
    f"SELECT a, b FROM {T} WHERE a > 1 ORDER BY b DESC NULLS LAST LIMIT 5",
    f"SELECT substring(b, 2, 3), concat(b, 'x'), upper(b), length(b) FROM {T}",
    f"WITH c AS (SELECT a FROM {T}) SELECT count(*) FROM c GROUP BY a HAVING count(*) > 0",
    "SELECT CAST('2024-01-01' AS DATE), date_add('2024-01-01', 3), datediff('2024-02-01', '2024-01-01')",
    "SELECT map('a', 1), array(1, 2, 3), struct(1 AS x), named_struct('a', 1)",
    "SELECT transform(array(1, 2), x -> x + 1), filter(array(1, 2), x -> x > 1)",
    "SELECT from_json('{\"a\":1}', 'a INT'), get_json_object('{\"a\":1}', '$.a')",
    f"SELECT a, row_number() OVER (PARTITION BY b ORDER BY c) FROM {T}",
    "SELECT 1 div 2, 5 % 3, pmod(-7, 3), 7 / 2",
    "SELECT X'1010', 1e10, 1.5BD, 10L, 3S, 2Y",
    f"SELECT CASE WHEN a = 1 THEN 'x' ELSE 'y' END FROM {T}",
    "SELECT explode(array(1, 2))",
    "SELECT 'it''s', \"double\"",
    "SELECT to_date('2024-01-01', 'yyyy-MM-dd'), date_format(TIMESTAMP '2024-01-02 03:04:05', 'yyyy-MM-dd HH')",
    "SELECT aggregate(array(1, 2, 3), 0, (acc, x) -> acc + x)",
    "SELECT nvl(NULL, 0), coalesce(NULL, 2), nullif(1, 1), round(2.555, 2), bround(2.5)",
    "SELECT split('a,b,c', ','), regexp_replace('abc', 'b', 'x'), lpad('a', 5, '*'), translate('abc', 'ab', 'x')",
    "SELECT cast('12' AS INT), cast('x' AS INT), cast(1.9 AS INT), try_cast('1' AS BIGINT), cast(1 AS STRING)",
    f"SELECT sum(a), avg(c), max(b), collect_list(a) FROM {T}",
    "SELECT 1; SELECT 2",
    "-- a comment\nSELECT /* another */ instr('abc', 'c'), locate('c', 'abc'), sha2('a', 256), md5('a')",
]
PIECES = ["(", ")", "'", '"', "`", ";", "--", "/*", "*/", "\x00", "\x1b", "\\", ",", "->", "=", "é", "😀", chr(0x202E),
          "SELECT", "FROM", "OVER", "map(", "struct(", "array(", "string(", "CAST(", " AS ", "INTERVAL", "X'", "1e999",
          "${x}", ":x", "?", "{", ":", "BD", "."]
# What a query may be refused with: a ValueError says why (sql.parse, duckdb_sql, the engine), a PermissionError
# that only reads may run, and DuckDB's own errors name what it couldn't bind or run.
REFUSALS = (ValueError, PermissionError, duckdb.Error)
SLOW = 5.0  # seconds one query may take


def mutate(r: random.Random, text: str) -> str:
    s = list(text)
    for _ in range(r.randint(1, 4)):
        op, i = r.randrange(6), r.randrange(len(s) + 1)
        if op == 0 and s:
            del s[r.randrange(len(s))]
        elif op == 1:
            s[i:i] = list(r.choice(PIECES))
        elif op == 2 and s:
            s[r.randrange(len(s))] = chr(r.randrange(0, 0x3000))
        elif op == 3:
            s = s[:i]
        elif op == 4:
            s[i:i] = list(r.choice(PIECES)) * r.randint(1, 40)
        elif op == 5 and s:
            j = r.randrange(len(s))
            s[i:i] = s[j:j + r.randint(1, 20)]
    return "".join(s)


def _cases(seed: int, n: int) -> list[str]:
    r = random.Random(seed)
    return CORPUS + [mutate(r, r.choice(CORPUS)) for _ in range(n)]


def _read_or_refused(call, query: str) -> None:
    start = time.monotonic()
    try:
        call(query)
    except REFUSALS:
        pass
    except RuntimeError as exc:  # a table name that isn't on this PC: sql.run looks for it in the lakehouse
        assert str(exc).startswith("No lakehouse is attached"), (query, exc)
    except Exception as exc:  # noqa: BLE001 - the point of the test
        pytest.fail(f"{type(exc).__name__}: {exc} for {query!r}")
    assert time.monotonic() - start < SLOW, f"took {time.monotonic() - start:.1f}s: {query!r}"


def _translate(query: str) -> None:
    for statement in sql.parse(query, "spark"):
        sql.duckdb_sql(statement)


@pytest.mark.parametrize("seed", [20261118, 7])
def test_mutated_spark_sql_is_translated_or_refused_by_name(seed):
    for query in _cases(seed, 400):
        _read_or_refused(_translate, query)


def test_mutated_spark_sql_runs_or_is_refused_by_name():
    for query in _cases(11, 150):
        _read_or_refused(lambda q: sql.run(q, "spark"), query)


def test_every_query_in_the_corpus_runs():
    for query in CORPUS:
        sql.run(query, "spark")


@pytest.fixture
def engine(tmp_path):
    local = LocalData(tmp_path / "local-data")
    return local.engine(local.create("Fuzz")["id"])


def test_mutated_spark_sql_in_a_local_data_project_runs_or_is_refused_by_name(engine):
    for query in _cases(13, 150):
        _read_or_refused(lambda q: engine.execute(q, dialect="spark"), query)


# ---- found by fuzzing


DEEP = [
    pytest.param("SELECT " + "(" * 2000 + "1" + ")" * 2000, id="brackets 2000 deep"),
    pytest.param("SELECT " + "array(" * 500 + "1" + ")" * 500, id="calls 500 deep"),
    pytest.param("SELECT " + "+".join(["1"] * 20000), id="20000 terms added"),  # ran 289 s, then RecursionError
    pytest.param("SELECT " + "struct(" * 20 + "1" + ")" * 20, id="struct 20 deep"),  # over a minute in sqlglot
    pytest.param("SELECT " + "string(" * 40, id="string( 40 deep, unclosed"),  # would never have ended
]


@pytest.mark.parametrize("query", DEEP)
def test_a_query_nested_too_deeply_is_refused_by_name_and_quickly(query):
    start = time.monotonic()
    with pytest.raises(ValueError, match="nested too deeply"):
        _translate(query)
    assert time.monotonic() - start < SLOW


def test_type_calls_nested_up_to_the_limit_still_read():
    depth = sql_dialects.TYPE_CALL_DEPTH
    start = time.monotonic()
    assert sql.run("SELECT " + "string(" * depth + "1" + ")" * depth + " AS s", "spark").to_pylist() == [{"s": "1"}]
    assert time.monotonic() - start < SLOW


def _best_time(text: str) -> float:
    """The quickest of three reads, in this process's CPU time with the garbage collector off: other test workers
    on the same machine take wall-clock time from a read, not CPU time, and a collection can land in any read."""
    best = float("inf")
    collecting = gc.isenabled()
    gc.disable()
    try:
        for _ in range(3):
            start = time.process_time()
            sql.parse(text, "spark")
            best = min(best, time.process_time() - start)
    finally:
        if collecting:
            gc.enable()
    return best


@pytest.mark.parametrize("query", [
    lambda n: "SELECT * FROM t WHERE " + " OR ".join(f"a = {i}" for i in range(n // 25)),
    lambda n: "SELECT a IN (" + ",".join(str(i) for i in range(n)) + ")",  # 20,000 items: 14.7 s before
], ids=["or chain", "in list"])
def test_a_long_or_chain_and_a_long_in_list_read_in_linear_time(query):
    # Each call and operator once walked its whole subtree for its first place in the text: 3,000 ORs took 48 s.
    # The check is how the time grows, not a number of seconds (a loaded Windows runner took 5.06 s for both).
    # Eight times the terms takes 8 to 9.3 times as long now (also with twice as many busy processes as cores),
    # and took 28 to 42 times as long before, so a bound of 16 leaves room on both sides. (4x the terms against
    # a bound of 9 was flaky with 8 test workers.)
    small, large = _best_time(query(2500)), _best_time(query(20000))
    assert large < 16 * small, f"{small:.3f}s for 2,500, {large:.3f}s for 20,000: grows faster than the length"


@pytest.mark.parametrize("query, says", [
    ("SELECT datediff('2024-02-01')", r"datediff\(\) is missing an argument"),  # was an AttributeError
    ("SELECT date_add(1)", "Could not read the query"),
    ("SELECT 1e101.5BD", "A number in the query isn't written the way Spark SQL reads one"),  # decimal.InvalidOperation
    ("SELECT from_json('{x', 'a INT'), {:1}", "Could not read the query"),  # AttributeError in sqlglot's DuckDB parser
])
def test_unreadable_calls_and_numbers_are_refused_by_name(query, says):
    with pytest.raises(ValueError, match=says):
        _translate(query)


def test_an_error_from_kratosforge_code_run_inside_sqlglot_is_raised_as_it_is(monkeypatch):
    """Only an AttributeError raised in sqlglot's own frames becomes a ParseError: one from a hook of ours
    that sqlglot.parse calls is a bug to see, not a query to refuse."""
    from sqlglot.dialects.spark import Spark
    from sqlglot.errors import ParseError

    def hook(args):
        raise AttributeError("a bug in a KratosForge hook")
    monkeypatch.setitem(Spark.Parser.FUNCTIONS, "KF_HOOK", hook)
    with pytest.raises(AttributeError, match="a bug in a KratosForge hook"):
        sql_dialects.parse("SELECT kf_hook(1)", read="spark")
    with pytest.raises(AttributeError, match="a bug in a KratosForge hook"):
        sql_dialects.parse_one("SELECT kf_hook(1)", read="spark")
    with pytest.raises(ParseError, match="Part of the query couldn't be read"):  # sqlglot's own AttributeError
        sql_dialects.parse("SELECT from_json('{x', 'a INT'), {:1}", read="duckdb")


def test_two_arguments_where_a_call_needs_them_still_read():
    assert sql.run("SELECT datediff('2024-02-01', '2024-01-01') AS d", "spark").to_pylist() == [{"d": 31}]


def test_an_unclosed_quote_in_a_local_data_project_is_refused_by_name(engine):
    # sqlglot's TokenError came out of the engine as it was.
    with pytest.raises(ValueError, match="Could not read the query as Spark SQL"):
        engine.execute("SELECT 'abc", dialect="spark")


def test_reading_back_from_a_place_matches_the_regular_expressions_it_replaced():
    r = random.Random(5)
    letters = "ab_Z9 (\t\n,ſ x"
    for _ in range(20000):
        text = "".join(r.choice(letters) for _ in range(r.randint(0, 10)))
        end = r.randint(0, len(text))
        word = re.search(r"([A-Za-z_]+)\s*$", text[:end])
        assert spark_sql_names._word_before(text, end) == (word.group(1) if word else None), (text, end)
        call = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*$", text[:end])
        assert spark_sql_names._call_before(text, end) == ((call.start(1), call.group(1)) if call else None)


# ---- queries from a small grammar give the answer worked out in Python


def _number(r: random.Random, depth: int) -> tuple[str, int]:
    if depth <= 0 or r.random() < 0.3:
        n = r.randint(-50, 50)
        return (str(n) if n >= 0 else f"({n})"), n
    kind = r.randrange(6)
    a, x = _number(r, depth - 1)
    b, y = _number(r, depth - 1)
    if kind == 0:
        return f"({a} + {b})", x + y
    if kind == 1:
        return f"({a} - {b})", x - y
    if kind == 2:
        return f"({a} * {b})", x * y
    if kind == 3:
        return f"abs({a})", abs(x)
    if kind == 4:
        return f"greatest({a}, {b})", max(x, y)
    return f"CASE WHEN {a} > 0 THEN {b} ELSE coalesce(NULL, -1) END", y if x > 0 else -1


def _text(r: random.Random, depth: int) -> tuple[str, str]:
    if depth <= 0 or r.random() < 0.3:
        s = "".join(r.choice("abcXYZ ") for _ in range(r.randint(0, 6)))
        return f"'{s}'", s
    kind = r.randrange(5)
    a, x = _text(r, depth - 1)
    if kind == 0:
        b, y = _text(r, depth - 1)
        return f"concat({a}, {b})", x + y
    if kind == 1:
        return f"upper({a})", x.upper()
    if kind == 2:
        return f"lower({a})", x.lower()
    if kind == 3:
        pos, n = r.randint(1, 4), r.randint(0, 4)
        return f"substring({a}, {pos}, {n})", x[pos - 1:pos - 1 + n]
    return f"trim({a})", x.strip(" ")


def test_queries_from_a_grammar_give_the_answer_python_gives():
    r = random.Random(20261118)
    for _ in range(40):
        numbers = [_number(r, 4) for _ in range(3)]
        texts = [_text(r, 2) for _ in range(3)]  # deeper, nested upper() and lower() grow the SQL threefold a level
        columns = [f"{e} AS n{i}" for i, (e, _) in enumerate(numbers)] + [f"{e} AS s{i}" for i, (e, _) in enumerate(texts)]
        query = "SELECT " + ", ".join(columns)
        row = sql.run(query, "spark").to_pylist()[0]
        expected = {f"n{i}": v for i, (_, v) in enumerate(numbers)} | {f"s{i}": v for i, (_, v) in enumerate(texts)}
        assert row == expected, query
