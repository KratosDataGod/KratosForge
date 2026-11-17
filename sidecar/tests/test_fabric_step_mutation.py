"""Mutation batch 62: fabric_step.py's mark checks, table names, OPTIMIZE reading and the step notebook."""

from __future__ import annotations

import ast
import json

import pytest

from fabric_desktop import fabric_step

LAKEHOUSE = {"workspace_id": "w-1", "lakehouse_id": "l-1", "name": "LH"}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def body(nb) -> str:
    src = nb["cells"][0]["source"]
    return "".join(src) if isinstance(src, list) else src


def step_source(nb) -> str:
    """The cell's own code, as the step notebook hands it to compile()."""
    line = next(ln for ln in body(nb).splitlines() if "exec(compile(" in ln)
    call = ast.parse(line.strip()).body[0].value  # exec(compile(<source>, "cell", "exec"), globals())
    return ast.literal_eval(call.args[0].args[0])


# ---- the mark


def test_an_empty_marked_cell_is_named_as_empty():
    cells = [{"type": "code", "source": "a = 1"}, {"type": "code", "source": "   ", "meta": {"fabric_step": True}}]
    with pytest.raises(ValueError, match="The cell marked Run on Fabric is empty"):
        fabric_step.step_index(cells)


def test_an_empty_marked_cell_above_code_is_still_named_as_empty():
    cells = [{"type": "code", "source": "", "meta": {"fabric_step": True}}, {"type": "code", "source": "a = 1"}]
    with pytest.raises(ValueError, match="is empty"):
        fabric_step.step_index(cells)


def test_two_code_cells_below_the_mark_are_counted():
    cells = [{"type": "code", "source": "a", "meta": {"fabric_step": True}},
             {"type": "code", "source": "b"}, {"type": "code", "source": "c"}]
    with pytest.raises(ValueError, match="and 2 code cells are below"):
        fabric_step.step_index(cells)


# ---- table names


@pytest.mark.parametrize("table, quoted", [("t", "`t`"), ("gold.t", "`gold`.`t`"), ("a`b", "`a``b`"),
                                           (".t", "`t`"), ("gold..t", "`gold`.`t`")])
def test_table_name_quotes_one_or_two_parts(table, quoted):
    assert fabric_step.table_name(table) == quoted


@pytest.mark.parametrize("table", ["", ".", "..", "a.b.c"])
def test_table_name_refuses_no_parts_or_three(table):
    with pytest.raises(ValueError, match="name tables as table or schema.table"):
        fabric_step.table_name(table)


# ---- what the step OPTIMIZEs


def test_a_table_optimized_twice_is_listed_once():
    src = 'spark.sql("OPTIMIZE gold.t VORDER")\nspark.sql("optimize `gold`.`t` vorder")\nspark.sql("OPTIMIZE u")'
    assert fabric_step.optimized_tables(src) == ["gold.t", "u"]


# ---- the step notebook


def test_a_bare_sql_line_magic_becomes_spark_sql():
    nb = fabric_step.step_ipynb("NB", {"type": "code", "source": "%sql SELECT 1"}, LAKEHOUSE, "r")
    assert step_source(nb) == 'display(spark.sql("SELECT 1"))'


def test_an_indented_sql_cell_magic_becomes_spark_sql():
    nb = fabric_step.step_ipynb("NB", {"type": "code", "source": "  %%sql\nSELECT 2"}, LAKEHOUSE, "r")
    assert step_source(nb) == 'display(spark.sql("SELECT 2"))'


def test_python_cells_are_left_as_written():
    nb = fabric_step.step_ipynb("NB", {"type": "code", "source": "x = 1\nprint(x)"}, LAKEHOUSE, "r")
    assert step_source(nb) == "x = 1\nprint(x)"


def test_a_sql_line_magic_below_the_first_line_becomes_spark_sql():
    nb = fabric_step.step_ipynb("NB", {"type": "code", "source": "df = %sql SELECT 1\nprint(df.count())"},
                                LAKEHOUSE, "r")
    assert step_source(nb).startswith('df = spark.sql("SELECT 1")')


def test_a_syntax_error_names_its_line():
    with pytest.raises(ValueError, match=r"\(line 2: "):
        fabric_step.step_ipynb("NB", {"type": "code", "source": "x = 1\nx = = 2"}, LAKEHOUSE, "r")


def test_the_header_names_the_notebook_exactly():
    nb = fabric_step.step_ipynb('Sales "Q1"', {"type": "code", "source": "pass"}, LAKEHOUSE, "r")
    first = body(nb).splitlines()[0]
    assert first == '# Fabric Desktop step: the last cell of Sales \\"Q1\\", run on Fabric Spark. Published from Fabric'
    assert json.dumps('Sales "Q1"')[1:-1] in first


def test_the_result_goes_to_the_requests_file_in_the_lakehouse():
    nb = fabric_step.step_ipynb("NB", {"type": "code", "source": "pass"}, LAKEHOUSE, "req-9")
    assert fabric_step.result_path("req-9") in body(nb)
    assert "abfss://w-1@" in body(nb) and "/l-1/" in body(nb) and '"request_id": \'req-9\'' in body(nb)
