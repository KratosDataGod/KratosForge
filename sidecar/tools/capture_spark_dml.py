"""Capture Apache Spark 3.5 and Delta 3.2's answers to MERGE, UPDATE and DELETE for tests/test_spark_dml_parity.py.

PB-015, criterion 1: the parity suite covers MERGE with matched and not-matched clauses, UPDATE and
DELETE. Each case below is a set of Delta tables and a notebook cell that changes one of them, through
spark.sql or the DeltaTable API. Real Spark with Delta runs the cell; the fixture keeps the table's rows
afterwards, the counts the cell returned (``result``), or the error Spark raised. The test runs the same
cell on this PC, where delta-rs makes the change, and compares.

Needs PySpark 3.5, delta-spark 3.2 and Java, and is run by hand, never in CI (as the other capture tools):

    python -m venv spark && spark/bin/pip install pyspark==3.5.3 delta-spark==3.2.0
    spark/bin/python tools/capture_spark_dml.py

Spark runs in local mode, UTC, ANSI mode off, with the tables in a database dbo that is the current
one, so an unqualified name finds them as it finds the attached lakehouse's tables on this PC. A cell
sets ``result`` to what it returns (None for DeltaTable.update and delete, which return nothing).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "spark_3_5_3_delta_3_2_dml.json"

SALES = {"columns": [["id", "int"], ["name", "string"], ["amount", "double"]],
         "rows": [[1, "a", 10.0], [2, "b", 20.0], [3, "c", 30.0]]}


def view(name: str, ddl: str, rows: list[tuple]) -> str:
    return f"spark.createDataFrame({rows!r}, {ddl!r}).createOrReplaceTempView({name!r})\n"


UPD = view("upd", "id INT, name STRING, amount DOUBLE", [(2, "B", 25.0), (3, "C", 35.0), (4, "d", 40.0)])
FLAGGED = view("upd", "id INT, name STRING, amount DOUBLE, gone BOOLEAN",
               [(1, "A", 11.0, True), (2, "B", 25.0, False), (4, "d", 40.0, False), (5, "e", 50.0, True)])


def sql(statement: str) -> str:
    return f"result = spark.sql({statement!r})\n"


CASES: list[dict] = [
    # ---- MERGE: WHEN MATCHED
    {"id": "merge_update_set_star", "title": "WHEN MATCHED THEN UPDATE SET * copies every column",
     "code": UPD + sql("MERGE INTO sales t USING upd s ON t.id = s.id WHEN MATCHED THEN UPDATE SET *")},
    {"id": "merge_update_explicit_columns", "title": "WHEN MATCHED THEN UPDATE SET named columns, one an expression",
     "code": UPD + sql("MERGE INTO sales t USING upd s ON t.id = s.id "
                       "WHEN MATCHED THEN UPDATE SET t.amount = t.amount + s.amount, name = s.name")},
    {"id": "merge_matched_and_delete", "title": "WHEN MATCHED AND a condition THEN DELETE",
     "code": FLAGGED + sql("MERGE INTO sales t USING upd s ON t.id = s.id WHEN MATCHED AND s.gone THEN DELETE")},
    {"id": "merge_matched_delete_then_update", "title": "A conditional DELETE before an UPDATE of the other matches",
     "code": FLAGGED + sql("MERGE INTO sales t USING upd s ON t.id = s.id "
                           "WHEN MATCHED AND s.gone THEN DELETE WHEN MATCHED THEN UPDATE SET amount = s.amount")},
    {"id": "merge_matched_clauses_in_order", "title": "Several WHEN MATCHED clauses: the first that holds wins",
     "tables": {"sales": {"columns": SALES["columns"],
                          "rows": [[1, "a", 10.0], [2, "b", 20.0], [3, "c", 30.0], [4, "d", 40.0]]}},
     "code": view("upd", "id INT, amount DOUBLE", [(1, -5.0), (2, 500.0), (3, -1000.0), (4, 7.0)])
     + sql("MERGE INTO sales t USING upd s ON t.id = s.id "
           "WHEN MATCHED AND s.amount < 0 AND t.amount > 20 THEN DELETE "
           "WHEN MATCHED AND s.amount < 0 THEN UPDATE SET amount = 0 "
           "WHEN MATCHED AND s.amount > 100 THEN UPDATE SET amount = 100, name = 'capped' "
           "WHEN MATCHED THEN UPDATE SET amount = s.amount")},
    # ---- MERGE: WHEN NOT MATCHED
    {"id": "merge_insert_star", "title": "WHEN NOT MATCHED THEN INSERT *",
     "code": UPD + sql("MERGE INTO sales t USING upd s ON t.id = s.id WHEN NOT MATCHED THEN INSERT *")},
    {"id": "merge_insert_explicit_columns", "title": "WHEN NOT MATCHED THEN INSERT named columns: the rest are NULL",
     "code": UPD + sql("MERGE INTO sales t USING upd s ON t.id = s.id "
                       "WHEN NOT MATCHED THEN INSERT (id, amount) VALUES (s.id, s.amount * 2)")},
    {"id": "merge_insert_conditional", "title": "WHEN NOT MATCHED AND a condition, twice, in order",
     "code": view("upd", "id INT, name STRING, amount DOUBLE", [(4, "d", 40.0), (5, "e", -1.0), (6, "f", 0.0)])
     + sql("MERGE INTO sales t USING upd s ON t.id = s.id "
           "WHEN NOT MATCHED AND s.amount > 0 THEN INSERT * "
           "WHEN NOT MATCHED AND s.amount < 0 THEN INSERT (id, name, amount) VALUES (s.id, 'negative', 0)")},
    {"id": "merge_upsert", "title": "Update the matches, insert the rest (the upsert)",
     "code": UPD + sql("MERGE INTO sales t USING upd s ON t.id = s.id "
                       "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *")},
    {"id": "merge_all_three_kinds", "title": "DELETE, UPDATE and INSERT in one MERGE",
     "code": FLAGGED + sql("MERGE INTO sales t USING upd s ON t.id = s.id "
                           "WHEN MATCHED AND s.gone THEN DELETE "
                           "WHEN MATCHED THEN UPDATE SET amount = s.amount "
                           "WHEN NOT MATCHED AND NOT s.gone THEN INSERT (id, name, amount) VALUES (s.id, s.name, s.amount)")},
    {"id": "merge_not_matched_by_source", "title": "WHEN NOT MATCHED BY SOURCE: update one, delete another",
     "tables": {"sales": {"columns": SALES["columns"],
                          "rows": [[1, "a", 10.0], [2, "b", 20.0], [3, "c", 30.0], [4, "d", 40.0]]}},
     "code": view("upd", "id INT, name STRING, amount DOUBLE", [(1, "A", 11.0)])
     + sql("MERGE INTO sales t USING upd s ON t.id = s.id WHEN MATCHED THEN UPDATE SET * "
           "WHEN NOT MATCHED BY SOURCE AND t.id = 2 THEN UPDATE SET name = 'stale' "
           "WHEN NOT MATCHED BY SOURCE AND t.amount > 25 THEN DELETE")},
    # ---- MERGE: the source and the keys
    {"id": "merge_source_subquery", "title": "The source is a subquery that aggregates",
     "code": view("raw", "id INT, amount DOUBLE", [(1, 1.0), (1, 2.0), (3, 5.0), (9, 9.0)])
     + sql("MERGE INTO sales t USING (SELECT id, sum(amount) AS total FROM raw GROUP BY id) s ON t.id = s.id "
           "WHEN MATCHED THEN UPDATE SET amount = t.amount + s.total "
           "WHEN NOT MATCHED THEN INSERT (id, name, amount) VALUES (s.id, 'new', s.total)")},
    {"id": "merge_source_temp_view_of_a_query", "title": "The source is a temp view made with a query",
     "code": UPD + "spark.sql('SELECT id, name, amount FROM upd WHERE id > 2').createOrReplaceTempView('later')\n"
     + sql("MERGE INTO sales AS target USING later AS source ON target.id = source.id "
           "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *")},
    {"id": "merge_source_another_table", "title": "The source is another Delta table",
     "tables": {"sales": SALES, "prices": {"columns": [["id", "int"], ["amount", "double"]],
                                           "rows": [[2, 99.0], [7, 70.0]]}},
     "code": sql("MERGE INTO sales t USING prices p ON t.id = p.id WHEN MATCHED THEN UPDATE SET amount = p.amount "
                 "WHEN NOT MATCHED THEN INSERT (id, amount) VALUES (p.id, p.amount)")},
    {"id": "merge_null_keys_never_match", "title": "A NULL key matches nothing, not even another NULL",
     "tables": {"sales": {"columns": SALES["columns"], "rows": [[1, "a", 10.0], [None, "n", 0.0]]}},
     "code": view("upd", "id INT, name STRING, amount DOUBLE", [(None, "m", 5.0), (1, "A", 11.0)])
     + sql("MERGE INTO sales t USING upd s ON t.id = s.id "
           "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *")},
    {"id": "merge_null_safe_key", "title": "ON with <=> does match a NULL key to a NULL key",
     "tables": {"sales": {"columns": SALES["columns"], "rows": [[1, "a", 10.0], [None, "n", 0.0]]}},
     "code": view("upd", "id INT, name STRING, amount DOUBLE", [(None, "m", 5.0)])
     + sql("MERGE INTO sales t USING upd s ON t.id <=> s.id "
           "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *")},
    {"id": "merge_on_with_a_filter", "title": "ON with a key and a filter on the table",
     "code": UPD + sql("MERGE INTO sales t USING upd s ON t.id = s.id AND t.amount > 25 "
                       "WHEN MATCHED THEN UPDATE SET amount = s.amount WHEN NOT MATCHED THEN INSERT *")},
    {"id": "merge_two_key_columns", "title": "A key of two columns",
     "tables": {"sales": {"columns": [["region", "string"], ["id", "int"], ["amount", "double"]],
                          "rows": [["EU", 1, 1.0], ["US", 1, 2.0], ["EU", 2, 3.0]]}},
     "code": view("upd", "region STRING, id INT, amount DOUBLE", [("US", 1, 20.0), ("US", 2, 30.0)])
     + sql("MERGE INTO sales t USING upd s ON t.region = s.region AND t.id = s.id "
           "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *")},
    {"id": "merge_duplicate_source_rows_all_delete", "title": "Two source rows may match one row when the only clause deletes it",
     "code": view("dup", "id INT", [(1,), (1,)]) + sql("MERGE INTO sales t USING dup s ON t.id = s.id WHEN MATCHED THEN DELETE")},
    {"id": "merge_duplicate_source_rows_insert_only", "title": "Duplicate source rows that only insert are all inserted",
     "code": view("dup", "id INT, name STRING, amount DOUBLE", [(8, "x", 1.0), (8, "y", 2.0)])
     + sql("MERGE INTO sales t USING dup s ON t.id = s.id WHEN NOT MATCHED THEN INSERT *")},
    {"id": "merge_case_insensitive_columns", "title": "Column names match the table's without regard to case",
     "code": view("upd", "ID INT, Name STRING, AMOUNT DOUBLE", [(2, "B", 25.0), (4, "d", 40.0)])
     + sql("MERGE INTO sales t USING upd s ON t.ID = s.id WHEN MATCHED THEN UPDATE SET * "
           "WHEN NOT MATCHED THEN INSERT *")},
    {"id": "merge_no_rows_match", "title": "A MERGE that matches nothing changes nothing",
     "code": view("upd", "id INT, name STRING, amount DOUBLE", [(9, "z", 1.0)])
     + sql("MERGE INTO sales t USING upd s ON t.id = s.id WHEN MATCHED THEN DELETE")},
    # ---- MERGE: Spark's errors
    {"id": "merge_two_source_rows_one_target_row", "title": "Two source rows that update one table row: Delta refuses",
     "code": view("dup", "id INT, amount DOUBLE", [(1, 5.0), (1, 6.0)])
     + sql("MERGE INTO sales t USING dup s ON t.id = s.id WHEN MATCHED THEN UPDATE SET amount = s.amount")},
    {"id": "merge_two_source_rows_conditional_delete", "title": "Two source rows and a conditional DELETE: Delta refuses",
     "code": view("dup", "id INT", [(2,), (2,)])
     + sql("MERGE INTO sales t USING dup s ON t.id = s.id WHEN MATCHED AND t.id > 0 THEN DELETE")},
    {"id": "merge_duplicates_a_condition_leaves_out", "title": "Two source rows for one table row that the clause's condition leaves out: Delta still refuses",
     "code": view("dup", "id INT, x BOOLEAN", [(1, False), (1, False)])
     + sql("MERGE INTO sales t USING dup s ON t.id = s.id WHEN MATCHED AND s.x THEN UPDATE SET amount = 1")},
    {"id": "merge_duplicates_by_source_only", "title": "Two source rows for one table row with only WHEN NOT MATCHED BY SOURCE",
     "code": view("dup", "id INT", [(1,), (1,)])
     + sql("MERGE INTO sales t USING dup s ON t.id = s.id WHEN NOT MATCHED BY SOURCE THEN DELETE")},
    {"id": "merge_duplicates_on_an_expression", "title": "Two source rows for one table row, ON not a plain key",
     "code": view("dup", "id INT", [(1,), (1,)])
     + sql("MERGE INTO sales t USING dup s ON t.id = s.id + 0 WHEN MATCHED THEN UPDATE SET amount = 1")},
    {"id": "merge_without_when", "title": "A MERGE with no WHEN clause",
     "code": UPD + sql("MERGE INTO sales t USING upd s ON t.id = s.id")},
    {"id": "merge_insert_star_missing_source_column", "title": "INSERT * with a table column the source hasn't got",
     "code": view("upd", "id INT", [(9,)]) + sql("MERGE INTO sales t USING upd s ON t.id = s.id WHEN NOT MATCHED THEN INSERT *")},
    {"id": "merge_not_matched_then_delete", "title": "WHEN NOT MATCHED THEN DELETE: there is no table row to delete",
     "code": UPD + sql("MERGE INTO sales t USING upd s ON t.id = s.id WHEN NOT MATCHED THEN DELETE")},
    {"id": "merge_by_source_update_star", "title": "WHEN NOT MATCHED BY SOURCE THEN UPDATE SET *: there is no source row",
     "code": UPD + sql("MERGE INTO sales t USING upd s ON t.id = s.id WHEN NOT MATCHED BY SOURCE THEN UPDATE SET *")},
    {"id": "merge_by_source_insert", "title": "WHEN NOT MATCHED BY SOURCE THEN INSERT: there is no source row",
     "code": UPD + sql("MERGE INTO sales t USING upd s ON t.id = s.id WHEN NOT MATCHED BY SOURCE THEN INSERT *")},
    {"id": "merge_value_changes_to_fit_the_column", "title": "A source DOUBLE into an INT column",
     "tables": {"counts": {"columns": [["id", "int"], ["n", "int"]], "rows": [[1, 1]]}}, "check": "counts",
     "code": view("upd", "id INT, n DOUBLE", [(1, 2.75)])
     + sql("MERGE INTO counts t USING upd s ON t.id = s.id WHEN MATCHED THEN UPDATE SET n = s.n")},
    {"id": "merge_target_missing", "title": "MERGE INTO a table that doesn't exist",
     "code": UPD + sql("MERGE INTO nope t USING upd s ON t.id = s.id WHEN MATCHED THEN DELETE")},
    # ---- UPDATE
    {"id": "update_with_where", "title": "UPDATE with WHERE",
     "code": sql("UPDATE sales SET amount = amount * 2 WHERE id >= 2")},
    {"id": "update_without_where", "title": "UPDATE without WHERE changes every row",
     "code": sql("UPDATE sales SET name = 'all'")},
    {"id": "update_same_column_expression", "title": "UPDATE with an expression on the column it sets",
     "code": sql("UPDATE sales SET amount = amount * 1.5 + 1 WHERE name <> 'b'")},
    {"id": "update_assignments_read_old_values", "title": "UPDATE's assignments all read the row as it was",
     "tables": {"pairs": {"columns": [["id", "int"], ["a", "int"], ["b", "int"]], "rows": [[1, 1, 2], [2, 3, 4]]}},
     "check": "pairs", "code": sql("UPDATE pairs SET a = b, b = a")},
    {"id": "update_where_skips_nulls", "title": "UPDATE's WHERE takes no row where it is NULL",
     "tables": {"sales": {"columns": SALES["columns"], "rows": [[1, "a", 10.0], [2, "b", None], [3, None, 30.0]]}},
     "code": sql("UPDATE sales SET amount = 0 WHERE amount > 5 OR name = 'b'")},
    {"id": "update_with_alias", "title": "UPDATE with an alias and a CASE",
     "code": sql("UPDATE sales AS s SET s.name = CASE WHEN s.amount > 15 THEN 'big' ELSE 'small' END")},
    {"id": "update_to_null", "title": "UPDATE a column to NULL",
     "code": sql("UPDATE sales SET name = NULL WHERE id = 1")},
    {"id": "update_value_changes_to_fit_the_column", "title": "UPDATE sets an INT column to 2.5",
     "code": sql("UPDATE sales SET id = 2.5 WHERE id = 1")},
    {"id": "update_set_star", "title": "UPDATE SET *, which only MERGE has",
     "code": sql("UPDATE sales SET *")},
    {"id": "update_partition_column", "title": "UPDATE of a partition column moves the rows",
     "tables": {"parts": {"columns": [["id", "int"], ["region", "string"], ["amount", "double"]],
                          "rows": [[1, "EU", 1.0], [2, "US", 2.0], [3, "EU", 3.0]], "partition_by": ["region"]}},
     "check": "parts", "code": sql("UPDATE parts SET region = 'APAC' WHERE region = 'EU' AND id = 3")},
    {"id": "update_where_subquery", "title": "UPDATE with WHERE ... IN (SELECT ...)",
     "tables": {"sales": SALES, "keep": {"columns": [["id", "int"]], "rows": [[1], [3]]}},
     "code": sql("UPDATE sales SET amount = 0 WHERE id IN (SELECT id FROM keep)")},
    {"id": "update_from", "title": "UPDATE ... FROM, which Spark SQL hasn't got",
     "tables": {"sales": SALES, "prices": {"columns": [["id", "int"], ["amount", "double"]], "rows": [[2, 99.0]]}},
     "code": sql("UPDATE sales SET amount = p.amount FROM prices p WHERE sales.id = p.id")},
    {"id": "update_missing_column", "title": "UPDATE of a column the table hasn't got",
     "code": sql("UPDATE sales SET price = 1")},
    # ---- DELETE
    {"id": "delete_with_where", "title": "DELETE with WHERE",
     "code": sql("DELETE FROM sales WHERE amount > 15")},
    {"id": "delete_where_subquery_in", "title": "DELETE with WHERE ... IN (SELECT ...)",
     "tables": {"sales": SALES, "gone": {"columns": [["id", "int"]], "rows": [[1], [3], [9]]}},
     "code": sql("DELETE FROM sales WHERE id IN (SELECT id FROM gone)")},
    {"id": "delete_without_where", "title": "DELETE without WHERE empties the table",
     "code": sql("DELETE FROM sales")},
    {"id": "delete_where_null", "title": "DELETE's WHERE takes no row where it is NULL",
     "tables": {"sales": {"columns": SALES["columns"], "rows": [[1, "a", 10.0], [2, "b", None]]}},
     "code": sql("DELETE FROM sales WHERE amount < 100")},
    {"id": "delete_using", "title": "DELETE ... USING, which Spark SQL hasn't got",
     "tables": {"sales": SALES, "gone": {"columns": [["id", "int"]], "rows": [[1]]}},
     "code": sql("DELETE FROM sales USING gone WHERE sales.id = gone.id")},
    {"id": "delete_nothing", "title": "DELETE whose WHERE takes no row",
     "code": sql("DELETE FROM sales WHERE id > 100")},
    {"id": "delete_partition", "title": "DELETE a whole partition",
     "tables": {"parts": {"columns": [["id", "int"], ["region", "string"], ["amount", "double"]],
                          "rows": [[1, "EU", 1.0], [2, "US", 2.0], [3, "EU", 3.0]], "partition_by": ["region"]}},
     "check": "parts", "code": sql("DELETE FROM parts WHERE region = 'EU'")},
    # ---- the DeltaTable API
    {"id": "api_merge_update_insert_expressions", "title": "DeltaTable.merge with whenMatchedUpdate and whenNotMatchedInsert",
     "code": UPD + "from delta.tables import DeltaTable\n"
     "result = (DeltaTable.forName(spark, 'sales').alias('t').merge(spark.table('upd').alias('s'), 't.id = s.id')\n"
     "    .whenMatchedUpdate(set={'amount': 't.amount + s.amount'})\n"
     "    .whenNotMatchedInsert(values={'id': 's.id', 'name': 's.name', 'amount': 's.amount'})\n"
     "    .execute())\n"},
    {"id": "api_merge_columns", "title": "DeltaTable.merge with Column conditions and values",
     "code": FLAGGED + "from delta.tables import DeltaTable\nfrom pyspark.sql import functions as F\n"
     "result = (DeltaTable.forName(spark, 'sales').alias('t').merge(spark.table('upd').alias('s'), F.col('t.id') == F.col('s.id'))\n"
     "    .whenMatchedDelete(condition=F.col('s.gone'))\n"
     "    .whenMatchedUpdate(set={'amount': F.col('s.amount') * 2})\n"
     "    .whenNotMatchedInsert(condition=~F.col('s.gone'), values={'id': F.col('s.id'), 'amount': F.lit(0.0)})\n"
     "    .execute())\n"},
    {"id": "api_merge_all", "title": "DeltaTable.merge with whenMatchedUpdateAll and whenNotMatchedInsertAll",
     "code": UPD + "from delta.tables import DeltaTable\n"
     "result = (DeltaTable.forName(spark, 'sales').alias('t').merge(spark.table('upd').alias('s'), 't.id = s.id')\n"
     "    .whenMatchedUpdateAll(condition='s.amount > 30').whenNotMatchedInsertAll().execute())\n"},
    {"id": "api_merge_not_matched_by_source", "title": "DeltaTable.merge with whenNotMatchedBySource clauses",
     "code": view("upd", "id INT, name STRING, amount DOUBLE", [(1, "A", 11.0)])
     + "from delta.tables import DeltaTable\n"
     "result = (DeltaTable.forName(spark, 'sales').alias('t').merge(spark.table('upd').alias('s'), 't.id = s.id')\n"
     "    .whenMatchedUpdateAll()\n"
     "    .whenNotMatchedBySourceUpdate(condition='t.id = 2', set={'name': \"'stale'\"})\n"
     "    .whenNotMatchedBySourceDelete()\n"
     "    .execute())\n"},
    {"id": "api_merge_default_alias", "title": "DeltaTable.merge without aliases: the table by its name, the source as source",
     "code": "from delta.tables import DeltaTable\n"
     "src = spark.createDataFrame([(1, 'A', 1.0)], 'id INT, name STRING, amount DOUBLE')\n"
     "result = DeltaTable.forName(spark, 'sales').merge(src, 'sales.id = source.id').whenMatchedUpdateAll().execute()\n"},
    {"id": "api_merge_duplicate_source_rows", "title": "DeltaTable.merge with two source rows for one table row: Delta refuses",
     "code": "from delta.tables import DeltaTable\n"
     "src = spark.createDataFrame([(1, 5.0), (1, 6.0)], 'id INT, amount DOUBLE')\n"
     "result = (DeltaTable.forName(spark, 'sales').alias('t').merge(src.alias('s'), 't.id = s.id')\n"
     "    .whenMatchedUpdate(set={'amount': 's.amount'}).execute())\n"},
    {"id": "api_update", "title": "DeltaTable.update with a condition and Column values",
     "code": "from delta.tables import DeltaTable\nfrom pyspark.sql import functions as F\n"
     "result = DeltaTable.forName(spark, 'sales').update(F.col('id') > 1, {'amount': F.col('amount') + 1})\n"},
    {"id": "api_update_sql_text", "title": "DeltaTable.update with SQL text for the condition and the values",
     "code": "from delta.tables import DeltaTable\n"
     "result = DeltaTable.forName(spark, 'sales').update('id = 3', {'name': \"'z'\", 'amount': 'amount - 1'})\n"},
    {"id": "api_update_every_row", "title": "DeltaTable.update without a condition",
     "code": "from delta.tables import DeltaTable\n"
     "result = DeltaTable.forName(spark, 'sales').update(set={'amount': '0'})\n"},
    {"id": "api_delete", "title": "DeltaTable.delete with a condition",
     "code": "from delta.tables import DeltaTable\n"
     "result = DeltaTable.forName(spark, 'sales').delete(\"name = 'b' OR amount > 25\")\n"},
    {"id": "api_delete_column_condition", "title": "DeltaTable.delete with a Column condition",
     "code": "from delta.tables import DeltaTable\nfrom pyspark.sql import functions as F\n"
     "result = DeltaTable.forName(spark, 'sales').delete(F.col('id') == 2)\n"},
    {"id": "api_delete_every_row", "title": "DeltaTable.delete without a condition",
     "code": "from delta.tables import DeltaTable\nresult = DeltaTable.forName(spark, 'sales').delete()\n"},
]

_TYPES = {"int": "INT", "bigint": "BIGINT", "string": "STRING", "double": "DOUBLE", "boolean": "BOOLEAN"}


def cases() -> list[dict]:
    """Each case with its tables (sales when it names none) and the table it checks (sales when it names none)."""
    return [{"id": c["id"], "title": c["title"], "tables": c.get("tables", {"sales": SALES}),
             "check": c.get("check", "sales"), "code": c["code"]} for c in CASES]


def capture() -> dict:
    from delta import configure_spark_with_delta_pip
    from pyspark.sql import SparkSession
    b = (SparkSession.builder.master("local[1]").config("spark.ui.enabled", "false")
         .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
         .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
         .config("spark.sql.session.timeZone", "UTC").config("spark.sql.ansi.enabled", "false")
         .config("spark.sql.shuffle.partitions", "1").config("spark.ui.showConsoleProgress", "false")
         .config("spark.sql.warehouse.dir", tempfile.mkdtemp(prefix="kf-dml-warehouse-")))
    spark = configure_spark_with_delta_pip(b).getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    import pyspark
    out = []
    for case in cases():
        spark.sql("DROP DATABASE IF EXISTS dbo CASCADE")
        spark.sql("CREATE DATABASE dbo")
        spark.sql("USE dbo")
        for row in spark.sql("SHOW VIEWS").collect():
            if row.isTemporary:
                spark.catalog.dropTempView(row.viewName)
        for name, spec in case["tables"].items():
            ddl = ", ".join(f"{n} {_TYPES[k]}" for n, k in spec["columns"])
            w = spark.createDataFrame([tuple(r) for r in spec["rows"]], ddl).coalesce(1).write.format("delta")
            if spec.get("partition_by"):
                w = w.partitionBy(*spec["partition_by"])
            w.saveAsTable(f"dbo.{name}")
        namespace: dict = {"spark": spark}
        record = dict(case)
        try:
            exec(case["code"], namespace)  # noqa: S102  # the cases above, run by hand in a throwaway Spark
            result = namespace.get("result")
            got = result.collect()[0].asDict() if result is not None else None
            rows = [r.asDict() for r in spark.table(f"dbo.{case['check']}").collect()]
            record["spark"] = {"result": got, "rows": rows}
        except Exception as exc:  # noqa: BLE001  # silent-ok: Spark's error is the answer being recorded
            text = str(getattr(exc, "desc", None) or exc).strip().splitlines()[0]
            cls = getattr(exc, "getErrorClass", lambda: None)() if hasattr(exc, "getErrorClass") else None
            rows = [r.asDict() for r in spark.table(f"dbo.{case['check']}").collect()] \
                if spark.catalog.tableExists(f"dbo.{case['check']}") else None
            record["spark"] = {"error": type(exc).__name__, "error_class": cls, "message": text[:400], "rows": rows}
        print(case["id"], json.dumps(record["spark"])[:300], file=sys.stderr)
        out.append(record)
    from importlib.metadata import version
    return {"spark": pyspark.__version__, "delta": version("delta-spark"),
            "time_zone": "UTC", "ansi": False, "cases": out}


if __name__ == "__main__":
    data = capture()
    OUT.write_text(json.dumps(data, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    print(f"{len(data['cases'])} cases written to {OUT}", file=sys.stderr)
