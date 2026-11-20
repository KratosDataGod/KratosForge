"""WR-210: local T-SQL text gives a Fabric Warehouse's answers at its edges.

A warehouse's default collation is Latin1_General_100_BIN2_UTF8: text compares by code point, so case
counts, and with ANSI padding, so trailing spaces don't. These tests cover comparisons, LIKE, ORDER BY,
LEN and DATALENGTH, CHARINDEX and PATINDEX, CONCAT and + with NULL, STRING_AGG with WITHIN GROUP,
TRIM, LTRIM and RTRIM, and REPLACE. Each value is what T-SQL's reference gives. A difference that
remains (grouping text that differs only in trailing spaces) runs with a note, and another collation
is refused by name, so neither differs silently.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("strings") / "app" / "local-data")
    e = local.engine(local.create("Strings")["id"])
    # 'a  ' and 'b ' end in spaces; n orders the rows.
    e.execute("CREATE TABLE dbo.t AS SELECT * FROM (VALUES ('a', 1), ('B', 2), ('A', 3), ('b ', 4), ('a  ', 5)) v(s, n)")
    e.execute("CREATE TABLE dbo.one AS SELECT 'ab  ' AS sp, CAST(NULL AS VARCHAR) AS nul, CAST(1 AS BOOLEAN) AS yes")
    return e


def one(engine, sql):
    r = engine.execute(sql, dialect="tsql")
    return r["rows"][0][0], (r.get("translation") or {}).get("notes") or []


def rows(engine, sql):
    return [list(x) for x in engine.execute(sql, dialect="tsql")["rows"]]


# (T-SQL expression on dbo.one, T-SQL's answer)
CASES = [
 # comparisons: case counts, trailing spaces don't, code point order (B < a)
 ("CASE WHEN 'a' = 'A' THEN 1 ELSE 0 END", 0),
 ("CASE WHEN 'a' = 'a  ' THEN 1 ELSE 0 END", 1),
 ("CASE WHEN 'a' <> 'a ' THEN 1 ELSE 0 END", 0),
 ("CASE WHEN 'a ' > 'a' THEN 1 ELSE 0 END", 0),
 ("CASE WHEN 'B' < 'a' THEN 1 ELSE 0 END", 1),
 ("CASE WHEN 'a' IN ('a  ', 'b') THEN 1 ELSE 0 END", 1),
 ("CASE WHEN sp = 'ab' THEN 1 ELSE 0 END", 1),
 # LIKE: case counts; the text's trailing spaces don't, the pattern's do
 ("CASE WHEN 'abc' LIKE 'ABC' THEN 1 ELSE 0 END", 0),
 ("CASE WHEN 'abc  ' LIKE 'abc' THEN 1 ELSE 0 END", 1),
 ("CASE WHEN 'abc ' LIKE 'abc_' THEN 1 ELSE 0 END", 1),
 ("CASE WHEN 'abc' LIKE 'abc ' THEN 1 ELSE 0 END", 0),
 ("CASE WHEN sp LIKE 'ab' THEN 1 ELSE 0 END", 1),
 ("CASE WHEN sp NOT LIKE 'ab' THEN 1 ELSE 0 END", 0),
 ("CASE WHEN sp LIKE 'a[a-c]' THEN 1 ELSE 0 END", 1),
 ("CASE WHEN 'a% ' LIKE 'a!%' ESCAPE '!' THEN 1 ELSE 0 END", 1),
 # LEN and DATALENGTH
 ("LEN('ab  ')", 2), ("LEN('  ab')", 4), ("LEN('')", 0), ("LEN(nul)", None), ("LEN(sp)", 2),
 ("DATALENGTH('ab  ')", 4), ("DATALENGTH(sp)", 4), ("DATALENGTH(N'ab')", 4), ("DATALENGTH('é')", 2),
 ("DATALENGTH(CAST('ab' AS CHAR(6)))", 6), ("LEN(CAST('ab' AS CHAR(6)))", 2),
 # CHARINDEX and PATINDEX: case counts, 0 when not found, a start of 0 or below searches from 1
 ("CHARINDEX('b', 'abcb')", 2), ("CHARINDEX('b', 'abcb', 3)", 4), ("CHARINDEX('B', 'abcb')", 0),
 ("CHARINDEX('z', 'abc')", 0), ("CHARINDEX('b', 'abcb', 0)", 2), ("CHARINDEX('b', 'abcb', -5)", 2),
 ("CHARINDEX('b', 'abcb', 9)", 0), ("CHARINDEX('', 'abc')", 0), ("CHARINDEX(nul, 'abc')", None),
 ("CHARINDEX('b', nul)", None), ("CHARINDEX('a ', 'a')", 0),
 ("PATINDEX('%b%', 'abc')", 2), ("PATINDEX('%B%', 'abc')", 0), ("PATINDEX('b%', 'abc')", 0),
 ("PATINDEX('%[0-9]%', 'ab12')", 3), ("PATINDEX('%[%]%', 'a%b')", 2), ("PATINDEX('a_c', 'abc')", 1),
 # CONCAT skips NULL, + doesn't; numbers and bits as T-SQL writes them
 ("CONCAT('a', NULL, 'b')", "ab"), ("CONCAT(nul, nul)", ""), ("'a' + nul", None), ("'ab' + 'c'", "abc"),
 ("CONCAT_WS(',', 'a', NULL, 'b')", "a,b"), ("CONCAT('a', 1)", "a1"), ("CONCAT('a', 1.50)", "a1.50"),
 ("CONCAT('a', CAST(1.5 AS DECIMAL(5,2)))", "a1.50"), ("CONCAT('a', CAST(1.5 AS FLOAT))", "a1.5"),
 ("CONCAT('a', yes)", "a1"), ("CONCAT('a', CAST(0 AS BIT))", "a0"), ("CAST(1.50 AS VARCHAR(10))", "1.50"),
 ("CAST(CAST(1 AS BIT) AS VARCHAR(5))", "1"),
 # TRIM, LTRIM, RTRIM (spaces only, or the characters given), REPLACE (case counts)
 ("'[' + TRIM('  a  ') + ']'", "[a]"), ("'[' + LTRIM('  a  ') + ']'", "[a  ]"), ("'[' + RTRIM('  a  ') + ']'", "[  a]"),
 ("'[' + LTRIM(CHAR(9) + 'a') + ']'", "[\ta]"), ("TRIM('xa' FROM 'xxaxbxa')", "b"), ("LTRIM('xxab', 'x')", "ab"),
 ("RTRIM('abxx', 'x')", "ab"), ("TRIM(nul)", None),
 ("REPLACE('aAa', 'a', 'x')", "xAx"), ("REPLACE('a  ', ' ', 'x')", "axx"), ("REPLACE('abc', '', 'x')", "abc"),
 ("REPLACE('abc', 'b', NULL)", None), ("REPLACE(nul, 'b', 'x')", None),
]


@pytest.mark.parametrize(("sql", "want"), CASES, ids=[c[0] for c in CASES])
def test_each_string_function_gives_tsqls_answer(engine, sql, want):
    got, notes = one(engine, f"SELECT {sql} AS v FROM dbo.one")
    assert got == want
    assert notes == [], "a checked case runs without a caveat"


def test_order_by_and_string_agg_order_by_code_point(engine):
    assert rows(engine, "SELECT s FROM dbo.t ORDER BY s, n") == [["A"], ["B"], ["a"], ["a  "], ["b "]]
    assert one(engine, "SELECT STRING_AGG(s, ',') WITHIN GROUP (ORDER BY n DESC) FROM dbo.t")[0] == "a  ,b ,A,B,a"
    assert one(engine, "SELECT STRING_AGG(n, '-') WITHIN GROUP (ORDER BY n) FROM dbo.t")[0] == "1-2-3-4-5"
    assert one(engine, "SELECT STRING_AGG(s, ',') WITHIN GROUP (ORDER BY s DESC, n) FROM dbo.t WHERE s <> 'a'")[0] \
        == "b ,B,A"


def test_string_agg_skips_nulls_and_is_null_for_no_rows(engine):
    assert one(engine, "SELECT STRING_AGG(x, ',') FROM (VALUES ('a'), (NULL), ('b')) v(x)")[0] == "a,b"
    assert one(engine, "SELECT STRING_AGG(nul, ',') FROM dbo.one")[0] is None
    assert one(engine, "SELECT STRING_AGG(s, ',') FROM dbo.t WHERE n > 100")[0] is None


def test_a_filter_on_text_ignores_trailing_spaces_and_keeps_case(engine):
    assert one(engine, "SELECT COUNT(*) FROM dbo.t WHERE s = 'a'")[0] == 2
    assert one(engine, "SELECT COUNT(*) FROM dbo.t WHERE s = 'A'")[0] == 1
    assert one(engine, "SELECT COUNT(*) FROM dbo.t WHERE s LIKE 'a'")[0] == 2
    assert one(engine, "SELECT COUNT(*) FROM dbo.t WHERE s LIKE 'b'")[0] == 1
    assert one(engine, "SELECT COUNT(*) FROM dbo.t WHERE s NOT LIKE 'a'")[0] == 3
    assert one(engine, "SELECT COUNT(*) FROM dbo.t WHERE s LIKE '[ab]'")[0] == 3


GROUPED = "T-SQL counts text that differs only in trailing spaces ('a' and 'a  ') as one value"


@pytest.mark.parametrize("sql", ["SELECT s, COUNT(*) FROM dbo.t GROUP BY s", "SELECT DISTINCT s FROM dbo.t",
                                 "SELECT COUNT(DISTINCT s) FROM dbo.t", "SELECT 'a' UNION SELECT 'a '",
                                 "SELECT s FROM dbo.t INTERSECT SELECT 'a'", "SELECT s FROM dbo.t EXCEPT SELECT 'a'"])
def test_grouping_text_says_trailing_spaces_stay_apart(engine, sql):
    notes = engine.execute(sql, dialect="tsql")["translation"]["notes"]
    assert any(n.startswith(GROUPED) for n in notes), notes


@pytest.mark.parametrize("sql", ["SELECT n, COUNT(*) FROM dbo.t GROUP BY n", "SELECT DISTINCT n FROM dbo.t",
                                 "SELECT RTRIM(s), COUNT(*) FROM dbo.t GROUP BY RTRIM(s)", "SELECT 'a' UNION SELECT 'b'",
                                 "SELECT s FROM dbo.t UNION ALL SELECT 'a '"])
def test_grouping_that_cant_differ_has_no_note(engine, sql):
    assert not (engine.execute(sql, dialect="tsql").get("translation") or {}).get("notes")


def test_rtrim_gives_tsqls_groups(engine):
    assert sorted(rows(engine, "SELECT RTRIM(s), COUNT(*) FROM dbo.t GROUP BY RTRIM(s)")) == [["A", 1], ["B", 1], ["a", 2],
                                                                                            ["b", 1]]


def test_a_binary_collation_runs_and_any_other_is_refused_by_name(engine):
    assert one(engine, "SELECT CASE WHEN 'a' = 'a' COLLATE Latin1_General_100_BIN2_UTF8 THEN 1 ELSE 0 END")[0] == 1
    assert one(engine, "SELECT CASE WHEN 'a' = 'A' COLLATE Latin1_General_100_BIN2 THEN 1 ELSE 0 END")[0] == 0
    for name in ("Latin1_General_100_CI_AS_KS_WS_SC_UTF8", "SQL_Latin1_General_CP1_CI_AS", "Latin1_General_CS_AS"):
        with pytest.raises(ValueError, match=f"COLLATE {name} isn't mapped yet: text here compares as a Fabric "
                                             r"Warehouse's default collation \(Latin1_General_100_BIN2_UTF8\)"):
            engine.execute(f"SELECT s FROM dbo.t WHERE s = 'a' COLLATE {name}", dialect="tsql")


def test_a_like_on_a_value_whose_type_cant_be_told_is_noted():
    from fabric_desktop.localdata.tsql import translate
    _, notes = translate("SELECT 1 FROM q WHERE a LIKE 'x'")
    assert any("'a  ' LIKE 'a'" in n for n in notes), notes


# Not in T-SQL's reference: a Fabric Warehouse gave these answers in live run 11 (smoke-test flow 17).
@pytest.mark.parametrize(("expr", "local"), [
    ("CASE WHEN 'abc  ' LIKE 'abc_' THEN 1 ELSE 0 END", 1),
    ("CASE WHEN 'abc  ' LIKE 'abc__' THEN 1 ELSE 0 END", 1),
    ("CASE WHEN 'abc  ' LIKE 'abc___' THEN 1 ELSE 0 END", 0),
    ("CASE WHEN 'abc  ' LIKE 'abc' THEN 1 ELSE 0 END", 1),
    ("CASE WHEN 'abc  ' NOT LIKE 'abc_' THEN 1 ELSE 0 END", 0),
    ("CASE WHEN 'a%  ' LIKE 'a!%_' ESCAPE '!' THEN 1 ELSE 0 END", 1),
    ("CASE WHEN 'abc' LIKE 'abc ' THEN 1 ELSE 0 END", 0),
    ("CHARINDEX('b', 'abc', NULL)", None),
    ("CAST(CAST('2024-02-29 13:04:05' AS DATETIME2(6)) AS VARCHAR(30))", "2024-02-29 13:04:05.000000"),
    ("CAST(CAST('2024-02-29 13:04:05.5' AS DATETIME2(3)) AS VARCHAR(30))", "2024-02-29 13:04:05.500"),
    ("CAST(CAST('13:04:05.25' AS TIME(3)) AS VARCHAR(30))", "13:04:05.250"),
    ("CAST(CAST('2024-02-29 13:04:05' AS DATETIME2(6)) AS VARCHAR(10))", "2024-02-29"),
])
def test_edges_a_live_warehouse_answered_give_its_answer(engine, expr, local):
    assert one(engine, f"SELECT {expr}")[0] == local
