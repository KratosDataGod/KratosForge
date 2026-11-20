"""fabric_run.py paths that the uncapped mutation run (batch 181, docs/quality/mutation-testing.md) showed
no test checked."""

from __future__ import annotations

import pytest

from fabric_desktop import fabric_run

V = fabric_run._VALUE


@pytest.fixture(autouse=True)
def _own_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def test_a_change_with_a_run_time_value_to_another_lakehouse_runs_in_fabric_only():
    sql = f"MERGE INTO LH_Keys.keys.customer t USING s ON t.id = s.id AND t.x = {V}1 WHEN MATCHED THEN DELETE"
    assert fabric_run._sql_where(sql, None) == "fabric"
    assert fabric_run._sql_where(f"DELETE FROM sales WHERE id = {V}1", "Sales") is None


LH = {"name": "Sales", "workspace_id": "ws1", "lakehouse_id": "lh1", "workspace_name": "Sales"}


def test_the_shim_cell_has_schemas_on_unless_told_otherwise():
    assert fabric_run.shim_cell(LH)["source"] == fabric_run.shim_cell(LH, True)["source"]
    assert fabric_run.shim_cell(LH)["source"] != fabric_run.shim_cell(LH, False)["source"]


def test_a_builtin_file_over_a_million_bytes_is_not_read(tmp_path):
    assert fabric_run._BUILTIN_MAX == 1_000_000
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    (builtin / "big.py").write_bytes(b"#" * 1_000_001)
    (builtin / "edge.py").write_bytes(b"#" * 1_000_000)
    assert fabric_run._builtin_cells("big.py", str(tmp_path)) is None
    assert fabric_run._builtin_cells("edge.py", str(tmp_path)) == [{"type": "code", "source": "#" * 1_000_000, "meta": {}}]


def test_a_comment_counts_only_from_where_it_starts_on_its_own_line():
    source = "a = 1\nx #c"
    assert fabric_run._in_comment(source, source.index("x")) is False
    assert fabric_run._in_comment(source, source.index("c")) is True


def _chain_of_names(n):
    import ast
    values = {"a0": [ast.Constant("SELECT 1")]}
    for i in range(1, n + 1):
        values[f"a{i}"] = [ast.Name(f"a{i - 1}")]
    return ast.Name(f"a{n}"), values


def test_sql_text_is_followed_through_five_names_and_no_more():
    assert fabric_run._sql_texts(*_chain_of_names(4)) == ["SELECT 1"]
    assert fabric_run._sql_texts(*_chain_of_names(5)) is None


@pytest.mark.parametrize("code, sql", [("spark.sql('x')", True), ("s.sql('x')", True),
                                       ("SparkSession.builder.getOrCreate().sql('x')", True),
                                       ("other.sql('x')", False), ("spark.table('x')", False)])
def test_session_sql_is_sql_on_any_name_of_the_session(code, sql):
    import ast
    assert fabric_run._session_sql(ast.parse(code).body[0].value, {"spark", "s"}) is sql


def _write_refusals(code):
    import ast
    out = {"fabric": [], "unknown_local": []}
    fabric_run._spark_refused(ast.parse(code).body[0].value, {}, out, 1)
    return out


def test_only_option_and_options_set_write_options():
    assert _write_refusals("df.write.mode(saveMode='overwrite').saveAsTable('t')") == {"fabric": [], "unknown_local": []}
    assert _write_refusals("df.write.options(bogus='1').saveAsTable('t')")["fabric"] == [
        (1, "uses the bogus write option, which saveAsTable doesn't take on this PC")]


def test_a_followed_notebook_shares_this_ones_lakehouse_unless_told_otherwise():
    cells = [{"type": "code", "source": "spark.sql('SELECT 1')"}]

    def load(name):
        return {"cells": cells, "lakehouse": None}
    assert fabric_run._followed("child", "%run", LH, load, (), None)["fabric"] is None
    assert fabric_run._followed("child", "%run", LH, load, (), None, root=False)["fabric"][0] == "blocked"


def test_a_notebook_or_builtin_file_already_on_the_way_is_a_loop(tmp_path):
    loop = "runs x ({how}), which runs notebooks in a loop or more than {n} deep, so the check stops there"

    def load(name):
        return {"cells": [], "lakehouse": None}
    got = fabric_run._followed("x", "%run", LH, load, ("X",), None)
    assert got["fabric"] == ("unknown", loop.format(how="%run", n=fabric_run.MAX_RUN_DEPTH))
    (tmp_path / "builtin").mkdir()
    (tmp_path / "builtin" / "x").write_text("x = 1", encoding="utf-8")
    got = fabric_run._followed("x", "%run -b", LH, None, ("builtin/x",), str(tmp_path))
    assert got["fabric"] == ("unknown", loop.format(how="%run -b", n=fabric_run.MAX_RUN_DEPTH))


def test_names_for_notebookutils_are_followed_five_assignments_deep():
    import ast
    src = "import notebookutils as nu\n" + "".join(f"a{i} = {'nu' if i == 1 else f'a{i - 1}'}\n" for i in range(1, 7))
    utils, _ = fabric_run._names([ast.parse(src)])
    assert sorted(utils) == ["a1", "a2", "a3", "a4", "a5", "nu"]


def test_names_for_the_spark_session_are_followed_five_assignments_deep():
    import ast
    values = {"s1": [ast.Name("spark")]}
    for i in range(2, 8):
        values[f"s{i}"] = [ast.Name(f"s{i - 1}")]
    assert fabric_run._sessions(values) == {"spark", "s1", "s2", "s3", "s4", "s5"}


def test_a_name_with_no_values_is_not_a_delta_table():
    import ast
    assert fabric_run._is_delta_table(ast.Name("t"), {}) is False
    assert fabric_run._is_delta_table(ast.Name("t"), {"t": [ast.parse("DeltaTable.forName(spark, 'x')").body[0].value]})


def test_a_fs_magic_blocks_only_a_python_cell_not_already_blocked():
    def local(source):
        return fabric_run.compute_check([{"type": "code", "source": source}], LH)["local"]["reasons"]
    assert [r["text"] for r in local('notebookutils.fs.mount("a", "b")\n%fs mount abfss://x /m')] == [
        "uses Fabric notebook utilities that need Fabric"]
    assert [r["text"] for r in local("%fs mount abfss://x /m")] == [
        "mounts storage with %fs mount, which runs in Databricks only"]
    assert local("%%sql\n%fs mount abfss://x /m") == []


def test_code_right_before_a_comment_is_not_in_it():
    source = "a = 1\nx#c"
    assert fabric_run._in_comment(source, source.index("x")) is False
    assert fabric_run._in_comment(source, source.index("#")) is True


@pytest.mark.parametrize("kw", [{"weekdays": ["Monday"]}, {"times": ["06:00"]}])
def test_a_weekly_schedule_needs_both_a_day_and_a_time(kw):
    with pytest.raises(ValueError, match="Pick at least one day and one time"):
        fabric_run.schedule_configuration("weekly", **kw)


def test_write_options_given_by_keyword_or_as_a_dict():
    assert _write_refusals("df.write.options(bogus='1')") == {"fabric": [], "unknown_local": []}
    assert _write_refusals("df.write.options({'a': '1'})")["unknown_local"] == [
        (1, "sets a write option named only at run time, so the check can't tell whether this PC's Spark takes it")]


def test_a_load_of_one_path_reads_parquet_not_a_format_named_by_the_path():
    assert _write_refusals("spark.read.load('Files/x.parquet')") == {"fabric": [], "unknown_local": []}


def test_a_python_notebook_keeps_fabrics_kernel_and_its_own_other_metadata():
    nb = fabric_run.fabric_ipynb([{"type": "code", "source": "from fabric_desktop import lakehouse"}], LH)
    assert nb["metadata"]["kernelspec"] == fabric_run.PYTHON_NOTEBOOK_METADATA["kernelspec"]
    assert nb["metadata"]["fabric_desktop"] == {"lakehouse": LH}
    assert nb["metadata"]["dependencies"]["lakehouse"]["default_lakehouse"] == "lh1"


def test_a_quoted_line_is_cut_after_60_characters():
    assert fabric_run._snippet("x" * 60) == "x" * 60
    assert fabric_run._snippet("x" * 61) == "x" * 57 + "..."


def test_a_comment_on_the_second_line_after_a_blank_first_line():
    source = "\nx#c"
    assert fabric_run._in_comment(source, source.index("x")) is False
    assert fabric_run._in_comment(source, source.index("#")) is True


def test_what_the_check_cannot_read_in_a_followed_notebook_is_passed_on():
    cells = [{"type": "code", "source": "exec(code)"}]
    got = fabric_run._followed("child", "%run", LH, lambda name: {"cells": cells, "lakehouse": None}, (), None)
    text = "runs child (%run), whose cell 1 runs code built at run time (exec), which the check can't read"
    assert got == {"local": ("unknown", text), "fabric": ("unknown", text)}


def test_sql_text_on_either_side_of_a_plus_is_followed_five_names_deep():
    import ast
    deep, values = _chain_of_names(4)  # read at depth 1 here, so its text sits at depth 6
    joined = fabric_run._sql_texts(ast.BinOp(deep, ast.Add(), ast.Constant(" x")), values)
    assert joined == [f"{V}0 x"]
    joined = fabric_run._sql_texts(ast.BinOp(ast.Constant("x "), ast.Add(), deep), values)
    assert joined == [f"x {V}1"]
    shallow, values = _chain_of_names(3)
    assert fabric_run._sql_texts(ast.BinOp(shallow, ast.Add(), ast.Constant(" x")), values) == ["SELECT 1 x"]


def test_sql_text_has_at_most_16_readings():
    import ast
    values = {"q": [ast.Constant(f"SELECT {i}") for i in range(17)]}
    assert len(fabric_run._sql_texts(ast.Name("q"), values)) == 16
    values |= {"l": [ast.Constant(f"a{i}") for i in range(4)], "r": [ast.Constant(f"b{i}") for i in range(5)]}
    assert len(fabric_run._sql_texts(ast.BinOp(ast.Name("l"), ast.Add(), ast.Name("r")), values)) == 16


def test_delta_maintenance_runs_here_when_the_preview_setting_is_not_set():
    assert _write_refusals("DeltaTable.forName(spark, 't').optimize()") == {"fabric": [], "unknown_local": []}


def test_save_as_table_arguments_and_keyword_splats_are_not_write_options():
    assert _write_refusals("df.write.saveAsTable('t', **opts)") == {"fabric": [], "unknown_local": []}
    assert _write_refusals("df.write.saveAsTable('t', mode='overwrite', format='delta')") == {
        "fabric": [], "unknown_local": []}


@pytest.mark.parametrize("code", ["spark.read.csv('f', **o)", "spark.read.csv(path='Files/x.csv')",
                                  "spark.read.format('csv', bogus=1).load('f')",
                                  "spark.read.option('mode', 'FAILFAST').csv('f', schema=s)"])
def test_read_options_are_only_option_options_and_the_readers_own_keywords(code):
    assert _write_refusals(code) == {"fabric": [], "unknown_local": []}


def test_a_csv_read_in_a_strict_mode_needs_a_schema():
    assert _write_refusals("spark.read.option('mode', 'FAILFAST').csv('f')")["fabric"] == [
        (1, "needs read.schema() for CSV mode FAILFAST, to say what a malformed value is")]


def test_a_schema_on_a_table_read_names_a_table():
    assert _write_refusals("spark.read.schema('a INT').table('t')")["fabric"] == [
        (1, ("applies read.schema() to a read of a table, which this PC's Spark refuses (leave it out and cast the "
            "columns after)"))]


def test_a_loader_that_gives_cells_without_a_lakehouse_is_still_read():
    got = fabric_run._followed("child", "%run", LH, lambda name: {"cells": [{"type": "code", "source": "x = 1"}]}, (), None)
    assert got == {"local": None, "fabric": None}


def test_a_followed_notebook_already_blocked_in_fabric_keeps_its_own_reason():
    cells = [{"type": "code", "source": "spark.read.csv('C:/data/x.csv')"}]
    got = fabric_run._followed("child", "notebook.run", LH, lambda name: {"cells": cells, "lakehouse": None}, (), None,
                               root=False)
    assert got["fabric"] == ("blocked", ("runs child (notebook.run), whose cell 1 reads or writes a path on this PC, "
                                        "which Fabric can't reach"))


def test_a_quoted_line_is_that_line_even_when_it_is_empty():
    assert fabric_run._line("\nx", 1) == ""
    assert fabric_run._line("\nx", 2) == "x"
    assert fabric_run._line("a\nb", 2) == "b"
    assert fabric_run._snippet("abc") == "abc"


def test_a_schedule_ends_ten_years_after_it_starts_by_default():
    import datetime as dt
    c = fabric_run.schedule_configuration("cron", every_minutes=60)
    start, end = (dt.datetime.fromisoformat(c[k]) for k in ("startDateTime", "endDateTime"))
    assert end - start == dt.timedelta(days=3650)


def test_an_f_string_part_is_followed_five_names_deep_and_has_at_most_16_readings():
    import ast
    deep, values = _chain_of_names(4)
    assert fabric_run._sql_texts(ast.JoinedStr([ast.FormattedValue(deep, -1, None)]), values) == [f"{V}0"]
    shallow, values = _chain_of_names(3)
    assert fabric_run._sql_texts(ast.JoinedStr([ast.FormattedValue(shallow, -1, None)]), values) == ["SELECT 1"]
    values = {"l": [ast.Constant(f"a{i}") for i in range(4)], "r": [ast.Constant(f"b{i}") for i in range(5)]}
    both = ast.JoinedStr([ast.FormattedValue(ast.Name("l"), -1, None), ast.FormattedValue(ast.Name("r"), -1, None)])
    assert len(fabric_run._sql_texts(both, values)) == 16


def test_a_name_given_a_value_the_check_cannot_see_or_another_name_is_not_a_delta_table():
    import ast
    assert fabric_run._is_delta_table(ast.Name("t"), {"t": [None]}) is False
    assert fabric_run._is_delta_table(ast.Name("t"), {"t": [ast.Name("u")], "u": []}) is False


def test_csv_read_options_count_from_options_and_a_schema_link_counts_as_a_schema():
    assert _write_refusals("spark.read.options(mode='FAILFAST').csv('f')")["fabric"] == [
        (1, "needs read.schema() for CSV mode FAILFAST, to say what a malformed value is")]
    assert _write_refusals("spark.read.schema(s).option('mode', 'FAILFAST').csv('f')") == {"fabric": [], "unknown_local": []}


def test_a_format_this_pc_does_not_read_is_named():
    assert _write_refusals("spark.read.format('xml').load('f')")["fabric"] == [
        (1, "reads format 'xml', which this PC's Spark doesn't have (it reads csv, json, parquet, text and delta)")]


def test_an_empty_format_is_not_refused_as_parquet():
    [(_, text)] = _write_refusals("spark.read.format('').load('f')")["fabric"]
    assert "'parquet'" not in text


def test_run_multiple_runs_each_notebook_without_this_ones_lakehouse():
    import ast
    call = ast.parse("notebookutils.notebook.runMultiple(['a', 'b'])").body[0].value
    assert fabric_run._run_names(call, "notebook.runMultiple") == [("a", False), ("b", False)]


def test_a_comment_further_along_the_line_does_not_cover_earlier_code():
    source = "a = 1\nxyz #c"
    assert fabric_run._in_comment(source, source.index("z")) is False


def test_relative_imports_are_not_notebookutils_and_a_from_import_names_its_part():
    import ast
    utils, _ = fabric_run._names([ast.parse("from . import x\nfrom notebookutils import fs\n")])
    assert utils == {"fs": "notebookutils.fs"}


def test_a_delta_table_by_its_full_module_path_is_one():
    import ast
    call = ast.parse("delta.tables.DeltaTable.forName(spark, 't')").body[0].value
    assert fabric_run._is_delta_table(call, {}) is True


def test_python_the_check_cannot_read_and_that_has_no_line_is_on_line_1():
    got = fabric_run.compute_check([{"type": "code", "source": "\nx = 1\x00"}], LH)["local"]["unknown"]
    assert got == [{"cell": 1, "text": "isn't Python the check can read (line 1: source code string cannot contain null "
                                       "bytes), so it can't tell where the cell runs", "code": ""}]


def test_a_dag_activity_runs_without_this_ones_lakehouse_unless_its_args_say_so():
    import ast
    call = ast.parse("notebookutils.notebook.runMultiple({'activities': [{'path': 'a'}, "
                     "{'path': 'b', 'args': {'useRootDefaultLakehouse': True}}]})").body[0].value
    assert fabric_run._run_names(call, "notebook.runMultiple") == [("a", False), ("b", True)]


def test_a_read_option_without_a_value_is_read_as_none():
    assert _write_refusals("spark.read.option('mode').csv('f')") == {"fabric": [], "unknown_local": []}


MERGE_KEYS = "MERGE INTO LH_Keys.keys.customer t USING s ON t.id = s.id WHEN MATCHED THEN DELETE"
IN_PLACE = "changes table rows in place (MERGE, UPDATE or DELETE of another lakehouse, or with Preview features off), " \
           "which runs in Fabric only"


def _local(source):
    return fabric_run.compute_check([{"type": "code", "source": source}], LH)["local"]


def test_spark_sql_by_its_sql_query_keyword_is_read():
    [reason] = _local(f'spark.sql(sqlQuery="{MERGE_KEYS}")')["reasons"]
    assert reason["text"] == ("changes table rows in place with spark.sql (MERGE, UPDATE or DELETE of another lakehouse, "
                              "or with Preview features off), which runs in Fabric only")


def test_a_sql_cell_this_pc_may_refuse_is_unknown_on_its_first_line():
    assert _local("\n%%sql\nDROP TABLE t")["unknown"] == [
        {"cell": 1, "text": "drops t (DROP TABLE), which runs here for a temp view and in Fabric for a lakehouse table",
         "code": ""}]


def test_a_sql_line_magic_is_quoted_by_its_own_line():
    [reason] = _local(f"x = 1\n%sql {MERGE_KEYS}\ny = 2")["reasons"]
    assert reason == {"cell": 1, "text": IN_PLACE, "code": "%sql MERGE INTO LH_Keys.keys.customer t USING s ON t.id =..."}


def test_a_sql_cell_that_names_sqldf_itself_is_left_as_a_sql_cell():
    cells = [{"type": "code", "source": "%%sql\nSELECT 1 AS _sqldf"}]
    assert fabric_run.fabric_sql_magics(cells, spark=True) == cells


def test_notebooks_are_followed_until_the_depth_limit():
    n = fabric_run.MAX_RUN_DEPTH
    deep = tuple(f"nb{i}" for i in range(n))
    got = fabric_run._followed("x", "%run", LH, lambda name: {"cells": [], "lakehouse": None}, deep, None)
    assert got["fabric"] == ("unknown", (f"runs x (%run), which runs notebooks in a loop or more than {n} deep, so the "
                                        "check stops there"))
    got = fabric_run._followed("x", "%run", LH, lambda name: {"cells": [], "lakehouse": None}, deep[1:], None)
    assert got == {"local": None, "fabric": None}


def test_line_zero_quotes_the_first_line():
    assert fabric_run._line("a\nb", 0) == "a"


def test_a_cron_schedule_runs_every_1_to_the_longest_interval_minutes():
    assert fabric_run.schedule_configuration("cron", every_minutes=1)["interval"] == 1
    top = fabric_run.MAX_EVERY_MINUTES
    assert fabric_run.schedule_configuration("cron", every_minutes=top)["interval"] == top
    with pytest.raises(ValueError, match="Pick how many minutes between runs"):
        fabric_run.schedule_configuration("cron", every_minutes=top + 1)


def test_save_as_table_off_a_writer_is_the_only_one_checked_for_write_options():
    assert _write_refusals("x.saveAsTable('t', bogus=1)") == {"fabric": [], "unknown_local": []}


def test_a_load_with_a_path_and_a_format_reads_that_format():
    assert _write_refusals("spark.read.load('Files/x.csv', 'csv')") == {"fabric": [], "unknown_local": []}
    assert _write_refusals("spark.read.load('Files/x.xml', 'xml')")["fabric"] == [
        (1, "reads format 'xml', which this PC's Spark doesn't have (it reads csv, json, parquet, text and delta)")]


def test_delta_maintenance_runs_here_when_the_settings_cannot_be_read(monkeypatch):
    from fabric_desktop import app_settings

    def broken(key):
        raise OSError("settings file is damaged")
    monkeypatch.setattr(app_settings, "value", broken)
    assert _write_refusals("DeltaTable.forName(spark, 't').optimize()") == {"fabric": [], "unknown_local": []}


def test_a_name_imported_from_another_module_and_then_set_to_a_notebookutils_part_is_that_part():
    src = ("import os as fs\nimport notebookutils as nu\nfs = nu.fs\n"
           "fs.mount('abfss://c@a.dfs.core.windows.net', '/data')")
    side = fabric_run.compute_check([{"type": "code", "source": src, "meta": {}}], LH)["local"]
    assert not side["ok"]


def test_a_cron_schedule_under_one_minute_is_refused():
    with pytest.raises(ValueError, match="Pick how many minutes between runs"):
        fabric_run.schedule_configuration("cron", every_minutes=0.5)
