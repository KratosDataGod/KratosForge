"""The SQL editor's function help (editor parity S07, S11): forms and summaries for Spark SQL, T-SQL and DuckDB."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from fabric_desktop import server, sql_function_help as fh
from fabric_desktop.audit import READ_ONLY

HERE = Path(fh.__file__).parent
ROOT = Path(__file__).resolve().parents[1]


def test_spark_functions_come_with_spark_s_own_usage():
    got = fh.function_help("spark", "date_trunc")
    assert got == {"name": "date_trunc", "more": 0,
                   "forms": [{"signature": "date_trunc(fmt, ts)",
                              "summary": "Returns timestamp `ts` truncated to the unit specified by the format model `fmt`."}]}
    assert len(fh.function_help("spark", "COUNT")["forms"]) == 3, "count(*), count(expr), count(DISTINCT expr)"


def test_tsql_functions_are_named_in_upper_case_for_every_t_sql_dialect():
    for dialect in ("warehouse", "endpoint", "sqldb"):
        got = fh.function_help(dialect, "dateadd")
        assert got["forms"][0]["signature"] == "DATEADD(datepart, number, date)"
    assert fh.function_help("warehouse", "date_trunc") is None, "a Spark function isn't T-SQL"


def test_duckdb_forms_come_from_the_engine_s_own_catalog():
    got = fh.function_help("duckdb", "date_trunc")
    assert got["forms"][0]["signature"].startswith("date_trunc(part VARCHAR, timestamp ")
    assert "→" in got["forms"][0]["signature"]
    many = fh.function_help("duckdb", "sum")
    assert len(many["forms"]) == fh.MAX_FORMS and many["more"] > 0, "one form per type, the rest counted"


@pytest.mark.parametrize("name", ["", "no_such_fn", "x;drop table t", "a b", "1abc", None, 7])
def test_nothing_for_unknown_or_odd_names(name):
    for dialect in ("spark", "warehouse", "duckdb"):
        assert fh.function_help(dialect, name) is None
    assert fh.function_help("oracle", "abs") is None


def test_every_tsql_function_the_editor_suggests_has_help():
    src = (ROOT.parent / "app" / "ui" / "sqleditor.js").read_text(encoding="utf-8")
    decl = re.search(r"const tsqlFns = ([^;]+);", src)
    assert decl, "tsqlFns not found in sqleditor.js"
    fns = " ".join(re.findall(r'"([^"]*)"', decl.group(1))).split()
    assert len(fns) > 100
    data = json.loads((HERE / "tsql_function_help.json").read_text(encoding="utf-8"))
    missing = [f for f in fns if f not in data]
    assert missing == []
    for name, entry in data.items():
        if name != "_source":
            assert isinstance(entry, list) and len(entry) == 2 and entry[1].endswith("."), name


def test_the_help_ships_with_the_engine_and_is_a_read_only_engine_call():
    pkg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]
    assert {"spark_function_help.json", "tsql_function_help.json"} <= set(pkg["fabric_desktop"])
    assert "sql_function_help" in server.METHODS
    assert "sql_function_help" in READ_ONLY
