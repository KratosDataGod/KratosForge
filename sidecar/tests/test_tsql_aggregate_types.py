"""COUNT, SUM and AVG on this PC's T-SQL give the types Microsoft documents (parity check PAR-002):
COUNT is an int (COUNT_BIG the bigint); SUM and AVG of a tinyint, smallint or int are an int, of a bigint a
bigint; a total past the type stops with T-SQL's overflow error. DuckDB gave a bigint for COUNT, a HUGEINT
(read back as decimal(38,0)) for SUM, and a bigint for AVG."""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata import tsql


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("tsql-aggregates") / "app" / "local-data")
    engine = local.engine(local.create("Aggregates")["id"])
    engine.execute("CREATE TABLE dbo.n (i INT, b BIGINT, s SMALLINT, d DECIMAL(5,2), g INT)", dialect="tsql")
    engine.execute("INSERT INTO dbo.n VALUES (2147483647, 9223372036854775807, 1, 1.5, 1), "
                   "(1, 1, 2, 2.25, 2)", dialect="tsql")
    return engine


def _types(result: dict) -> dict[str, str]:
    return {c["name"]: c["type"] for c in result["columns"]}


def test_count_sum_and_avg_take_the_documented_types(engine):
    result = engine.execute("SELECT COUNT(*) AS c, COUNT(i) AS ci, COUNT_BIG(*) AS cb, SUM(s) AS ss, "
                            "SUM(b - b) AS sb, SUM(d) AS sd, AVG(s) AS av, AVG(b - b) AS ab FROM dbo.n", dialect="tsql")
    assert result["rows"] == [[2, 2, 2, 3, 0, "3.75", 1, 0]]
    assert _types(result) == {"c": "int32", "ci": "int32", "cb": "int64", "ss": "int32", "sb": "int64",
                              "sd": "decimal128(38, 2)", "av": "int32", "ab": "int64"}


def test_a_total_past_its_type_stops_with_tsqls_overflow_error(engine):
    with pytest.raises(Exception, match="Arithmetic overflow error converting expression to data type int"):
        engine.execute("SELECT SUM(i) AS t FROM dbo.n", dialect="tsql")
    with pytest.raises(Exception, match="Arithmetic overflow error converting expression to data type bigint"):
        engine.execute("SELECT SUM(b) AS t FROM dbo.n", dialect="tsql")


def test_windows_groups_and_unnamed_columns(engine):
    result = engine.execute("SELECT g, COUNT(*) OVER () AS c, SUM(s) OVER (ORDER BY g) AS r FROM dbo.n ORDER BY g",
                            dialect="tsql")
    assert result["rows"] == [[1, 2, 1], [2, 2, 3]]
    assert _types(result)["c"] == "int32" and _types(result)["r"] == "int32"
    grouped = engine.execute("SELECT g, COUNT(*), SUM(s) FROM dbo.n GROUP BY g ORDER BY g", dialect="tsql")
    assert grouped["rows"] == [[1, 1, 1], [2, 1, 2]]
    assert [c["name"] for c in grouped["columns"]][1:] == ["COUNT(*)", "SUM(s)"]  # not the SQL written for them


def test_a_pivot_keeps_its_bare_aggregate(engine):
    result = engine.execute("SELECT * FROM (SELECT g, s FROM dbo.n) AS x PIVOT (SUM(s) FOR g IN ([1], [2])) AS p",
                            dialect="tsql")
    assert result["rows"] == [["1", "2"]]  # still DuckDB's HUGEINT there, read back as text (a known limitation)


def test_the_total_is_read_once():
    sql = tsql.translate("SELECT COUNT(*) OVER (ORDER BY y) FROM t")[0][0]
    assert sql.count("[COUNT(*) OVER (ORDER BY y NULLS FIRST)]") == 1 and sql.count("COUNT(*)") == 2
