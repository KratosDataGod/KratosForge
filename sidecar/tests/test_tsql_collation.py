"""WR-246: local T-SQL text follows a Fabric Warehouse's default collation, Latin1_General_100_BIN2_UTF8.

https://learn.microsoft.com/fabric/data-warehouse/collation: the default is case-sensitive
Latin1_General_100_BIN2_UTF8; a warehouse can be made with Latin1_General_100_CI_AS_KS_WS_SC_UTF8 instead.
https://learn.microsoft.com/sql/relational-databases/collations/collation-and-unicode-support: a BIN2
collation sorts and compares by Unicode code point, case- and accent-sensitive ('B' < 'a', 'Z' < 'a',
'z' < 'é'). T-SQL compares text with ANSI padding, so 'a' and 'a  ' are equal: in =, BETWEEN, IN (also
with a subquery), = ANY, NULLIF, a simple CASE, joins, COUNT(DISTINCT ...), a window's PARTITION BY and
ORDER BY, and an ORDER BY key that another key follows. NULL sorts first ascending (ORDER BY's reference:
"NULL values are treated as the lowest possible values").

Each answer here is what T-SQL gives. What isn't rewritten runs with a note (GROUP BY, DISTINCT, UNION;
ORDER BY after a set operation) or is refused by name (another collation; JOIN ... USING, which isn't
T-SQL), so none differs silently.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import translate


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("collation") / "app" / "local-data")
    e = local.engine(local.create("Collation")["id"])
    e.execute("CREATE TABLE t (s VARCHAR(20), n INT)", dialect="tsql")
    e.execute("INSERT INTO t VALUES ('a', 1), ('a ', 2), ('A', 3), ('B', 4), ('é', 5), ('', 6), (NULL, 7), "
              "('z', 8), ('Z', 9), ('e', 10), ('a  ', 11)", dialect="tsql")
    e.execute("CREATE TABLE u (s VARCHAR(20))", dialect="tsql")
    e.execute("INSERT INTO u VALUES ('a'), ('b ')", dialect="tsql")
    return e


def run(engine, sql):
    r = engine.execute(sql, dialect="tsql")
    return [list(x) for x in r["rows"]], (r.get("translation") or {}).get("notes") or []


def rows(engine, sql):
    got, notes = run(engine, sql)
    assert not notes, notes
    return got


def col(engine, sql):
    return [r[0] for r in rows(engine, sql)]


# 'a', 'a ' and 'a  ' are rows 1, 2 and 11.
A_ROWS = [1, 2, 11]


def test_order_is_code_point_order_with_null_first(engine):
    assert col(engine, "SELECT s FROM t WHERE n NOT IN (2, 11) ORDER BY s") == [None, "", "A", "B", "Z", "a", "e",
                                                                              "z", "é"]
    assert col(engine, "SELECT s FROM t WHERE n NOT IN (2, 11) ORDER BY s DESC") == ["é", "z", "e", "a", "Z", "B",
                                                                                   "A", "", None]


@pytest.mark.parametrize(("sql", "answer"), [
    ("SELECT CASE WHEN 'B' < 'a' THEN 1 ELSE 0 END", 1),
    ("SELECT CASE WHEN 'Z' < 'a' THEN 1 ELSE 0 END", 1),
    ("SELECT CASE WHEN 'z' < 'é' THEN 1 ELSE 0 END", 1),
    ("SELECT CASE WHEN N'B' < N'a' THEN 1 ELSE 0 END", 1),
    ("SELECT CASE WHEN 'e' = 'é' THEN 1 ELSE 0 END", 0),
    ("SELECT CASE WHEN '' < 'A' THEN 1 ELSE 0 END", 1),
    ("SELECT CASE WHEN 'a' = 'a ' THEN 1 ELSE 0 END", 1),
    ("SELECT CASE WHEN 'a' <> 'a ' THEN 1 ELSE 0 END", 0),
    ("SELECT CASE WHEN '' = ' ' THEN 1 ELSE 0 END", 1),
    ("SELECT UPPER('é')", "É"),
    ("SELECT LOWER('É')", "é"),
])
def test_binary_comparisons(engine, sql, answer):
    assert rows(engine, sql) == [[answer]]


def test_min_and_max_are_code_point_order(engine):
    assert rows(engine, "SELECT MIN(s), MAX(s) FROM t") == [["", "é"]]
    # 'a', 'a ' and 'a  ' are equal: T-SQL may show any of them
    assert rows(engine, "SELECT MAX(s) FROM t WHERE n IN (1, 2, 11)")[0][0] in ("a", "a ", "a  ")


def test_like_is_case_sensitive(engine):
    assert col(engine, "SELECT n FROM t WHERE s LIKE 'a%' ORDER BY n") == A_ROWS
    assert col(engine, "SELECT n FROM t WHERE s LIKE 'A%' ORDER BY n") == [3]


@pytest.mark.parametrize("sql", [
    "SELECT n FROM t WHERE s = 'a' ORDER BY n",
    "SELECT n FROM t WHERE s = N'a' ORDER BY n",
    "SELECT n FROM t WHERE s IN ('a', 'q') ORDER BY n",
    "SELECT n FROM t WHERE s BETWEEN 'a' AND 'a' ORDER BY n",
    "SELECT n FROM t WHERE s IN (SELECT s FROM u) ORDER BY n",
    "SELECT n FROM t WHERE s IN (SELECT s AS v FROM u) ORDER BY n",
    "SELECT n FROM t WHERE s = ANY (SELECT s FROM u) ORDER BY n",
    "SELECT t.n FROM t JOIN u ON t.s = u.s ORDER BY t.n",
    "SELECT t.n FROM t JOIN u ON u.s = t.s + '' ORDER BY t.n",
    "SELECT n FROM t WHERE EXISTS (SELECT 1 FROM u WHERE u.s = t.s) ORDER BY n",
    "SELECT n FROM t WHERE NULLIF(s, 'a') IS NULL AND s IS NOT NULL ORDER BY n",
    "SELECT n FROM t WHERE CASE s WHEN 'a' THEN 1 ELSE 0 END = 1 ORDER BY n",
])
def test_trailing_spaces_dont_count_in_what_compares(engine, sql):
    assert col(engine, sql) == A_ROWS


def test_in_a_union_subquery_trailing_spaces_dont_count(engine):
    # 'b ' in u is 'b'; 'a', 'a ' and 'a  ' match 'a'
    got, _ = run(engine, "SELECT n FROM t WHERE s IN (SELECT s FROM u UNION SELECT 'e ') ORDER BY n")
    assert [r[0] for r in got] == [1, 2, 10, 11]


def test_not_in_and_null(engine):
    assert col(engine, "SELECT n FROM t WHERE s NOT IN ('a') ORDER BY n") == [3, 4, 5, 6, 8, 9, 10]
    assert col(engine, "SELECT NULLIF(s, 'b') FROM t WHERE n = 2") == ["a "]  # the value shown stays as it is


def test_a_self_join_on_text_pairs_trailing_spaces(engine):
    assert rows(engine, "SELECT COUNT(*) FROM t a JOIN t b ON a.s = b.s") == [[16]]  # 3 x 3 for 'a', 7 others


def test_count_distinct_counts_trailing_spaces_once(engine):
    assert rows(engine, "SELECT COUNT(DISTINCT s) FROM t") == [[8]]


def test_windows_tie_and_partition_text_without_trailing_spaces(engine):
    ranks = dict(rows(engine, "SELECT n, RANK() OVER (ORDER BY s) FROM t"))
    assert [ranks[n] for n in A_ROWS] == [6, 6, 6] and ranks[10] == 9
    numbers = dict(rows(engine, "SELECT n, ROW_NUMBER() OVER (PARTITION BY s ORDER BY n) FROM t"))
    assert [numbers[n] for n in A_ROWS] == [1, 2, 3]
    totals = {n: int(v) for n, v in rows(engine, "SELECT n, SUM(n) OVER (ORDER BY s) FROM t WHERE n IN (1, 2, 11, 10)")}
    assert totals == {1: 14, 2: 14, 11: 14, 10: 24}  # RANGE frame: 'a', 'a ' and 'a  ' are peers


@pytest.mark.parametrize("sql", [
    "SELECT s, n FROM t WHERE n IN (1, 2, 11) ORDER BY s DESC, n DESC",
    "SELECT s AS k, n FROM t WHERE n IN (1, 2, 11) ORDER BY k DESC, n DESC",
    "SELECT s, n FROM t WHERE n IN (1, 2, 11) ORDER BY 1 DESC, 2 DESC",
    "SELECT t.s, t.n FROM t JOIN u ON u.s = 'a' WHERE t.n IN (1, 2, 11) ORDER BY t.s, t.n DESC",
])
def test_order_by_text_then_another_key_orders_equal_text_by_the_next_key(engine, sql):
    assert rows(engine, sql) == [["a  ", 11], ["a ", 2], ["a", 1]]


def test_order_by_an_alias_that_shadows_a_column_orders_by_the_alias(engine):
    got = rows(engine, "SELECT UPPER(s) AS s, n FROM t WHERE n IN (1, 3, 4) ORDER BY s, n DESC")
    assert got == [["A", 3], ["A", 1], ["B", 4]]  # by the table's s it would be A, B, a


def test_what_isnt_rewritten_says_so(engine):
    group_note = "T-SQL counts text that differs only in trailing spaces ('a' and 'a  ') as one value in GROUP BY"
    for sql in ("SELECT s, COUNT(*) FROM t GROUP BY s", "SELECT DISTINCT s FROM t",
                "SELECT s FROM t UNION SELECT s FROM u"):
        assert any(n.startswith(group_note) for n in run(engine, sql)[1]), sql
    _, notes = run(engine, "SELECT s, n FROM t UNION ALL SELECT s, 0 FROM u ORDER BY s, n")
    assert any("After a UNION, INTERSECT or EXCEPT" in n for n in notes)


def test_another_collation_is_refused_by_name(engine):
    for name in ("Latin1_General_100_CI_AS_KS_WS_SC_UTF8", "Latin1_General_100_CI_AS"):
        with pytest.raises(ValueError, match=f"COLLATE {name} isn't mapped yet: text here compares as a Fabric "
                                             r"Warehouse's default collation \(Latin1_General_100_BIN2_UTF8\)"):
            engine.execute(f"SELECT s FROM t ORDER BY s COLLATE {name}", dialect="tsql")


def test_the_default_collation_and_database_default_run(engine):
    assert col(engine, "SELECT n FROM t WHERE s COLLATE Latin1_General_100_BIN2_UTF8 = 'a' ORDER BY n") == A_ROWS
    assert col(engine, "SELECT n FROM t WHERE s COLLATE database_default = 'A' ORDER BY n") == [3]


def test_a_code_page_binary_collation_runs_with_a_note(engine):
    got, notes = run(engine, "SELECT CASE WHEN 'a' = 'A' COLLATE Latin1_General_100_BIN2 THEN 1 ELSE 0 END")
    assert got == [[0]]
    assert notes == [("COLLATE Latin1_General_100_BIN2 runs here as Latin1_General_100_BIN2_UTF8. They agree on ASCII "
                     "text, but a warehouse first converts other text to Latin1_General_100_BIN2's code page, which can "
                     "order it another way (code page 1252 puts '€' before 'é') or turn a character it hasn't got "
                     "into '?'. Use Latin1_General_100_BIN2_UTF8 to compare as a warehouse does by default.")]


def test_column_collations(engine):
    engine.execute("CREATE TABLE c (s VARCHAR(20) COLLATE Latin1_General_100_BIN2_UTF8 NOT NULL)", dialect="tsql")
    engine.execute("INSERT INTO c VALUES ('a')", dialect="tsql")
    assert col(engine, "SELECT COUNT(*) FROM c WHERE s = 'A'") == [0]
    with pytest.raises(ValueError, match=r"a column with COLLATE Latin1_General_100_CI_AS_KS_WS_SC_UTF8 \(column s\) "
                                         "isn't mapped yet, because a Fabric Warehouse's tables take"):
        engine.execute("CREATE TABLE ci (s VARCHAR(20) COLLATE Latin1_General_100_CI_AS_KS_WS_SC_UTF8)",
                       dialect="tsql")


def test_join_using_is_refused_as_not_tsql(engine):
    with pytest.raises(ValueError, match=r"JOIN \.\.\. USING isn't T-SQL, so a Fabric Warehouse refuses it\. "
                                         r"Use JOIN \.\.\. ON\."):
        engine.execute("SELECT n FROM t JOIN u USING (s)", dialect="tsql")


def test_untyped_subquery_comparisons_are_noted():
    _, notes = translate("SELECT n FROM nowhere WHERE s IN (SELECT 'a')")
    assert any("trailing spaces" in n for n in notes)


# Quality on #3179: a value _collated copies (NULLIF's, an ORDER BY key that names an output column) still gets the
# rewrites that run after it, such as + on text becoming ||.
@pytest.mark.parametrize("sql, want", [
    ("SELECT s + 'z' AS w, n FROM p ORDER BY w, n", [["a z", 2], ["az", 1], ["bz", 3]]),
    ("SELECT s + 'z' AS w, n FROM p ORDER BY 1, 2", [["a z", 2], ["az", 1], ["bz", 3]]),
    ("SELECT s + g AS w, n FROM p ORDER BY w, n", [["a x", 2], ["ax", 1], ["bx", 3]]),
    ("SELECT NULLIF(s + 'z', 'az') FROM p ORDER BY n", [[None], ["a z"], ["bz"]]),
])
def test_a_copied_value_still_joins_text_with_plus(engine, sql, want):
    engine.execute("DROP TABLE IF EXISTS p", dialect="tsql")
    engine.execute("CREATE TABLE p (s VARCHAR(10), g VARCHAR(10), n INT)", dialect="tsql")
    engine.execute("INSERT INTO p VALUES ('a', 'x', 1), ('a ', 'x', 2), ('b', 'x', 3)", dialect="tsql")
    assert engine.execute(sql, dialect="tsql")["rows"] == want


def test_a_column_collate_database_default_runs(engine):
    engine.execute("CREATE TABLE #d (s VARCHAR(10) COLLATE database_default)", dialect="tsql")
