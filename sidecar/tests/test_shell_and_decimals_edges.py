"""Shell cells (shell.py) and Spark's decimal result types (spark_decimals.py), the paths the main
tests don't reach: %%powershell and %%cmd command lines, a blocked %%cmd script, output that is slow
to come, and decimal expressions whose types can't be worked out, which keep DuckDB's types."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
import sqlglot

from fabric_desktop import shell, spark_decimals, sql
from fabric_desktop.spark_decimals import spark_decimal_columns, spark_decimal_types

# ---- shell.py


def test_braces_that_arent_python_and_dollars_stay_for_the_shell():
    ns = {"n": 3, "name": "orders"}
    assert shell.expand("echo {n + 1} {not python at all} $name $$HOME $missing", ns) == \
        "echo 4 {not python at all} orders $HOME $missing"


def test_powershell_runs_the_cell_from_stdin_without_a_profile(monkeypatch):
    ran = []
    monkeypatch.setattr(shell, "trusted_cli", lambda name: "C:/pwsh/pwsh.exe" if name == "pwsh" else None)
    monkeypatch.setattr(shell, "_run", lambda args, stdin=None, echo=None, shell=False: ran.append((args, stdin)) or (0, []))
    assert shell.script("powershell", "Get-Date", echo=lambda s: None) == 0
    assert ran == [(["C:/pwsh/pwsh.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"], "Get-Date")]


def test_powershell_missing_says_so(monkeypatch):
    monkeypatch.setattr(shell, "trusted_cli", lambda name: None)
    with pytest.raises(FileNotFoundError, match="%%powershell needs PowerShell"):
        shell.script("powershell", "Get-Date")


def test_an_unknown_shell_is_refused():
    with pytest.raises(ValueError, match="Unknown shell 'fish'"):
        shell.script("fish", "echo hi")


def _windows(monkeypatch):
    """shell.py's own view of Windows (its module-local os), not the test process's."""
    monkeypatch.setattr(shell, "os", SimpleNamespace(name="nt", fdopen=os.fdopen))


def test_cmd_runs_the_cell_from_a_script_file_that_is_removed_after(monkeypatch):
    _windows(monkeypatch)
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    seen = {}

    def run(args, stdin=None, echo=None, shell=False):
        seen["args"], seen["text"] = args, Path(args[-1]).read_bytes()
        return 0, ["hi"]
    monkeypatch.setattr(shell, "_run", run)
    said = []
    assert shell.script("cmd", "echo hi\nver", echo=said.append) == 0
    assert seen["args"][:3] == [r"C:\Windows\System32\cmd.exe", "/d", "/c"] and seen["args"][3].endswith(".cmd")
    assert seen["text"] == b"@echo off\r\necho hi\r\nver"
    assert not Path(seen["args"][3]).exists() and said == []


def test_cmd_blocked_by_policy_says_what_to_use_instead_and_still_removes_the_file(monkeypatch):
    _windows(monkeypatch)
    used = []

    def run(args, stdin=None, echo=None, shell=False):
        used.append(args[-1])
        return 1, ["This program is blocked by group policy. For more information, contact your system administrator."]
    monkeypatch.setattr(shell, "_run", run)
    said = []
    assert shell.script("cmd", "echo hi", echo=said.append) == 1
    assert said == [shell.CMD_BLOCKED + "\n"] and not Path(used[0]).exists()


def test_reading_output_waits_out_quiet_spells():
    class Quiet:
        """Nothing for the first two waits, then a line."""
        def __init__(self):
            self.waits = []

        def get(self, timeout):
            self.waits.append(timeout)
            if len(self.waits) < 3:
                raise queue.Empty
            return "done\n"
    lines = Quiet()
    assert shell._next(lines) == "done\n"  # type: ignore[arg-type]
    assert lines.waits == [0.1, 0.1, 0.1]  # short waits, so Cancel lands between them


def test_ending_a_command_that_already_ended_does_nothing():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert shell.end(proc) is True


# ---- spark_decimals.py


@pytest.fixture
def conn():
    c = duckdb.connect()
    c.execute("CREATE TEMP VIEW t AS SELECT CAST(10 AS DECIMAL(10,2)) AS a, CAST(3 AS DECIMAL(10,2)) AS b, "
              "CAST(1 AS DECIMAL(38,18)) AS wide, CAST(1 AS DECIMAL(38,0)) AS whole, "
              "CAST(1 AS DECIMAL(38,35)) AS tiny, 'x' AS s")
    return c


def _types(conn, statement):
    return [str(t) for t in conn.sql(sql.duckdb_sql(statement)).types]


def _same(conn, query):
    """spark_decimal_types leaves the statement as it was."""
    statement = sql.parse(query)[0]
    before = sql.duckdb_sql(statement)
    assert sql.duckdb_sql(spark_decimal_types(statement, conn)) == before


@pytest.mark.parametrize("query", [
    "SELECT a * 1e3 AS v FROM t",       # 1e3 is a DOUBLE in Spark
    "SELECT a / tiny AS v FROM t",      # Spark's scale for it can't be reached exactly
    "SELECT AVG(tiny) AS v FROM t",     # the mean's scale would pass 38 digits
])
def test_expressions_whose_spark_type_cant_be_worked_out_keep_duckdbs_types(conn, query):
    duckdb_types = _types(conn, sql.parse(query)[0])
    assert _types(conn, spark_decimal_types(sql.parse(query)[0], conn)) == duckdb_types


def test_a_changed_column_between_two_stars_cant_be_named_so_nothing_changes(conn):
    _same(conn, "SELECT *, a / b, * FROM t")


def test_a_string_operand_has_no_decimal_type_so_nothing_changes(conn):
    _same(conn, "SELECT a * '2' AS v FROM t")


def test_a_statement_duckdb_cant_bind_is_left_as_it_was_and_logged(conn, caplog):
    with caplog.at_level(logging.INFO, logger=spark_decimals.log.name):
        _same(conn, "SELECT a / b FROM t WHERE no_such_column = 1")
    assert "column names unknown" in caplog.text


def test_a_statement_sqlglot_cant_type_is_left_unmarked(conn, monkeypatch, caplog):
    def broken(*a, **k):
        raise sqlglot.errors.OptimizeError("can't qualify")
    monkeypatch.setattr(spark_decimals, "qualify", broken)
    statement = sql.parse("SELECT a / b FROM t")[0]
    with caplog.at_level(logging.INFO, logger=spark_decimals.log.name):
        out = spark_decimal_types(statement, conn)
    assert out is statement and "OptimizeError" in caplog.text
    assert not any(spark_decimals._KEY in n.meta for n in out.walk())


def test_a_union_takes_its_column_names_from_its_first_select(conn):
    statement = spark_decimal_types(sql.parse("SELECT a / b FROM t UNION ALL SELECT b / a FROM t")[0], conn)
    rel = conn.sql(sql.duckdb_sql(statement))
    assert [str(t) for t in rel.types] == ["DECIMAL(23,13)"]
    plain = sql.parse("SELECT a / b FROM t UNION ALL SELECT b / a FROM t")[0]
    assert rel.columns == conn.sql(sql.duckdb_sql(plain)).columns  # named as DuckDB names it


def test_frame_expressions_that_change_nothing_or_dont_parse_as_one_column_stay():
    cols = [("a", "DECIMAL(10,2)"), ("b", "DECIMAL(10,2)")]
    assert spark_decimal_columns(["a"], cols) == [None]                # no arithmetic
    assert spark_decimal_columns(["a, b"], cols) == [None]             # two columns for one expression
    assert spark_decimal_columns(["a / b"], [("a", "DOUBLE")]) == [None]  # no decimal operand


def test_a_frame_column_named_like_the_lambdas_keeps_duckdbs_types(caplog):
    with caplog.at_level(logging.INFO, logger=spark_decimals.log.name):
        assert spark_decimal_columns(["kf_decv / 3"], [("kf_decv", "DECIMAL(10,2)")]) == [None]
    assert "named like the rewrite's lambdas" in caplog.text
