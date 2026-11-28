"""df.drop and joins without a condition as Spark 3.5's (spark_compat._drop_cols, _same_self). Each
expected answer was checked against Apache Spark 3.5.3 in local mode."""

import pytest

from fabric_desktop.notebook import Kernel

SETUP = ("from pyspark.sql import Row\nfrom pyspark.sql.functions import col, lit\n"
         "df = spark.createDataFrame([(14, 'Tom'), (23, 'Alice'), (16, 'Bob')], ['age', 'name'])\n"
         "df2 = spark.createDataFrame([Row(height=80, name='Tom'), Row(height=85, name='Bob')])\n"
         "j = df.join(df2, df.name == df2.name)\n")


@pytest.fixture(autouse=True)
def no_lakehouse(monkeypatch):
    from fabric_desktop import lakehouse
    monkeypatch.setattr(lakehouse, "_attached", None)


@pytest.fixture(scope="module")
def kernel():
    k = Kernel()
    out = k.run(SETUP)
    assert out["status"] == "ok", out
    return k


def frame(kernel, expr):
    """``expr``'s columns and its rows, sorted."""
    out = kernel.run(f"_f = {expr}\nprint(_f.columns, sorted(map(tuple, _f.collect())))")
    assert out["status"] == "ok", out
    return "".join(o.get("text", "") for o in out["outputs"]).strip()


def error(kernel, expr):
    out = kernel.run(expr)
    assert out["status"] == "error", out
    return " ".join(f"{o.get('ename')}: {o.get('evalue')}" for o in out["outputs"] if o.get("type") == "error")


def test_dropping_one_sides_column_after_a_join_keeps_the_other(kernel):
    # main dropped both name columns here, so the joined frame silently lost df's name.
    assert frame(kernel, "j.drop(df2.name)") == "['age', 'name', 'height'] [(14, 'Tom', 80), (16, 'Bob', 85)]"
    assert frame(kernel, "j.drop(df.name)") == "['age', 'height', 'name'] [(14, 80, 'Tom'), (16, 85, 'Bob')]"
    assert frame(kernel, "j.drop(df2['NAME'])") == "['age', 'name', 'height'] [(14, 'Tom', 80), (16, 'Bob', 85)]"
    assert frame(kernel, "j.drop(df2.name, 'age')") == "['name', 'height'] [('Bob', 85), ('Tom', 80)]"
    assert frame(kernel, "df.alias('s').join(df2.alias('r'), col('s.name') == col('r.name')).drop(col('r.name'))") == \
        "['age', 'name', 'height'] [(14, 'Tom', 80), (16, 'Bob', 85)]"


def test_a_name_drops_every_column_called_that(kernel):
    assert frame(kernel, "j.drop('NAME')") == "['age', 'height'] [(14, 80), (16, 85)]"
    assert frame(kernel, "df.withColumn('a.b.c', lit(1)).drop('a.b.c')") == \
        "['age', 'name'] [(14, 'Tom'), (16, 'Bob'), (23, 'Alice')]"
    # A name is a whole name: 'r.name' isn't a column, so nothing is dropped.
    assert frame(kernel, "df.alias('s').join(df2.alias('r'), col('s.name') == col('r.name')).drop('r.name')") == \
        "['age', 'name', 'height', 'name'] [(14, 'Tom', 80, 'Tom'), (16, 'Bob', 85, 'Bob')]"


def test_columns_that_arent_there_or_arent_columns_are_skipped(kernel):
    assert frame(kernel, "df.withColumn('a.b.c', lit(1)).drop(col('a.b.c'))") == \
        "['age', 'name', 'a.b.c'] [(14, 'Tom', 1), (16, 'Bob', 1), (23, 'Alice', 1)]"
    assert frame(kernel, "df.withColumn('a.b.c', lit(1)).drop(col('`a.b.c`'))") == \
        "['age', 'name'] [(14, 'Tom'), (16, 'Bob'), (23, 'Alice')]"
    assert frame(kernel, "j.drop(df2.height + 1)") == \
        "['age', 'name', 'height', 'name'] [(14, 'Tom', 80, 'Tom'), (16, 'Bob', 85, 'Bob')]"
    assert frame(kernel, "df.drop(col('NAME'), 'missing')") == "['age'] [(14,), (16,), (23,)]"
    assert frame(kernel, "j.select('age', df2.name).drop(df2.name)") == "['age'] [(14,), (16,)]"


def test_an_ambiguous_column_is_an_error_not_a_guess(kernel):
    assert "[AMBIGUOUS_REFERENCE] Reference `name` is ambiguous" in error(kernel, "j.drop(col('name'))")
    # After a select, this PC can't tell which name df2.name is (Spark keeps df's): refused by name.
    text = error(kernel, "j.select('age', df2.name, df.name).drop(df2.name)")
    assert "NotImplementedError" in text and "can't tell which one" in text
    assert "drop would leave no columns" in error(kernel, "df.drop('age', 'NAME')")


def test_a_join_without_a_condition_pairs_every_row(kernel):
    every = ("['age', 'name', 'height', 'name'] [(14, 'Tom', 80, 'Tom'), (14, 'Tom', 85, 'Bob'), (16, 'Bob', 80, 'Tom'), "
             "(16, 'Bob', 85, 'Bob'), (23, 'Alice', 80, 'Tom'), (23, 'Alice', 85, 'Bob')]")
    for how in ("", ", how='inner'", ", how='cross'", ", how='left'", ", how='full'"):
        assert frame(kernel, f"df.join(df2{how})") == every, how
    assert frame(kernel, "df.join(df2, how='left_anti')") == "['age', 'name'] []"
    assert frame(kernel, "df.join(df2, how='left_semi')") == "['age', 'name'] [(14, 'Tom'), (16, 'Bob'), (23, 'Alice')]"
    assert frame(kernel, "df.join(df2.filter('false'), how='left')") == \
        "['age', 'name', 'height', 'name'] [(14, 'Tom', None, None), (16, 'Bob', None, None), (23, 'Alice', None, None)]"
    assert frame(kernel, "df.join(df2, df.name == df2.name, 'cross')") == \
        "['age', 'name', 'height', 'name'] [(14, 'Tom', 80, 'Tom'), (16, 'Bob', 85, 'Bob')]"
    # A join on names keeps each side's columns reachable as df.x and df2.x afterwards.
    assert frame(kernel, "df.join(df2, 'name').select(df.name, df2.height)") == "['name', 'height'] [('Bob', 85), ('Tom', 80)]"

