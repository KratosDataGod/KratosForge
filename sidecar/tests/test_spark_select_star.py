"""select("a.*") and select(col("s.*")): a DataFrame alias's columns or a struct's fields, as Spark 3.5's
(spark_compat._star_select). Each expected answer was checked against Apache Spark 3.5.3."""

import pytest

from fabric_desktop.notebook import Kernel

SETUP = ("from pyspark.sql.functions import col\n"
         "a = spark.createDataFrame([(1, 'x', 10), (2, 'y', 20)], ['id', 'k', 'v'])\n"
         "b = spark.createDataFrame([(1, 'p'), (3, 'q')], ['id', 'w'])\n"
         "j = a.alias('a').join(b.alias('b'), col('a.id') == col('b.id'), 'left')\n"
         "s = spark.sql(\"SELECT named_struct('x', 1, 'y', 2) AS s, 3 AS z\")\n")


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
    out = kernel.run(f"_f = {expr}\nprint(_f.columns, sorted(map(tuple, _f.collect()), key=repr))")
    assert out["status"] == "ok", out
    return "".join(o.get("text", "") for o in out["outputs"]).strip()


def test_an_alias_star_is_that_sides_columns(kernel):
    assert frame(kernel, "j.select('a.*')") == "['id', 'k', 'v'] [(1, 'x', 10), (2, 'y', 20)]"
    assert frame(kernel, "j.select('a.*', 'b.w')") == "['id', 'k', 'v', 'w'] [(1, 'x', 10, 'p'), (2, 'y', 20, None)]"
    assert frame(kernel, "j.select(col('a.*'), col('b.w'))") == \
        "['id', 'k', 'v', 'w'] [(1, 'x', 10, 'p'), (2, 'y', 20, None)]"
    assert frame(kernel, "j.select(['b.*'])") == "['id', 'w'] [(1, 'p'), (None, None)]"
    assert frame(kernel, "j.select('a.*', col('b.w').alias('ww'))") == \
        "['id', 'k', 'v', 'ww'] [(1, 'x', 10, 'p'), (2, 'y', 20, None)]"
    assert frame(kernel, "a.alias('a').select('a.*')") == "['id', 'k', 'v'] [(1, 'x', 10), (2, 'y', 20)]"
    assert frame(kernel, "a.alias('a.b').select('`a.b`.*')") == "['id', 'k', 'v'] [(1, 'x', 10), (2, 'y', 20)]"
    assert frame(kernel, "j.select('*')") == \
        "['id', 'k', 'v', 'id', 'w'] [(1, 'x', 10, 1, 'p'), (2, 'y', 20, None, None)]"


def test_a_struct_star_is_its_fields(kernel):
    assert frame(kernel, "s.select('s.*', 'z')") == "['x', 'y', 'z'] [(1, 2, 3)]"
    assert frame(kernel, "s.select(col('s.*'))") == "['x', 'y'] [(1, 2)]"


def test_a_star_of_nothing_is_sparks_error(kernel):
    out = kernel.run("j.select('c.*')")
    assert out["status"] == "error"
    text = str(out["outputs"])
    assert "[CANNOT_RESOLVE_STAR_EXPAND] Cannot resolve `c`.* given input columns `id`, `k`, `v`, `id`, `w`" in text


def test_a_name_with_a_quote_stays_a_name(kernel):
    out = kernel.run("a.alias('q\"x').select('q\"x.*').columns")
    assert out["status"] == "ok", out
    out = kernel.run("a.select('nope\" FROM x; --.*')")
    assert out["status"] == "error" and "CANNOT_RESOLVE_STAR_EXPAND" in str(out["outputs"])


def test_a_nested_struct_star_and_sparks_errors(kernel):
    # Spark 3.5.3's answers and error texts.
    kernel.run("n = spark.sql(\"SELECT named_struct('i', named_struct('p', 1, 'q', 2), 'j', 3) AS s, 5 AS k, "
               "map('a', 1) AS m\")")
    assert frame(kernel, "n.select('s.i.*')") == "['p', 'q'] [(1, 2)]"
    assert frame(kernel, "n.select(col('S.I.*'), 'k')") == "['p', 'q', 'k'] [(1, 2, 5)]"
    for star, names in (("k", "k"), ("m", "m"), ("s.j", "s, j")):
        out = kernel.run(f"n.select('{star}.*')")
        assert out["status"] == "error", star
        assert f"Can only star expand struct data types. Attribute: `ArrayBuffer({names})`." in str(out["outputs"])
    out = kernel.run("n.select('s.zz.*')")
    assert out["status"] == "error" and "CANNOT_RESOLVE_STAR_EXPAND" in str(out["outputs"])
