"""Snowflake SQL and Databricks SQL fuzzed: every query is translated and run on this PC, or refused with a
ValueError (or DuckDB's own named error) that says why, in good time.

Seeded and deterministic (the standard library's random, no new package): the documented examples in
snowflake_sql_parity and databricks_sql_parity, cut, flipped and filled with stray quotes, comments,
brackets, VARIANT paths, semi-structured calls and odd Unicode, through the ways a cell reaches DuckDB here
(snowflake_sql.translate, local_sql._databricks, and a DuckDB engine session). JSON text from
settings_fuzz's mutations goes to parse_json and try_parse_json. Queries made from a small grammar must
give the answer worked out here in Python.

The cases fuzzing found are kept below by name: each crashed, hung or answered wrongly before it was fixed.
tests/test_spark_sql_fuzz.py covers Spark SQL and the shared depth limit; this file doesn't repeat it.
"""

from __future__ import annotations

import ipaddress
import json
import random
import re
import time

import duckdb
import pytest

from fabric_desktop import databricks_variant, snowflake_sql, snowflake_sql_parity
from fabric_desktop.engines import databricks_sql_parity, local_sql, registry
from fabric_desktop.engines.contract import CellRequest, SessionConfig
from settings_fuzz import mutations

SNOWFLAKE = [c.sql for c in snowflake_sql_parity.CASES]
DATABRICKS = [c.sql for c in databricks_sql_parity.CASES] + [
    "SELECT parse_json('{\"a\":{\"b\":[1,2.50,\"x\"]}}'):a.b[1]",
    "SELECT variant_get(parse_json('[1, {\"k\": 2}]'), '$[1].k', 'int'), try_variant_get(parse_json('1'), '$', 'boolean')",
    "SELECT * FROM variant_explode(parse_json('{\"b\": 1, \"a\": [2]}'))",
    "SELECT try_parse_json('{x'), to_json(named_struct('a', 1))",
    "SELECT ip_host('10.1.2.3'), try_ip_network('10.0.0.0/8'), ip_cidr_contains('10.0.0.0/8', '10.9.9.9')",
    "SELECT url_decode('a+b%21'), try_url_decode('%zz'), hex(hmac('k', 'm')), bit_reverse(5), make_valid_utf8(x'61FF')",
]
PIECES = ["(", ")", "'", '"', "`", ";", "--", "/*", "*/", "$$", "\x00", "\\", ",", ":", "::", "[", "]", ".", "é",
          "😀", chr(0x202E), "﻿", "İ", "ǅ", "١٢", "SELECT", "FROM", "parse_json(", "try_parse_json(",
          "get_path(", "flatten(input => ", "variant_get(", "'$.a[0]'", ":a.b[0]", "::variant", "::number(38,37)",
          "1e999", "-0.0", "9" * 40, "'2024-02-30'", "'0-01-01'", "'{\"a\":", "'[1,", "\\ud800", "IN (", "NULL",
          "object_construct(", "url_decode(", "ip_host(", "bround(", "split_part(", "levenshtein("]
REFUSALS = (ValueError, duckdb.Error)
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
            s[r.randrange(len(s))] = chr(r.choice((r.randrange(0, 0x3000), r.randrange(0x10000, 0x10FFFF))))
        elif op == 3:
            s = s[:i]
        elif op == 4:
            s[i:i] = list(r.choice(PIECES)) * r.randint(1, 30)
        elif op == 5 and s:
            j = r.randrange(len(s))
            s[i:i] = s[j:j + r.randint(1, 20)]
    return "".join(s)


def _cases(corpus: list[str], seed: int, n: int) -> list[str]:
    r = random.Random(seed)
    return [mutate(r, r.choice(corpus)) for _ in range(n)]


def _timed(call, query: str) -> None:
    start = time.monotonic()
    try:
        call(query)
    except REFUSALS:
        pass
    except Exception as exc:  # noqa: BLE001 - the point of the test
        pytest.fail(f"{type(exc).__name__}: {exc} for {query!r}")
    assert time.monotonic() - start < SLOW, f"took {time.monotonic() - start:.1f}s: {query!r}"


def _translate_snowflake(query: str) -> None:
    for statement in snowflake_sql.translate(query).statements:
        assert statement.verdict in snowflake_sql.VERDICTS


@pytest.mark.parametrize("seed", [20261121, 1027])
def test_mutated_snowflake_sql_is_translated_or_refused_by_name(seed):
    for query in _cases(SNOWFLAKE, seed, 500):
        _timed(_translate_snowflake, query)


@pytest.mark.parametrize("seed", [20261121, 1556])
def test_mutated_databricks_sql_is_translated_or_refused_by_name(seed):
    for query in _cases(DATABRICKS, seed, 250):
        _timed(local_sql._databricks, query)


@pytest.fixture(scope="module")
def session():
    return registry.adapter("local-duckdb").open_session(SessionConfig("fuzz"))


def _cell(session, code: str, dialect: str):
    """The cell's result: its rows, or ("error", exception name, message)."""
    result = session.execute(CellRequest(code=code, language="sql", dialect=dialect))
    out = result.outputs[-1]
    if result.status != "ok":
        return ("error", out["ename"], out["evalue"])
    return out.get("rows")


def _named(ename: str, evalue: str) -> bool:
    """A refusal a person can read: a ValueError or the engine's Unsupported, or DuckDB's own named error that
    isn't a Python exception from a function of ours other than a ValueError."""
    if ename in ("ValueError", "Unsupported", "OutsideSession"):
        return True
    kind = getattr(duckdb, ename, None)
    if not (isinstance(kind, type) and issubclass(kind, duckdb.Error)) or ename == "Error":
        return False
    udf = re.search(r"Python exception occurred while executing the UDF: (\w+)", evalue)
    return "INTERNAL" not in evalue and (udf is None or udf.group(1) == "ValueError")


@pytest.mark.parametrize("dialect, corpus, seed", [("snowflake", SNOWFLAKE, 122), ("databricks", DATABRICKS, 179)])
def test_mutated_cells_run_or_are_refused_by_name(session, dialect, corpus, seed):
    for query in _cases(corpus, seed, 100):
        start = time.monotonic()
        got = _cell(session, query, dialect)
        if isinstance(got, tuple):
            assert _named(got[1], got[2]), (query, got)
        assert time.monotonic() - start < SLOW, query


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_damaged_json_is_read_or_refused_by_name(session, dialect):
    for label, data in mutations({"a": [1, 2.50, "x"], "b": {"c": None}, "d": True}, 7, 40):
        text = data.decode("utf-8", errors="replace").replace("\x00", "")
        literal = "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'" if dialect == "databricks" \
            else "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"
        for call in ("parse_json", "try_parse_json"):
            start = time.monotonic()
            got = _cell(session, f"SELECT {call}({literal}) AS v", dialect)
            if isinstance(got, tuple):
                assert _named(got[1], got[2]), (label, call, got)
            assert time.monotonic() - start < SLOW, (label, call)


# ---- found by fuzzing


@pytest.mark.parametrize("query", ["SELECT 'abc", "SELECT /* abc", "SELECT \"abc", "SELECT $$abc"])
def test_an_unclosed_quote_or_comment_is_refused_by_name(session, query):
    with pytest.raises(ValueError, match="Could not read the query"):  # sqlglot's TokenError came out as it was
        snowflake_sql.translate(query)
    with pytest.raises(ValueError, match="Could not read the query"):
        local_sql._databricks(query.replace("$$", "'"))
    assert _cell(session, query, "snowflake")[1] == "ValueError"
    assert _cell(session, query.replace("$$", "'"), "databricks")[1] == "ValueError"


@pytest.mark.parametrize("query, verdict", [
    ("CREATE TABLE src (k INT, v IN)", "difference"),  # an IN "constraint" has no kind: AttributeError
    ("CREATE TABLE t (a INT PRIMARY KEY, d IN PRIMARY KEY, x INT)", "difference"),
    ("SELECT date_part(year)", "not_translatable"),  # sqlglot's own AttributeError while it writes
])
def test_snowflake_statements_sqlglot_reads_oddly_get_a_verdict(query, verdict):
    assert [s.verdict for s in snowflake_sql.translate(query).statements] == [verdict]


@pytest.mark.parametrize("query, says", [
    ("SELECT 1" + "::variant" * 600, "nested too deeply"),  # RecursionError
    ("SELECT div0null(a, )", "missing an argument"),  # AttributeError in the DIV0 rewrite
    ("SELECT DIV0(a, b), DIV0NULL(a, ) FROM t", "missing an argument"),
])
def test_snowflake_queries_that_crashed_are_refused_by_name(query, says):
    with pytest.raises(ValueError, match=says):
        snowflake_sql.translate(query)


def test_a_snowflake_path_with_a_name_after_an_index_reads_the_json(session):
    # [0] after .Zed was taken for a 1-based list index ([1]) and read NULL, labelled SFD-009
    doc = "parse_json('[{\"Zed\": [8, null]}]')"
    assert _cell(session, f"SELECT {doc}[0].Zed[0]::string AS c", "snowflake") == [["8"]]
    assert _cell(session, f"SELECT v[0].Zed[0] AS c FROM (SELECT {doc} v)", "snowflake") == [["8"]]
    assert snowflake_sql.translate(f"SELECT v[0].Zed[0] FROM (SELECT {doc} v)").statements[0].notes == ()


@pytest.mark.parametrize("query, says", [
    ("SELECT variant_get(parse_json('1'), '$', 'DECIAL')", "can't read 'DECIAL' as a type"),  # ParseError
    ("SELECT variant_get(parse_json('1'), '$', 'DECIM/*AL')", "can't read 'DECIM/\\*AL' as a type"),  # TokenError
    ("SELECT zip_with(array('a'), a-0.0rrayeee('d'), (x, y) -> concat(x, y))", "Could not read the query"),
    ("SELECT levenshtein('kitten' 'sitting')", "levenshtein\\(\\) is missing an argument"),  # AttributeError
    ("SELECT split_part('11.12.13', 3)", "split_part\\(\\) is missing an argument"),  # KeyError
    ("SELECT bround(25, -99999999999999999999)", "bround\\(\\) can't round to"),  # 10 ** 1e20: never ended
    ("SELECT 1" + "::string" * 1200, "nested too deeply"),  # RecursionError
])
def test_databricks_queries_that_crashed_or_hung_are_refused_by_name(query, says):
    start = time.monotonic()
    with pytest.raises(ValueError, match=says):
        local_sql._databricks(query)
    assert time.monotonic() - start < SLOW


def test_a_map_whose_arguments_spark_reads_another_way_is_named(session):
    # x:k is one argument in Databricks SQL and two in Spark: naming the column read it again as Spark SQL,
    # where sqlglot's build_var_map stopped with an IndexError
    assert _cell(session, "SELECT map(1, 'a', x:k, 'b') AS m FROM (SELECT parse_json('{\"k\": 2}') x)",
                 "databricks") == [["[('1', 'a'), ('2', 'b')]"]]
    local_sql._databricks("SELECT map_contains_key(map(c, 'a', k:v, 'b'), 3) FROM t")


def test_bround_far_from_the_point_rounds_to_itself_or_zero(session):
    assert _cell(session, "SELECT bround(25, -2000000000) a, bround(2.5, 2000000000) b, bround(25, -401) c",
                 "databricks") == [[0, "2.5", 0]]


@pytest.mark.parametrize("text", ['{"\\ud800": 1, "a": 2}', '"\\ud800"', '["\\udc00x"]'])
def test_parse_json_of_half_a_utf16_pair_is_refused_by_name(session, text):
    # UnicodeEncodeError sorting the keys, or pybind11's "Unable to cast Python instance" handing it to DuckDB
    for safe in (False, True):
        with pytest.raises(ValueError, match="half of a UTF-16 pair"):
            databricks_variant.parse_json(text, safe)
    got = _cell(session, f"SELECT try_parse_json('{text.replace(chr(92), chr(92) * 2)}') AS v", "databricks")
    assert got[1] == "InvalidInputException" and "half of a UTF-16 pair" in got[2]
    assert databricks_variant.parse_json('["\\ud83d\\ude00"]', False) == '["😀"]'  # a whole pair is one character


def test_databricks_url_decode_is_its_own_and_nests(session):
    # Spark's template stood in front of it: %+1 refused, a nested call stopped on TRY with a volatile
    # function, and each level wrote its argument five times (six levels took 8 s)
    assert _cell(session, "SELECT url_decode('%+1%-0') AS a", "databricks") == [["\x01\x00"]]
    assert _cell(session, "SELECT url_decode(url_decode('a%252B1')) AS a", "databricks") == [["a+1"]]
    start = time.monotonic()
    assert _cell(session, "SELECT " + "url_decode(" * 30 + "'a%2B'" + ")" * 30 + " AS a", "databricks") == [["a "]]
    assert time.monotonic() - start < SLOW
    assert _cell(session, "SELECT url_decode('a+%21') AS a", "spark") == [["a !"]]  # Spark keeps its template


def test_a_long_chain_of_json_paths_runs_quickly(session):
    # DuckDB 1.x binds a -> chain in time that doubles with each link: 27 links ran for minutes
    for n in (3, 9, 30):
        start = time.monotonic()
        doc = "{\"a\":" * n + "7" + "}" * n
        assert _cell(session, "SELECT x" + ":a" * n + f" AS v FROM (SELECT parse_json('{doc}') x)",
                     "databricks") == [["7"]]
        assert time.monotonic() - start < SLOW


def test_a_chain_of_casts_reads_in_time_that_grows_with_its_length():
    # Each cast copied the casts inside it, twice: 80 casts took 17 s, 200 several minutes
    def took(n: int) -> float:
        start = time.process_time()
        local_sql._databricks("SELECT 1" + "::string" * n)
        return time.process_time() - start
    small, large = min(took(10) for _ in range(2)), min(took(40) for _ in range(2))
    assert large < 9 * small, f"{small:.2f}s for 10 casts, {large:.2f}s for 40"  # was 16 times as long or more


def test_an_error_duckdb_cut_mid_character_is_refused_by_name(session):
    # DuckDB cuts a long message at a byte count; its package then raised UnicodeDecodeError
    got = _cell(session, "select typeof(parse_json('18446744073709551615" + "ǅ" * 20 + "'))", "snowflake")
    assert got[1] == "ValueError" and "cut the message in the middle of a non-ASCII character" in got[2]


def test_values_python_cant_hold_are_shown_as_text(session):
    # year 0 stopped the cell's preview with an OverflowError, a struct of two unnamed fields with a ValueError
    assert _cell(session, "SELECT to_timestamp('0-12-31 00:12:00') AS a", "databricks") == [
        ["0000-12-31 00:12:00.000000"]]
    assert len(_cell(session, "SELECT (NULL, NULL)", "snowflake")) == 1


# ---- queries from a small grammar give the answer worked out in Python


KEYS = ["a", "b", "k_1", "Zed", "é"]


def _doc(r: random.Random, depth: int):
    if depth == 0 or r.random() < 0.3:
        return r.choice([r.randint(-10**6, 10**6), r.randint(-10**18, 10**18), r.choice([True, False]), None,
                         "".join(r.choice("abc xyz'é😀") for _ in range(r.randint(0, 5)))])
    if r.random() < 0.5:
        return [_doc(r, depth - 1) for _ in range(r.randint(0, 3))]
    return {k: _doc(r, depth - 1) for k in r.sample(KEYS, r.randint(0, 3))}


def _paths(value, prefix=()):
    yield prefix, value
    items = enumerate(value) if isinstance(value, list) else value.items() if isinstance(value, dict) else ()
    for step, inner in items:
        yield from _paths(inner, (*prefix, step))


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _snowflake_path(path) -> str:
    out = ""
    for step in path:
        if isinstance(step, int):
            out += f"[{step}]"
        else:
            name = step if step.isidentifier() and step.isascii() else f'"{step}"'
            out += (":" if not out else ".") + name
    return out


def test_snowflake_variant_paths_flatten_and_in_lists_give_python_answers(session):
    r = random.Random(20261121)
    for _ in range(25):
        value = _doc(r, 3)
        text = "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'"
        found = [(p, v) for p, v in _paths(value) if p and v is not None and not isinstance(v, (bool, list, dict))]
        if found:
            path, want = r.choice(found)
            query = f"SELECT parse_json({text}){_snowflake_path(path)}::string AS c"
            assert _cell(session, query, "snowflake") == [[want if isinstance(want, str) else str(want)]], query
        if isinstance(value, (list, dict)):
            query = f"SELECT COUNT(*) AS c FROM TABLE(FLATTEN(input => parse_json({text})))"
            assert _cell(session, query, "snowflake") == [[len(value)]], query
    for _ in range(5):
        values, x = [r.randint(-50, 50) for _ in range(r.randint(1, 1500))], r.randint(-60, 60)
        query = f"SELECT {x} IN ({', '.join(map(str, values))}) AS c"
        assert _cell(session, query, "snowflake") == [[x in values]]


def test_databricks_variants_ip_blocks_and_in_lists_give_python_answers(session):
    r = random.Random(20261121)
    for _ in range(15):
        value = _doc(r, 3)
        text = "'" + json.dumps(value, ensure_ascii=False).replace("\\", "\\\\").replace("'", "\\'") + "'"
        assert _cell(session, f"SELECT CAST(parse_json({text}) AS STRING) AS c", "databricks") == [[_canon(value)]]
        found = [(p, v) for p, v in _paths(value) if p]
        if found:
            path, want = r.choice(found)
            steps = "$" + "".join(f"[{s}]" if isinstance(s, int) else f'["{s}"]' for s in path)
            query = f"SELECT variant_get(parse_json({text}), '{steps}', 'string') AS c"
            expected = None if want is None else want if isinstance(want, str) else _canon(want)
            assert _cell(session, query, "databricks") == [[expected]], query
    for _ in range(10):
        bits, top = (32, ipaddress.IPv4Address) if r.random() < 0.5 else (128, ipaddress.IPv6Address)
        address, length = top(r.getrandbits(bits)), r.randint(0, bits)
        block = ipaddress.ip_network(f"{address}/{length}", strict=False)
        query = (f"SELECT ip_network('{address}/{length}') AS n, ip_network_last('{address}/{length}') AS l, "
                 f"ip_cidr_contains('{address}/{length}', '{address}') AS c")
        assert _cell(session, query, "databricks") == [[block.network_address.compressed,
                                                         block.broadcast_address.compressed, True]], query
    for _ in range(5):
        values, x = [r.randint(-50, 50) for _ in range(r.randint(1, 1500))], r.randint(-60, 60)
        query = f"SELECT {x} IN ({', '.join(map(str, values))}) AS c"
        assert _cell(session, query, "databricks") == [[x in values]]
