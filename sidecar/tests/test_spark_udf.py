"""Python UDFs on this PC's Spark (spark_udf.py, Fabric notebook parity F1): udf, @udf, pandas_udf,
spark.udf.register for SQL and call_udf, run through a notebook kernel as a cell would. Nothing
reaches OneLake or Fabric."""

import pytest

from fabric_desktop.notebook import Kernel


@pytest.fixture(autouse=True)
def no_lakehouse(monkeypatch):
    from fabric_desktop import lakehouse
    monkeypatch.setattr(lakehouse, "_attached", None)


@pytest.fixture
def kernel():
    return Kernel()


def run(kernel, code):
    out = kernel.run(code)
    assert out["status"] == "ok", out
    return "".join(o.get("text", "") for o in out["outputs"]).strip()


SETUP = """
from pyspark.sql import functions as F
from pyspark.sql.types import IntegerType, StringType, ArrayType, StructType, StructField
df = spark.createDataFrame([(1, " A@X.com ", None), (2, None, 3.5), (3, "b@y.org", 1.0)], ["id", "email", "amt"])
"""


def test_udf_forms_return_their_declared_type_and_take_null(kernel):
    out = run(kernel, SETUP + """
clean = F.udf(lambda s: s.strip().lower() if s is not None else "none", StringType())
@F.udf("int")
def dbl(x): return x * 2
@F.udf(returnType=IntegerType())
def twice(x): return x + x
@F.udf
def as_text(x): return x
r = df.select(clean("email").alias("e"), dbl(F.col("id")).alias("d"), twice("id").alias("t"), as_text("id").alias("s")).collect()
print([tuple(x) for x in r])
print(dict(df.select(dbl("id"), as_text("id")).dtypes))
""")
    lines = out.splitlines()
    assert lines[0] == "[('a@x.com', 2, 2, '1'), ('none', 4, 4, '2'), ('b@y.org', 6, 6, '3')]"
    assert lines[1] == "{'dbl(id)': 'int', 'as_text(id)': 'string'}"


def test_a_value_that_does_not_fit_the_return_type_is_null_as_in_spark(kernel):
    out = run(kernel, SETUP + """
wrong = F.udf(lambda x: "text", "int")
floaty = F.udf(lambda x: 1, "double")
lst = F.udf(lambda x: [x, None, "z"], ArrayType(IntegerType()))
print([tuple(r) for r in df.select(wrong("id"), floaty("id"), lst("id")).limit(1).collect()])
""")
    assert out == "[(None, None, [1, None, None])]"


def test_structs_arrive_as_rows_and_struct_results_are_built(kernel):
    out = run(kernel, SETUP + """
schema = StructType([StructField("user", StringType()), StructField("n", IntegerType())])
split = F.udf(lambda s: (s.split("@")[0].strip(), len(s)) if s else None, schema)
first = F.udf(lambda r: r.a + 1, "int")
r = df.select(split("email").alias("p"), first(F.struct(F.col("id").alias("a"))).alias("f")).collect()
print(r[0].p.user, r[0].p.n, r[1].p, r[2].f)
""")
    assert out == "A 9 None 4"


def test_an_exception_in_the_udf_fails_the_query_naming_it(kernel):
    out = kernel.run(SETUP + """
def boom(x):
    raise ValueError("bad row " + str(x))
df.select(F.udf(boom)("id")).collect()
""")
    assert out["status"] == "error"
    text = str(out["outputs"])
    assert "ValueError" in text and "bad row" in text


def test_pandas_udf_scalar_and_iterator_forms(kernel):
    out = run(kernel, SETUP + """
import pandas as pd
from typing import Iterator
@F.pandas_udf("double")
def plus_one(a: pd.Series) -> pd.Series:
    return a.fillna(0) + 1
@F.pandas_udf("long")
def tens(batches: Iterator[pd.Series]) -> Iterator[pd.Series]:
    for b in batches:
        yield b * 10
print([tuple(r) for r in df.select(plus_one("amt"), tens("id")).collect()])
""")
    assert out == "[(1.0, 10), (4.5, 20), (2.0, 30)]"


def test_a_grouped_aggregate_pandas_udf_says_it_runs_in_fabric(kernel):
    out = kernel.run(SETUP + """
import pandas as pd
@F.pandas_udf("double")
def mean_(a: pd.Series) -> float:
    return a.mean()
""")
    assert out["status"] == "error"
    assert "grouped-aggregate pandas_udf" in str(out["outputs"]) and "runs in Fabric" in str(out["outputs"])


def test_a_pandas_udf_returning_the_wrong_length_is_refused(kernel):
    out = kernel.run(SETUP + """
import pandas as pd
@F.pandas_udf("long")
def short(a: pd.Series) -> pd.Series:
    return a.head(1)
df.select(short("id")).collect()
""")
    assert out["status"] == "error"
    assert "returned 1 for 3 rows" in str(out["outputs"])


def test_registered_udfs_work_in_sql_and_call_udf(kernel):
    out = run(kernel, SETUP + """
spark.udf.register("shout", lambda s: (s or "").strip().upper() + "!", "string")
made = F.udf(lambda x: x * 100, "int")
spark.udf.register("hundred", made)
df.createOrReplaceTempView("people")
print(spark.sql("SELECT shout(email) AS s, hundred(id) AS h FROM people ORDER BY id").collect())
print(df.select(F.call_udf("shout", "email")).columns, df.select(F.call_udf("shout", "email")).first()[0])
""")
    lines = out.splitlines()
    assert lines[0] == "[Row(s='A@X.COM!', h=100), Row(s='!', h=200), Row(s='B@Y.ORG!', h=300)]"
    assert lines[1] == "['shout(email)'] A@X.COM!"


def test_register_refuses_a_bad_name_and_call_udf_an_unknown_one(kernel):
    out = kernel.run(SETUP + "spark.udf.register('drop table', lambda x: x)")
    assert out["status"] == "error" and "plain name" in str(out["outputs"])
    out = kernel.run(SETUP + "df.select(F.call_udf('nope', 'id'))")
    assert out["status"] == "error" and "No UDF is registered as nope" in str(out["outputs"])


def test_as_nondeterministic_calls_it_for_every_row(kernel):
    out = run(kernel, SETUP + """
seen = []
def tick(x):
    seen.append(x)
    return len(seen)
t = F.udf(tick, "int").asNondeterministic()
spark.range(4).select(t(F.lit(1).alias("one"))).collect()
print(len(seen))
""")
    assert out == "4"


def test_udfs_are_no_longer_flagged_as_fabric_only():
    from fabric_desktop import fabric_run
    texts = [why for pattern, why in fabric_run._FABRIC_ONLY if pattern.search("F.pandas_udf('double')\nspark.udf.register('f', g)")]
    assert texts == []
    assert any(p.search("df.rdd.map(f)") for p, _ in fabric_run._FABRIC_ONLY)


@pytest.mark.parametrize("name", ["lower", "UPPER", "nvl", "date_add"])
def test_a_name_sql_already_has_is_refused_since_sql_would_never_call_the_udf(kernel, name):
    out = kernel.run(f"spark.udf.register({name!r}, lambda s: 'mine', 'string')")
    assert out["status"] == "error" and "SQL already has a function called" in str(out["outputs"]), out
    assert run(kernel, "spark.sql(\"SELECT lower('AB') AS v\").first().v") == "'ab'"


def test_registering_a_udf_again_under_its_own_name_replaces_it(kernel):
    assert run(kernel, "spark.udf.register('tag', lambda s: 'one', 'string')\n"
                       "spark.udf.register('tag', lambda s: 'two', 'string')\n"
                       "spark.sql(\"SELECT tag('x') AS v\").first().v") == "'two'"


def test_an_int_that_doesnt_fit_the_declared_type_is_null(kernel):
    out = run(kernel, SETUP + "big = F.udf(lambda x: 2 ** 40 if x == 1 else x, 'int')\n"
                              "[r.v for r in df.orderBy('id').select(big('id').alias('v')).collect()]")
    assert out == "[None, 2, 3]"


def test_spark2_function_types_are_honoured(kernel):
    out = run(kernel, SETUP + """
from pyspark.sql.functions import pandas_udf, PandasUDFType
@pandas_udf("long", PandasUDFType.SCALAR_ITER)
def plus_one(batches):
    for s in batches:
        yield s + 1
[r.v for r in df.orderBy('id').select(plus_one('id').alias('v')).collect()]""")
    assert out == "[2, 3, 4]"
    out = kernel.run("from pyspark.sql.functions import pandas_udf, PandasUDFType\n"
                     "@pandas_udf('id long', PandasUDFType.GROUPED_MAP)\ndef g(pdf): return pdf")
    assert out["status"] == "error" and "applyInPandas" in str(out["outputs"])
