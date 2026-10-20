"""Line magics and SQL results in notebook cells (magics.py and the kernel)."""

import json

from fabric_desktop import depgraph, envprofile, magics, proof
from fabric_desktop.fabric_run import compute_check, fabric_ipynb, fabric_sql_magics
from fabric_desktop.notebook import Kernel

LH = {"workspace_id": "w", "lakehouse_id": "l", "name": "LH"}


def _view(k):
    k.run("spark.createDataFrame([(1, 'a'), (2, 'b'), (3, 'b')], ['id', 'v']).createOrReplaceTempView('t')")


def test_sql_cell_keeps_its_result_in_sqldf_for_the_next_cell():
    k = Kernel()
    _view(k)
    out = k.run("%%sql\nSELECT v, count(*) AS n FROM t GROUP BY v ORDER BY v")
    assert out["status"] == "ok" and out["outputs"][0]["rows"] == [["a", 1], ["b", 2]]
    out = k.run("_sqldf.filter('n > 1').count()")
    assert out["outputs"] == [{"type": "text", "text": "1"}]
    assert any(v["name"] == "_sqldf" for v in k.variables())


def test_sql_cell_that_returns_nothing_leaves_sqldf_alone():
    k = Kernel()
    _view(k)
    k.run("%%sql\nSELECT 1 AS one")
    k.run("%%sql\nCREATE OR REPLACE TEMP VIEW t2 AS SELECT * FROM t")
    assert k.run("_sqldf.columns")["outputs"] == [{"type": "text", "text": "['one']"}]


def test_sql_line_magic_returns_a_dataframe_inside_python():
    k = Kernel()
    _view(k)
    out = k.run("df = %sql SELECT * FROM t WHERE v = 'b'\nprint(df.count())\n%sql SELECT max(id) AS top FROM t")
    assert out["status"] == "ok", out
    assert out["outputs"][0]["text"].strip() == "2"
    assert out["outputs"][-1]["rows"] == [[3]]


def test_sql_line_magic_refuses_in_place_changes_and_needs_a_query():
    k = Kernel()
    out = k.run("x = %sql MERGE INTO a USING b ON a.id = b.id WHEN MATCHED THEN DELETE")
    assert out["status"] == "error" and "Fabric" in out["outputs"][-1]["evalue"]
    out = k.run("x = %sql")
    assert out["status"] == "error" and "needs a query" in out["outputs"][-1]["evalue"]


def test_unknown_magic_names_the_supported_ones():
    out = Kernel().run("%nope")
    assert "%sql" in out["outputs"][-1]["evalue"]


def test_rewrite_and_blank_keep_assignments_and_line_numbers():
    code = "a = 1\n  df = %sql SELECT 1\n%time a + 1\nb, c = %sql SELECT 1, 2\nx = 5 % 2"
    assert magics.rewrite(code).split("\n")[1] == "  df = __fd_magic__('sql', 'SELECT 1')"
    assert magics.blank(code) == "a = 1\n  df = None\n\nb, c = None\nx = 5 % 2"
    assert [(m.line, m.name, m.target) for m in magics.find(code)] == [(2, "sql", "df"), (3, "time", None),
                                                                       (4, "sql", "b, c")]


def test_returns_rows_reads_the_last_statement():
    assert magics.returns_rows("SELECT 1")
    assert magics.returns_rows("-- top\nWITH x AS (SELECT 1) SELECT * FROM x")
    assert magics.returns_rows("CREATE TEMP VIEW v AS SELECT 1;\nSELECT * FROM v;")
    assert not magics.returns_rows("INSERT INTO t SELECT 1")
    assert not magics.returns_rows("SELECT 1;\nDROP TABLE t")
    assert not magics.returns_rows("")


def test_dependencies_see_sqldf_and_sql_line_reads():
    sql_cell = depgraph.analyze("%%sql\nSELECT * FROM dbo.sales")
    assert "_sqldf" in sql_cell["defines"]
    py = depgraph.analyze("df = %sql SELECT * FROM dbo.orders\nn = df.count()")
    assert py["parsed"] and "df" in py["defines"]
    assert {"kind": "table", "name": "dbo.orders", "line": 1} in py["reads"]
    graph = depgraph.graph([{"type": "code", "source": "%%sql\nSELECT * FROM dbo.sales", "cell_id": "a"},
                            {"type": "code", "source": "_sqldf.count()", "cell_id": "b"}])
    assert json.dumps(graph).count('"_sqldf"') >= 1


def test_environment_check_parses_cells_with_assigned_magics():
    found = envprofile.imports("import polars as pl\ndf = %sql SELECT 1")
    assert found["parsed"] and [m["module"] for m in found["modules"]] == ["polars"]


def test_compute_check_and_proof_catch_sql_line_writes():
    cells = [{"type": "code", "source": "from fabric_desktop import lakehouse\n"
                                        "x = %sql DELETE FROM dbo.sales WHERE id = 1"}]
    local = compute_check(cells, LH)["local"]
    assert not local["ok"] and "in place" in local["reasons"][0]["text"]
    assert local["reasons"][0]["code"].startswith("x = %sql DELETE")
    checked = proof.check([{"type": "code", "source": "from fabric_desktop import lakehouse\n"
                                                      "%sql INSERT INTO dbo.t SELECT 1"}], LH)
    assert any("creates or changes tables" in b["text"] and b["line"] == 2 for b in checked["blockers"])


def test_publish_turns_sql_magics_into_calls_fabric_has():
    cells = [{"type": "code", "source": "%%sql\nSELECT * FROM sales"},
             {"type": "code", "source": "top = _sqldf.limit(5)\n  df = %sql SELECT 1 AS \"x\"\n%sql SELECT 2"},
             {"type": "code", "source": "%%sql\nSELECT * FROM other"}]
    spark = fabric_sql_magics(cells, spark=True)
    assert spark[0]["source"] == '_sqldf = spark.sql("SELECT * FROM sales")\ndisplay(_sqldf)'
    assert spark[1]["source"].split("\n")[1:] == ['  df = spark.sql("SELECT 1 AS \\"x\\"")', 'display(spark.sql("SELECT 2"))']
    assert spark[2]["source"] == "%%sql\nSELECT * FROM other"  # nothing after it reads _sqldf
    python = fabric_sql_magics(cells, spark=False)
    assert python[0]["source"].startswith("_sqldf = lakehouse.sql(")
    nb = fabric_ipynb(cells, None)
    assert "".join(nb["cells"][0]["source"]).startswith("_sqldf = spark.sql(")
