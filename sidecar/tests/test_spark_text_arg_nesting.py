"""Text functions nested in each other stay small on this PC: Spark's implicit cast to text (upper(14) is
'14') wrote its argument three times, so upper(upper(upper(upper(upper(x))))) was 381 KB of SQL and eight
levels 10 MB. Now an argument that is already text passes through, a column is written out as before, and
anything else is read once, through a lambda, so rand() in it is one sample."""

from __future__ import annotations

import pytest

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel

SRC = "(SELECT * FROM VALUES (1, 'aB c', 1.5D) AS t(i, s, d))"


def _nested(fn: str, levels: int, inner: str) -> str:
    for _ in range(levels):
        inner = f"{fn}({inner})"
    return inner


def _translated(expr: str) -> str:
    return sql.duckdb_sql(sql.parse(f"SELECT {expr} AS v FROM {SRC}")[0])


@pytest.mark.parametrize(("fn", "inner", "want"), [
    ("upper", "s", "AB C"),
    ("lower", "d + 1", "2.5"),
    ("trim", "i + 1", "2"),
    ("initcap", "s", "Ab C"),
    ("ltrim", "CAST(d AS STRING)", "1.5"),
    ("upper", "rand() * 0 + d", "1.5"),
])
def test_eight_nested_text_calls_stay_small(fn: str, inner: str, want: str) -> None:
    expr = _nested(fn, 8, inner)
    assert len(_translated(expr)) < 20_000
    assert sql.run(f"SELECT {expr} AS v FROM {SRC}").to_pylist() == [{"v": want}]


def test_mixed_nesting_over_a_number_stays_small() -> None:
    expr = "length(upper(lower(trim(initcap(upper(lower(trim(d * 2))))))))"
    assert len(_translated(expr)) < 20_000
    assert sql.run(f"SELECT {expr} AS v FROM {SRC}").to_pylist() == [{"v": 3}]


def test_format_string_and_levenshtein_over_nested_arguments_stay_small() -> None:
    expr = _nested("upper", 8, "format_string('%s', format_string('%s', format_string('%s', d + 1)))")
    assert len(_translated(expr)) < 20_000
    assert sql.run(f"SELECT {expr} AS v FROM {SRC}").to_pylist() == [{"v": "2.5"}]
    expr = f"levenshtein({_nested('upper', 8, 'd + 1')}, {_nested('lower', 8, 'i + 1')})"
    assert len(_translated(expr)) < 40_000
    assert sql.run(f"SELECT {expr} AS v FROM {SRC}").to_pylist() == [{"v": 2}]  # '2.5' and '2'


def test_a_changing_argument_is_one_sample() -> None:
    # %1$s twice is the same value, as in Spark: the argument was written once per use, so each half
    # was a sample of its own.
    rows = sql.run("SELECT format_string('%1$s|%1$s', rand()) AS a, "
                   "upper(format_string('%1$s|%1$s', rand() + 1)) AS b FROM range(200)").to_pylist()
    for row in rows:
        for v in (row["a"], row["b"]):
            first, second = v.split("|")
            assert first == second
    # and a lambda reads the argument once: lower() of the text of rand() is a number's text
    rows = sql.run("SELECT lower(upper(rand() - 0.5)) AS v FROM range(2000)").to_pylist()
    assert all(-0.5 <= float(row["v"]) < 0.5 for row in rows)


def test_a_wrong_type_inside_a_nested_call_is_still_refused() -> None:
    with pytest.raises(Exception, match="upper\\(\\) reads text here"):
        sql.run(f"SELECT upper(upper(array(i + 1))) AS v FROM {SRC}")


def test_nested_dataframe_text_functions_stay_small() -> None:
    kernel = Kernel()
    out = kernel.run("""from pyspark.sql import functions as F
df = spark.createDataFrame([(1, 1.5)], "i long, d double")
c = F.col("d") + 1
for _ in range(4):
    c = F.upper(F.lower(c))
print(len(str(c)), df.select(c.alias("v")).collect())""")
    assert out["status"] == "ok", out["outputs"]
    size, rows = "".join(o.get("text", "") for o in out["outputs"]).split(" ", 1)
    assert int(size) < 40_000
    assert rows.strip() == "[Row(v='2.5')]"
