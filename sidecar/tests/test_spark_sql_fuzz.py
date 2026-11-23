"""Spark SQL fuzzed: every query is read and run on this PC, or refused with a ValueError that says why.

Seeded and deterministic (the standard library's random, no new package): valid queries cut, flipped and
filled with stray brackets, quotes, comments, control characters and Unicode, through the three ways Spark
SQL reaches DuckDB here (sql.parse and duckdb_sql, sql.run, a local data project's engine). None may stop
with an error a person can't read (AttributeError, IndexError, RecursionError, sqlglot's own TokenError...)
or take long. Queries made from a small grammar must give the answer worked out here in Python.

The cases fuzzing found are kept below by name: each crashed or ran for minutes before it was fixed.
"""

from __future__ import annotations

import contextlib
import random
import re
import sys

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
# The most Python calls one query may make (generators resumed included). Counted, not timed: a limit of 5 s a
# query failed on busy CI runners with nothing changed. The most any query here makes is about 3 million (the
# type calls at the limit, run), about a second's work on a quiet PC; 10 million is about 3 s, so the bound is
# no looser than the 5 s it replaces. A read that goes past it is stopped there, so a regression fails at once
# instead of hanging.
CALLS = 10_000_000


class _TooMuchWork(BaseException):
    """Stops a read that made more calls than its bound (a BaseException, so no `except Exception` keeps it)."""


class _Counted:
    calls = 0


@contextlib.contextmanager
def _counting(bound: int):
    """Counts the Python calls made inside the block; past ``bound`` the work is stopped with _TooMuchWork.
    The count stays past the bound if the code inside catches the stop, so assert on it afterwards."""
    counted = _Counted()

    def profile(frame, event, arg):
        if event == "call":
            counted.calls += 1
            if counted.calls > bound:
                raise _TooMuchWork(f"more than {bound} calls")
    sys.setprofile(profile)
    try:
        yield counted
    finally:
        sys.setprofile(None)


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
    try:
        with _counting(CALLS) as counted:
            call(query)
    except _TooMuchWork:
        pytest.fail(f"more than {CALLS} calls: {query!r}")
    except REFUSALS:
        pass
    except RuntimeError as exc:  # a table name that isn't on this PC: sql.run looks for it in the lakehouse
        assert str(exc).startswith("No lakehouse is attached"), (query, exc)
    except Exception as exc:  # noqa: BLE001 - the point of the test
        pytest.fail(f"{type(exc).__name__}: {exc} for {query!r}")
    assert counted.calls <= CALLS, f"more than {CALLS} calls: {query!r}"


def _translate(query: str) -> None:
    for statement in sql.parse(query, "spark"):
        sql.duckdb_sql(statement)


@pytest.mark.parametrize("seed", [20261118, 7])
def test_mutated_spark_sql_is_translated_or_refused_by_name(seed):
    for query in _cases(seed, 400):
        _read_or_refused(_translate, query)


def test_mutated_spark_sql_runs_or_is_refused_by_name():
    sql.run("SELECT 1", "spark")  # anything set up once per process is set up before counting
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
    engine.execute("SELECT 1", dialect="spark")  # the project's engine is set up before counting
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
    # Counted, not timed (a wall-clock limit failed on busy CI runners with nothing changed): the refusal comes
    # after a number of Python calls that grows with the query's length (the most, 20,000 terms added, makes
    # about 120 a character), never a factor per level. A read that goes past the bound is stopped there.
    bound = 200 * len(query)
    with _counting(bound) as counted, pytest.raises(ValueError, match="nested too deeply"):
        _translate(query)
    assert counted.calls <= bound


def test_type_calls_nested_up_to_the_limit_still_read():
    # sqlglot's reading doubles with each level of type calls: at the limit, read and run make about 3 million
    # calls (0.7 million of them sqlglot's parse); four levels more would be over 11 million in the parse alone.
    depth = sql_dialects.TYPE_CALL_DEPTH
    sql.run("SELECT 1", "spark")  # anything set up once per process is set up before counting
    with _counting(CALLS) as counted:
        got = sql.run("SELECT " + "string(" * depth + "1" + ")" * depth + " AS s", "spark").to_pylist()
    assert got == [{"s": "1"}]
    assert counted.calls <= CALLS


class _Text(str):
    """Text that counts the characters read from it by index or slice."""
    read = 0

    def __getitem__(self, key):
        got = str.__getitem__(self, key)
        _Text.read += len(got)
        return got


def _work(text: str, monkeypatch) -> tuple[int, int]:
    """What one read of ``text`` does, counted the same on any machine however busy, where its time is not:
    the Python function calls it makes (generators resumed included), and the characters of the query that
    naming a constant or call reads back from where it starts."""
    for name in ("_word_before", "_call_before"):
        real = getattr(spark_sql_names, name)
        monkeypatch.setattr(spark_sql_names, name, lambda text, end, real=real: real(_Text(text), end))
    calls, _Text.read = 0, 0

    def profile(frame, event, arg):
        nonlocal calls
        if event == "call":
            calls += 1
    sys.setprofile(profile)
    try:
        sql.parse(text, "spark")
    finally:
        sys.setprofile(None)
        monkeypatch.undo()
    return calls, _Text.read


@pytest.mark.parametrize("query", [
    lambda n: "SELECT * FROM t WHERE " + " OR ".join(f"a = {i}" for i in range(n // 25)),
    lambda n: "SELECT a IN (" + ",".join(str(i) for i in range(n)) + ")",  # 20,000 items: 14.7 s before
], ids=["or chain", "in list"])
def test_a_long_or_chain_and_a_long_in_list_read_in_linear_time(query, monkeypatch):
    # Each call and operator once walked its whole subtree for its first place in the text, and each constant
    # searched the whole text before it: 3,000 ORs took 48 s. The check is how the work grows with the length,
    # counted rather than timed (a CPU-time ratio was flaky beside busy test workers): eight times the terms
    # makes about eight times the calls and the characters read back; either regression made them grow with
    # the square of the length. The bound of 16 is the one the timed check had.
    sql.parse(query(100), "spark")  # anything read once per process is read before counting
    (small_calls, small_read), (large_calls, large_read) = _work(query(2500), monkeypatch), _work(query(20000), monkeypatch)
    assert large_calls < 16 * small_calls, f"{small_calls} calls for 2,500, {large_calls} for 20,000: grows faster than the length"
    assert large_read < 16 * max(small_read, 1), f"{small_read} characters read back for 2,500, {large_read} for 20,000"


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
