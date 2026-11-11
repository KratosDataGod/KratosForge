"""Publishing and checking notebooks for Fabric: the helper edges the main tests don't reach. A
notebook using both the lakehouse module and Spark, string literals and comments Python can't read,
imports by name, SQL that isn't one change, notebook.run calls without names, Built-in files that
aren't text, and schedules."""

from __future__ import annotations

import ast

import pytest

from fabric_desktop import fabric_run as fr

LH = {"workspace_id": "ws1", "lakehouse_id": "lh1", "name": "LH_Gold"}


def test_a_notebook_using_the_module_and_spark_keeps_spark_and_gets_the_setup_cell():
    cells = [{"type": "code", "source": "from fabric_desktop import lakehouse\ndf = spark.read.table('x')"}]
    nb = fr.fabric_ipynb(cells, LH)
    assert "".join(nb["cells"][0]["source"]).startswith(fr.SHIM_MARKER)
    assert "spark.read.table" in "".join(nb["cells"][1]["source"])


def test_a_spark_sql_literal_python_cant_read_gives_no_sql():
    m = fr._SPARK_SQL_LITERAL.search('spark.sql("\\N{NOT A NAME}")')
    assert m is not None and fr._literal_sql(m) == ""


def test_a_comment_is_found_and_unreadable_source_has_none():
    src = "x = 1  # spark.sql('DROP TABLE t')\n"
    assert fr._in_comment(src, src.index("spark")) is True
    assert fr._in_comment(src, 0) is False
    broken = 'x = """never closed\nspark.sql("x")'
    assert fr._in_comment(broken, broken.index("spark")) is False


def test_dunder_import_names_the_top_module():
    assert fr._imported(ast.parse("__import__('pyspark.sql')").body[0].value) == "pyspark"
    assert fr._imported(ast.parse("importlib.import_module('pyspark.sql')").body[0].value) == "pyspark.sql"
    assert fr._imported(ast.parse("load('pyspark')").body[0].value) is None


def test_sql_built_too_deep_isnt_read():
    assert fr._sql_texts(ast.parse("'x'").body[0].value, {}, depth=6) is None


@pytest.mark.parametrize("sql", ["MERGE INTO (", "SELECT * FROM t", "UPDATE t SET a = 1; DELETE FROM t"])
def test_sql_that_isnt_one_change_has_no_target(sql):
    assert fr._change_target(sql) is None


def test_a_change_names_its_table():
    assert fr._change_target("DELETE FROM sales.orders WHERE id = 1") == "sales.orders"


def test_a_notebook_run_without_arguments_names_nothing():
    call = ast.parse("notebookutils.notebook.run()").body[0].value
    assert fr._run_names(call, "notebook.run") is None


def test_literals_and_the_root_lakehouse_flag():
    assert fr._literal(None) is None
    assert fr._literal(ast.parse("f(x)").body[0].value) is fr._UNREAD
    assert fr._root_in(None) is False
    no_key = ast.parse("{'when': today()}").body[0].value  # other values aren't literals, the key isn't there
    assert fr._root_in(no_key) is False
    with_key = ast.parse("{'useRootDefaultLakehouse': flag()}").body[0].value
    assert fr._root_in(with_key) is fr._UNREAD


def test_a_builtin_file_that_isnt_utf8_isnt_read(tmp_path):
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    (builtin / "setup.py").write_bytes(b"x = '\xff\xfe'\n")
    assert fr._builtin_cells("setup.py", str(tmp_path)) is None
    (builtin / "ok.sql").write_text("SELECT 1", encoding="utf-8")
    assert fr._builtin_cells("ok.sql", str(tmp_path)) == [{"type": "code", "source": "%%sql\nSELECT 1", "meta": {}}]


def test_a_daily_schedule_needs_a_time():
    with pytest.raises(ValueError, match="at least one time of day"):
        fr.schedule_configuration("daily", times=["  "])


def test_a_schedule_of_another_kind_is_described_by_its_type():
    assert fr.describe_schedule({"configuration": {"type": "Monthly"}}) == "Monthly"
    assert fr.describe_schedule({}) == "Schedule"
