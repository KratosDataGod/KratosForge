"""Spark SQL's arrays, maps, structs and generators on this PC, value-checked against Spark 3.5 (WR-109).

Each expected value is Spark's, ANSI off as Fabric runs it, from the functions' documented results
(sql-ref-functions-builtin) and Spark's defaults: spark.sql.legacy.sizeOfNull (size of NULL is -1),
spark.sql.mapKeyDedupPolicy EXCEPTION, element_at out of range is NULL, array_contains is NULL when the
value isn't found and the array holds a NULL, array_position is 0 when not found. They run through the
notebook's spark.sql. What still differs is listed in docs/sql-compatibility.md ("Arrays, maps and
structs on this PC"). None of this was run on a Fabric cluster: UNVERIFIED live.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, onelake, spark_complex, sql
from fabric_desktop import publish as publish_mod
from fabric_desktop.notebook import Kernel

DOCS = Path(__file__).resolve().parents[2] / "docs" / "sql-compatibility.md"

_VIEW = ("CREATE OR REPLACE TEMP VIEW c AS SELECT * FROM VALUES (1, array(1, 2, NULL), map('a', 1, 'b', 2), 3, 1), "
         "(2, array(), map(), 1, 3), (3, NULL, NULL, 2, 2) AS c(id, arr, m, s, e)")

_RUN = """
import json
spark.sql({view!r})
out = []
for q in {cases!r}:
    try:
        r = spark.sql(q)
        rows = [[v if v is None or isinstance(v, (list, bool, int, float, str)) else (dict(v) if isinstance(v, dict) else str(v))
                 for v in row] for row in r.collect()]
        out.append([[d[1] for d in r.dtypes], rows])
    except Exception as e:
        out.append(['error', str(e)])
print('RESULT' + json.dumps(out))
"""


@pytest.fixture(scope="module")
def kernel() -> Kernel:
    return Kernel()


def run(kernel: Kernel, code: str) -> str:
    out = kernel.run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    return text


def check(kernel: Kernel, table: list[tuple[str, list[str], list[list[object]]]]) -> None:
    text = run(kernel, _RUN.format(view=_VIEW, cases=[q for q, _, _ in table]))
    got = json.loads(text.split("RESULT", 1)[1])
    wrong = [(q, (t, v), g) for (q, t, v), g in zip(table, got, strict=True) if g != [t, v]]
    assert not wrong, "\n".join(f"{q}: want {w}, got {g}" for q, w, g in wrong)


CASES = [
    # size and cardinality: -1 for NULL, an int, a map's key count
    ("SELECT size(array(1, 2)), size(NULL), size(map('a', 1)), size(CAST(NULL AS ARRAY<INT>)), typeof(size(array(1)))",
     ["int", "int", "int", "int", "string"], [[2, -1, 1, -1, "int"]]),
    ("SELECT cardinality(array(1, 2)), cardinality(CAST(NULL AS MAP<STRING, INT>))", ["int", "int"], [[2, -1]]),
    ("SELECT id, size(arr), size(m), cardinality(m) FROM c ORDER BY id", ["int", "int", "int", "int"],
     [[1, 3, 2, 2], [2, 0, 0, 0], [3, -1, -1, -1]]),
    ("WITH w AS (SELECT * FROM c) SELECT id, size(m) FROM w ORDER BY id", ["int", "int"], [[1, 2], [2, 0], [3, -1]]),
    # element_at and [ ]: out of range or a missing key is NULL
    (("SELECT element_at(array(1, 2, 3), 1), element_at(array(1, 2, 3), -1), element_at(array(1, 2, 3), 5), "
      "element_at(array(1, 2, 3), -5)"), ["int", "int", "int", "int"], [[1, 3, None, None]]),
    ("SELECT array(1, 2, 3)[0], array(1, 2, 3)[3], element_at(map('a', 1), 'b'), map('a', 1)['b']",
     ["int", "int", "int", "int"], [[1, None, None, None]]),
    ("SELECT id, m['a'], element_at(m, 'z') FROM c ORDER BY id", ["int", "int", "int"],
     [[1, 1, None], [2, None, None], [3, None, None]]),
    # array_contains, array_position, flatten
    (("SELECT array_contains(array(1, NULL), 2), array_contains(array(1, NULL), 1), array_contains(array(1), 2), "
      "array_contains(NULL, 1)"), ["boolean", "boolean", "boolean", "boolean"], [[None, True, False, None]]),
    ("SELECT array_position(array(3, 2, 1), 1), array_position(array(3, 2, 1), 5), typeof(array_position(array(1), 1))",
     ["bigint", "bigint", "string"], [[3, 0, "bigint"]]),
    ("SELECT flatten(array(array(1), array(2, 3))), flatten(array(array(1), NULL))", ["array<int>", "array<int>"],
     [[[1, 2, 3], None]]),
    # sorting with NULLs
    ("SELECT array_sort(array(3, NULL, 1)), sort_array(array(3, NULL, 1)), sort_array(array(3, NULL, 1), false)",
     ["array<int>", "array<int>", "array<int>"], [[[1, 3, None], [None, 1, 3], [3, 1, None]]]),
    # sequence counts down when stop is below start; whole numbers stay int
    ("SELECT sequence(1, 5, 2), sequence(5, 1)", ["array<int>", "array<int>"], [[[1, 3, 5], [5, 4, 3, 2, 1]]]),
    ("SELECT id, sequence(s, e), reverse(arr) FROM c ORDER BY id", ["int", "array<int>", "array<int>"],
     [[1, [3, 2, 1], [None, 2, 1]], [2, [1, 2, 3], []], [3, [2], None]]),
    ("SELECT reverse(array(1, 2)), concat(array(1), array(2, NULL)), reverse('ab')",
     ["array<int>", "array<int>", "string"], [[[2, 1], [1, 2, None], "ba"]]),
    # maps
    ("SELECT map_concat(map('a', 1), map('b', 2))", ["map<string,int>"], [[{"a": 1, "b": 2}]]),
    ("SELECT map_keys(map('a', 1, 'b', 2)), map_values(map('a', 1)), map_from_arrays(array('a', 'b'), array(1, 2))",
     ["array<string>", "array<int>", "map<string,int>"], [[["a", "b"], [1], {"a": 1, "b": 2}]]),
    # structs
    ("SELECT struct(1 AS a, 'x' AS b).a, named_struct('a', 1, 'b', 2).b", ["int", "int"], [[1, 2]]),
    ("SELECT s.a, s.b FROM (SELECT named_struct('a', 1, 'b', 'x') AS s)", ["int", "string"], [[1, "x"]]),
    # explode, posexplode and LATERAL VIEW, with OUTER
    ("SELECT explode(array(1, NULL, 3))", ["int"], [[1], [None], [3]]),
    ("SELECT explode(map('a', 1, 'b', 2))", ["string", "int"], [["a", 1], ["b", 2]]),
    ("SELECT id, explode(m) FROM c ORDER BY id, key", ["int", "string", "int"], [[1, "a", 1], [1, "b", 2]]),
    ("SELECT explode(CAST(NULL AS ARRAY<INT>))", ["int"], []),
    ("SELECT id, x FROM c LATERAL VIEW explode(arr) t AS x ORDER BY id, x", ["int", "int"],
     [[1, None], [1, 1], [1, 2]]),
    ("SELECT id, x FROM c LATERAL VIEW OUTER explode(arr) t AS x ORDER BY id, x", ["int", "int"],
     [[1, None], [1, 1], [1, 2], [2, None], [3, None]]),
    ("SELECT id, k, v FROM c LATERAL VIEW explode(m) x AS k, v ORDER BY id, k", ["int", "string", "int"],
     [[1, "a", 1], [1, "b", 2]]),
    ("SELECT id, k, v FROM c LATERAL VIEW OUTER explode(m) x AS k, v ORDER BY id, k", ["int", "string", "int"],
     [[1, "a", 1], [1, "b", 2], [2, None, None], [3, None, None]]),
    # stack, json_tuple and inline after LATERAL VIEW: one column per name, as in a select list
    ("SELECT id, a, b FROM c LATERAL VIEW stack(2, 1, 2, 3, 4) t AS a, b WHERE id = 1 ORDER BY a", ["int", "int", "int"],
     [[1, 1, 2], [1, 3, 4]]),
    ("SELECT x, y FROM c LATERAL VIEW json_tuple('{\"a\": 1, \"b\": [2]}', 'a', 'b') t AS x, y WHERE id = 1",
     ["string", "string"], [["1", "[2]"]]),
    ("SELECT id, a, b FROM c LATERAL VIEW inline(array(struct(id AS a, 'x' AS b))) t AS a, b ORDER BY id",
     ["int", "int", "string"], [[1, 1, "x"], [2, 2, "x"], [3, 3, "x"]]),
    (("SELECT id, a, b FROM c LATERAL VIEW OUTER inline(CASE WHEN id = 1 THEN array(struct(1 AS a, 'x' AS b)) END) t AS a, b "
      "ORDER BY id"), ["int", "int", "string"], [[1, 1, "x"], [2, None, None], [3, None, None]]),
]

REFUSED = [
    ("SELECT element_at(array(1, 2, 3), 0)", "index 0 is invalid"),
    ("SELECT map_concat(map('a', 1), map('a', 2))", "DUPLICATED_MAP_KEY"),
    ("SELECT map('a', 1, 'a', 2)", "Map keys must be unique"),
]


def test_complex_types_answer_as_in_spark(kernel: Kernel) -> None:
    check(kernel, CASES)


def test_what_spark_refuses_is_refused(kernel: Kernel) -> None:
    text = run(kernel, _RUN.format(view=_VIEW, cases=[q for q, _ in REFUSED]))
    got = json.loads(text.split("RESULT", 1)[1])
    wrong = [(q, g) for (q, want), g in zip(REFUSED, got, strict=True) if g[0] != "error" or want not in g[1]]
    assert not wrong, wrong


def test_dataframe_array_functions(kernel: Kernel) -> None:
    text = run(kernel, """
import json
from pyspark.sql import functions as F
df = spark.createDataFrame([(1, [1, 2, None]), (2, []), (3, None)], "id int, arr array<int>")
r = df.select("id", F.size("arr"), F.array_contains("arr", 5), F.array_position("arr", 2)).orderBy("id")
print('RESULT' + json.dumps([r.dtypes, [list(x) for x in r.collect()]]))
""")
    dtypes, rows = json.loads(text.split("RESULT", 1)[1])
    assert [d[1] for d in dtypes] == ["int", "int", "boolean", "bigint"]
    assert rows == [[1, 3, None, 2], [2, 0, False, 0], [3, -1, None, None]]


def test_dataframe_maps_reverse_sequence_and_posexplode(kernel: Kernel) -> None:
    text = run(kernel, """
import json
from pyspark.sql import functions as F
df = spark.createDataFrame([(1, {'a': 1, 'b': 2}, [3, 1, 2]), (2, {}, []), (3, None, None)],
                           "id int, m map<string,int>, arr array<int>")
frames = [df.select("id", F.size("m"), F.cardinality("m"), (F.size("m") + 1).alias("n")),
          df.withColumn("n", F.size("m")).select("id", "n"),
          df.filter(F.size("m") > 1).select("id"),
          df.select("id", F.explode("m")),
          df.select("id", F.explode_outer("m")),
          df.select("id", F.posexplode("arr")),
          df.select("id", F.posexplode_outer("arr")),
          df.select("id", F.posexplode("m")),
          df.select("id", F.reverse("arr"), F.reverse(F.lit("abc"))),
          df.limit(1).select(F.sequence(F.lit(3), F.lit(1)), F.sequence(F.lit(1), F.lit(5), F.lit(2)),
                             F.sequence(F.lit(5).cast("bigint"), F.lit(3))),
          df.limit(1).select(F.map_concat("m", F.expr("map('c', 3)")))]
out = [[r.columns, [d[1] for d in r.dtypes], [list(x) for x in r.orderBy(*r.columns[:1]).collect()]] for r in frames]
print('RESULT' + json.dumps(out))
""")
    got = json.loads(text.split("RESULT", 1)[1])
    assert got == [
        [["id", "size(m)", "cardinality(m)", "n"], ["int", "int", "int", "int"], [[1, 2, 2, 3], [2, 0, 0, 1], [3, -1, -1, 0]]],
        [["id", "n"], ["int", "int"], [[1, 2], [2, 0], [3, -1]]],
        [["id"], ["int"], [[1]]],
        [["id", "key", "value"], ["int", "string", "int"], [[1, "a", 1], [1, "b", 2]]],
        [["id", "key", "value"], ["int", "string", "int"], [[1, "a", 1], [1, "b", 2], [2, None, None], [3, None, None]]],
        [["id", "pos", "col"], ["int", "int", "int"], [[1, 0, 3], [1, 1, 1], [1, 2, 2]]],
        [["id", "pos", "col"], ["int", "int", "int"], [[1, 0, 3], [1, 1, 1], [1, 2, 2], [2, None, None], [3, None, None]]],
        [["id", "pos", "key", "value"], ["int", "int", "string", "int"], [[1, 0, "a", 1], [1, 1, "b", 2]]],
        [["id", "reverse(arr)", "reverse(abc)"], ["int", "array<int>", "string"],
         [[1, [2, 1, 3], "cba"], [2, [], "cba"], [3, None, "cba"]]],
        [["sequence(3, 1)", "sequence(1, 5, 2)", "sequence(CAST(5 AS BIGINT), 3)"], ["array<int>", "array<int>", "array<bigint>"],
         [[[3, 2, 1], [1, 3, 5], [5, 4, 3]]]],
        [["map_concat(m, map(c, 3))"], ["map<string,int>"], [[{"a": 1, "b": 2, "c": 3}]]],
    ]


def test_rewrite_fixes_only_the_querys_own_calls() -> None:
    # arrays_zip and array_repeat are written with sizes and ranges of their own; those stay as written.
    statement = sql.parse("SELECT arrays_zip(array(1, 2), array('a')), array_repeat('a', 2)")[0]
    import duckdb
    assert duckdb.connect().execute(sql.duckdb_sql(statement)).fetchone() == (
        [{"0": 1, "1": "a"}, {"0": 2, "1": None}], ["a", "a"])


def test_databricks_sql_path_has_the_untyped_fixes() -> None:
    import duckdb

    from fabric_desktop.engines.local_sql import _databricks
    query = "SELECT array_contains(array(1, NULL), 2), array_position(array(1), 5), size(CAST(NULL AS ARRAY<INT>))"
    assert duckdb.connect().execute(sql.duckdb_sql(_databricks(query)[-1])).fetchone() == (None, 0, -1)


# LATERAL VIEW names holding a double quote stay one name each: nothing in them runs as SQL.
QUOTED_NAMES = [
    ("SELECT * FROM c LATERAL VIEW explode(m) t AS `k\" , getenv('HOME') AS \"z`, v ORDER BY id",
     ["id", "arr", "m", "s", "e", "k\" , getenv('HOME') AS \"z", "v"]),
    ("SELECT * FROM c LATERAL VIEW explode(m) t AS `k\"`, v ORDER BY id", ["id", "arr", "m", "s", "e", "k\"", "v"]),
    ("SELECT `t\"x`.k FROM c LATERAL VIEW OUTER explode(m) `t\"x` AS k, v ORDER BY id", ["k"]),
]


def test_lateral_view_names_with_quotes_stay_names(kernel: Kernel) -> None:
    text = run(kernel, f"""
import json
spark.sql({_VIEW!r})
out = []
for q in {[q for q, _ in QUOTED_NAMES]!r}:
    r = spark.sql(q)
    out.append([r.columns, len(r.collect())])
print('RESULT' + json.dumps(out))
""")
    got = json.loads(text.split("RESULT", 1)[1])
    assert [g[0] for g in got] == [want for _, want in QUOTED_NAMES]
    assert [g[1] for g in got] == [2, 2, 4]


# An argument holding rand() or shuffle() is read once, as Spark reads it, though the rewrite uses it
# more than once: each function below writes its argument two to four times.
ONCE = [
    "array_contains(shuffle(array(1, 2, NULL)), 1)",
    "array_contains(array(1, 2), CAST(rand() * 2 AS INT))",
    "array_position(shuffle(array(1, 2)), 1)",
    "flatten(array(array(rand()), array(rand())))",
    "size(array_repeat(rand(), 3))",
    "map_concat(map('a', rand()), map('b', rand()))",
]


@pytest.mark.parametrize("call", ONCE)
def test_an_argument_holding_rand_is_read_once(call: str) -> None:
    import duckdb
    written = sql.duckdb_sql(sql.parse(f"SELECT {call}")[0])
    assert written.count("RANDOM()") == call.count("rand()") + call.count("shuffle("), written
    duckdb.connect().execute(written).fetchall()


def test_typed_forms_read_rand_once(kernel: Kernel) -> None:
    text = run(kernel, """
import json
qs = ["SELECT size(map('a', rand())) FROM range(3)",
      "SELECT count(*) FROM (SELECT map('a', rand(), 'b', rand()) AS m) LATERAL VIEW explode(m) t AS k, v",
      "SELECT count(*) FROM (SELECT 1) LATERAL VIEW OUTER explode(shuffle(array(1, 2))) t AS x",
      "SELECT size(sequence(1, CAST(rand() * 3 AS INT) + 1)) BETWEEN 1 AND 3 FROM range(20)"]
print('RESULT' + json.dumps([sorted({r[0] for r in spark.sql(q).collect()}) for q in qs]))
""")
    assert json.loads(text.split("RESULT", 1)[1]) == [[1], [2], [2], [True]]


def test_a_struct_field_named_like_a_part_is_the_querys_own(kernel: Kernel) -> None:
    text = run(kernel, f"""
import json
spark.sql({_VIEW!r})
qs = ["SELECT size(named_struct('a', map('x', 1, 'y', 2, 'z', 3), 'b', m).b) FROM c ORDER BY id",
      "SELECT size(named_struct('a', array(1, 2, 3), 'b', arr).b) FROM c ORDER BY id",
      "SELECT size(named_struct('kf_a', array(1, 2, 3), 'b', arr).b) FROM c ORDER BY id"]
print('RESULT' + json.dumps([[r[0] for r in spark.sql(q).collect()] for q in qs]))
""")
    assert json.loads(text.split("RESULT", 1)[1]) == [[2, 0, -1], [3, 0, -1], [3, 0, -1]]


def test_untyped_statements_keep_array_forms() -> None:
    class Broken:
        def execute(self, *_a: object) -> object:
            raise RuntimeError("no catalog")
    statement = sql.parse("SELECT size(m) FROM c")[0]
    assert "ARRAY_LENGTH" in sql.duckdb_sql(spark_complex.spark_complex_types(statement, Broken()))


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    write_deltalake(str(root / "dbo" / "seed"), pa.table({"id": [1]}))
    yield root
    lakehouse.attach(None)


def test_nested_types_written_to_delta_keep_sparks_schema(lake) -> None:
    out = Kernel().run("spark.sql(\"SELECT named_struct('a', 1, 'b', 'x') AS s, array(1, 2) AS arr, "
                       "map('k', 1L) AS m, size(array(1)) AS n\").write.mode('overwrite').saveAsTable('dbo.nested')\n"
                       "print(spark.table('dbo.nested').dtypes)")
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    schema = DeltaTable(str(lake / "dbo" / "nested")).to_pyarrow_table().schema
    assert schema.field("s").type == pa.struct([("a", pa.int32()), ("b", pa.string())])
    assert pa.types.is_list(schema.field("arr").type) and schema.field("arr").type.value_type == pa.int32()
    assert pa.types.is_map(schema.field("m").type)
    assert schema.field("n").type == pa.int32()
    assert "('s', 'struct<a:int,b:string>')" in text and "('m', 'map<string,bigint>')" in text


STILL_DIFFERS = ["posexplode", "LATERAL VIEW", "DataFrame", "Databricks SQL", "array(1, 'a')"]


def test_docs_list_what_still_differs() -> None:
    text = DOCS.read_text(encoding="utf-8")
    assert "## Arrays, maps and structs on this PC" in text
    section = text.split("## Arrays, maps and structs on this PC", 1)[1].split("\n## ", 1)[0]
    missing = [name for name in STILL_DIFFERS if name not in section]
    assert not missing, missing
