"""SQL page run edges (sql_exec.py) the run tests don't reach: closing the app while a worker won't
die or won't end in time, EXPORT/IMPORT DATABASE found past strings and comments, duplicate column
names, and an export with no free name or whose partial file can't be removed. No worker is started."""

import subprocess
import threading
import types
from pathlib import Path

import pyarrow as pa
import pytest
from sqlglot import exp

from fabric_desktop import sql_exec as se


class Proc:
    def __init__(self, kill_error=None, wait_error=None):
        self.killed, self.waited = 0, []
        self.kill_error, self.wait_error = kill_error, wait_error

    def poll(self):
        return None

    def kill(self):
        self.killed += 1
        if self.kill_error:
            raise self.kill_error

    def wait(self, timeout=None):
        self.waited.append(timeout)
        if self.wait_error:
            raise self.wait_error
        return 0


def _manager(tmp_path):
    return se.SqlExecutions(tmp_path / "data", token=lambda: "", limits=lambda: {})


def test_closing_the_app_ends_running_workers_even_when_one_wont_die_or_wait(tmp_path):
    m = _manager(tmp_path)
    stubborn = Proc(kill_error=OSError("access denied"), wait_error=subprocess.TimeoutExpired("worker", 10))
    plain = Proc()
    m._runs = {"a": types.SimpleNamespace(state="running", stop_reason="", target="lakehouse", proc=stubborn),
               "b": types.SimpleNamespace(state="cancelling", stop_reason="timeout", target="warehouse", proc=plain),
               "c": types.SimpleNamespace(state="running", stop_reason="", target="lakehouse", proc=None),
               "d": types.SimpleNamespace(state="completed", stop_reason="", target="lakehouse", proc=Proc())}
    cancel = threading.Event()
    m._exports = {"e": types.SimpleNamespace(cancel=cancel)}
    m.shutdown()
    assert stubborn.killed == 1 and plain.killed == 1 and m._runs["d"].proc.killed == 0
    assert stubborn.waited == [10] and plain.waited == [10]
    assert m._runs["a"].stop_reason == "cancel" and m._runs["b"].stop_reason == "timeout"
    assert cancel.is_set()


def test_an_e_string_with_no_closing_quote_runs_to_the_end():
    assert se._escape_string_end(r"abc\'def", 0) == len(r"abc\'def")
    assert se._escape_string_end(r"a\\'b", 0) == 4


@pytest.mark.parametrize("query, word", [
    ("export database 'x'", "EXPORT"),
    ("-- note\n/* block */ IMPORT  DATABASE 'x'", "IMPORT"),
    ("SELECT 1; export database 'x'", "EXPORT"),
    ("SELECT E'it\\'s; EXPORT DATABASE' AS t", ""),
    ("SELECT 'a; EXPORT DATABASE' AS t", ""),
    ('SELECT "a; EXPORT DATABASE" AS t', ""),
    ("SELECT $$; EXPORT DATABASE 'x'$$", ""),
    ("SELECT $tag$; EXPORT DATABASE$tag$", ""),
    ("SELECT x$y$; 1", ""),
    ("SELECT 1 -- ; EXPORT DATABASE 'x'", ""),
    ("SELECT 1 /* ; EXPORT DATABASE 'x' */", ""),
    ("SELECT 'never closed; EXPORT DATABASE", ""),
    ("SELECT 1 /* never closed", ""),
    ("-- only a comment", ""),
    ("/* only a comment", ""),
    ("SELECT name'x'", ""),
])
def test_export_and_import_database_are_found_only_where_a_statement_starts(query, word):
    assert se._outside_command(query) == word


def test_export_database_is_classed_as_a_command_and_other_parse_errors_stay_errors(monkeypatch):
    def unreadable(query, dialect):
        raise ValueError("can't parse")
    monkeypatch.setattr(se.sql, "parse", unreadable)
    statements = se._policy_statements("EXPORT DATABASE 'out'", "duckdb")
    assert len(statements) == 1 and isinstance(statements[0], exp.Command) and statements[0].this == "EXPORT"
    with pytest.raises(ValueError, match="can't parse"):
        se._policy_statements("SELEC 1", "duckdb")


def test_a_repeated_column_name_gets_a_number_not_already_taken():
    schema = pa.schema([("a", pa.int64()), ("A", pa.int64()), ("a_2", pa.int64()), ("b", pa.string())],
                       metadata={b"k": b"v"})
    out = se._unique_names(schema)
    assert out.names == ["a", "A_3", "a_2", "b"]
    assert out.metadata == {b"k": b"v"}


def test_no_free_name_for_an_export_is_said(tmp_path, monkeypatch):
    real_open = open

    def taken(path, mode="r", *a, **k):
        if mode == "x":
            raise FileExistsError(path)
        return real_open(path, mode, *a, **k)
    monkeypatch.setattr("builtins.open", taken)
    with pytest.raises(ValueError, match="free file name in Downloads for the export"):
        se._reserve(tmp_path, "result", ".csv")


def test_a_stopped_export_leaves_a_reserved_name_that_already_has_data(tmp_path):
    partial, reserved = tmp_path / "out.csv.partial", tmp_path / "out.csv"
    reserved.write_text("someone else's data", encoding="utf-8")
    se._discard(partial, reserved)  # no partial file: nothing to remove
    assert reserved.read_text(encoding="utf-8") == "someone else's data"
    empty = tmp_path / "empty.csv"
    empty.touch()
    se._discard(partial, empty)
    assert not empty.exists()


def test_a_reserved_name_that_cant_be_removed_is_left_alone(tmp_path, monkeypatch):
    reserved = tmp_path / "out.csv"
    reserved.touch()

    def locked(self, *a, **k):
        raise PermissionError("locked by another program")
    monkeypatch.setattr(Path, "unlink", locked)
    se._discard(tmp_path / "gone.partial", reserved)  # neither removal raises
    monkeypatch.undo()
    assert reserved.exists()
