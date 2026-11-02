"""Characterization of the T-SQL "loud gaps" on the local engine (enterprise audit, T-SQL parity notes
in the audit's characterization folder): statements that stop with DuckDB's own error instead of the
answer, or of the "can't be run on the local engine" message.

Each expected value is SQL Server's documented answer, UNVERIFIED on a Fabric Warehouse (no tenant
calls). Each case is xfail(strict=True): the PR that maps or refuses a function removes its marker, and
a case that starts passing without that fails the run. The guard test below is not xfail: a loud gap
may stop with an error, but it must never give a wrong answer.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("app") / "local-data")
    e = local.engine(local.create("TSQL gaps")["id"])
    e.execute("CREATE TABLE dbo.orders AS SELECT * FROM (VALUES (1, 2), (2, 2), (3, 5)) v(id, boxes)")
    yield e
    local.close_all()


def first(engine, sql):
    return engine.execute(sql, dialect="tsql")["rows"][0][0]


# (case, T-SQL, SQL Server's documented answer as the first cell). These run on the local engine now.
MAPPED = [  # decimals come back as text, as the engine sends them
    ("ROUND truncate mode", "SELECT ROUND(123.456, 1, 1)", "123.4"),
    ("ROUND truncate mode, negative", "SELECT ROUND(-123.456, 1, 1)", "-123.4"),
    ("ROUND mode 0 rounds", "SELECT ROUND(123.456, 1, 0)", "123.5"),
    ("CHOOSE", "SELECT CHOOSE(2, 'a', 'b', 'c')", "b"),
    ("CHOOSE out of range", "SELECT CHOOSE(4, 'a', 'b', 'c')", None),
    ("CAST 'true' AS bit", "SELECT CAST('true' AS bit)", True),
    ("CAST 'FALSE' AS bit", "SELECT CAST('FALSE' AS bit)", False),
    ("CAST '2' AS bit", "SELECT CAST('2' AS bit)", True),
    ("CAST 1 AS bit", "SELECT CAST(1 AS bit)", True),
    ("CAST 0 AS bit", "SELECT CAST(0 AS bit)", False),
    ("TRY_CAST 'yes' AS bit", "SELECT TRY_CAST('yes' AS bit)", None),
    ("DATALENGTH of varchar", "SELECT DATALENGTH('abc')", 3),
    ("DATALENGTH counts bytes", "SELECT DATALENGTH('é')", 2),
    ("DATEPART quarter of a string literal", "SELECT DATEPART(quarter, '2024-03-05')", 1),
    ("DATEPART weekday of a string literal", "SELECT DATEPART(weekday, '2024-03-05')", 3),  # Tuesday, DATEFIRST 7
    ("PATINDEX", "SELECT PATINDEX('%b%', 'abc')", 2),
]
# Refused with the local-engine message: mapping them faithfully needs more than a rewrite.
REFUSED = [
    ("FORMAT N2", "SELECT FORMAT(1234.5, 'N2')", "1,234.50"),
    ("ISNUMERIC of a number", "SELECT ISNUMERIC('12.5')", 1),
    ("ISNUMERIC of text", "SELECT ISNUMERIC('x')", 0),
]
GAPS = MAPPED + REFUSED


def _same(got, want):
    if isinstance(want, float) and isinstance(got, (int, float)) or hasattr(got, "as_tuple"):
        return float(got) == float(want)
    return got == want


@pytest.mark.parametrize("sql, want", [(s, w) for _, s, w in MAPPED], ids=[c for c, _, _ in MAPPED])
def test_a_mapped_function_gives_tsqls_answer(engine, sql, want):
    assert _same(first(engine, sql), want)


@pytest.mark.parametrize("sql, want", [(s, w) for _, s, w in REFUSED], ids=[c for c, _, _ in REFUSED])
@pytest.mark.xfail(strict=True, reason="T-SQL loud gap: refused on the local engine for now")
def test_a_refused_function_would_give_tsqls_answer(engine, sql, want):
    assert _same(first(engine, sql), want)


@pytest.mark.parametrize("sql", [s for _, s, _ in REFUSED], ids=[c for c, _, _ in REFUSED])
def test_a_refused_function_says_it_is_a_local_engine_gap(engine, sql):
    with pytest.raises(Exception, match="on the local engine"):
        engine.execute(sql, dialect="tsql")


def test_a_bit_column_holds_true_and_false(engine):
    engine.execute("CREATE TABLE dbo.flags (id INT, ok BIT)", dialect="tsql")
    engine.execute("INSERT INTO dbo.flags VALUES (1, 1), (2, 0)", dialect="tsql")
    assert engine.execute("SELECT id FROM dbo.flags WHERE ok = 1", dialect="tsql")["rows"] == [[1]]


def test_string_split_gives_one_row_per_part(engine):
    rows = engine.execute("SELECT value FROM STRING_SPLIT('a,b,c', ',')", dialect="tsql")["rows"]
    assert sorted(r[0] for r in rows) == ["a", "b", "c"]


def test_string_split_in_cross_apply(engine):
    engine.execute("CREATE TABLE dbo.tags AS SELECT * FROM (VALUES (1, 'x,y'), (2, 'z')) v(id, tags)")
    rows = engine.execute("SELECT t.id, s.value FROM dbo.tags t CROSS APPLY STRING_SPLIT(t.tags, ',') AS s "
                          "ORDER BY t.id, s.value", dialect="tsql")["rows"]
    assert rows == [[1, "x"], [1, "y"], [2, "z"]]


def test_top_with_ties_keeps_the_tied_rows(engine):
    rows = engine.execute("SELECT TOP 1 WITH TIES id FROM dbo.orders ORDER BY boxes", dialect="tsql")["rows"]
    assert sorted(r[0] for r in rows) == [1, 2]
    rows = engine.execute("SELECT TOP (2) WITH TIES id FROM dbo.orders ORDER BY boxes DESC, id", dialect="tsql")["rows"]
    assert rows == [[3], [1]]


@pytest.mark.parametrize("sql", [
    "SELECT TOP 1 WITH TIES DISTINCT boxes FROM dbo.orders ORDER BY boxes",
    "SELECT TOP 50 PERCENT WITH TIES id FROM dbo.orders ORDER BY boxes",
    "SELECT value FROM STRING_SPLIT('a,b', ',', 1)",
    "SELECT ROUND(1.5, 0, boxes) FROM dbo.orders",
])
def test_what_isnt_mapped_faithfully_is_refused(engine, sql):
    with pytest.raises(Exception, match="on the local engine"):
        engine.execute(sql, dialect="tsql")


@pytest.mark.parametrize("sql, want", [(s, w) for _, s, w in GAPS], ids=[c for c, _, _ in GAPS])
def test_a_loud_gap_never_gives_a_wrong_answer(engine, sql, want):
    """Not xfail: either T-SQL's answer or an error, never a different value (that would be a silent gap)."""
    try:
        got = first(engine, sql)
    except Exception:  # noqa: BLE001 - stopping is what a loud gap does
        return
    assert _same(got, want), f"{sql} gave {got!r}, T-SQL gives {want!r}"
