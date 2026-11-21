"""Mutation batch 41: engines/compare.py. Pins what the earlier tests let through: the default row
limit, which side's error is quoted, the exact refusal text, and that a remote engine that can't
return a result is refused as unsupported, not as a missing copy confirm."""

from __future__ import annotations

import pyarrow as pa
import pytest

from fabric_desktop.engines import compare as cmp
from fabric_desktop.engines import registry
from fabric_desktop.engines.capabilities import EngineDescriptor, Support
from fabric_desktop.engines.contract import CellRequest, CellResult, Unsupported

TABLE = pa.table({"id": [1, 2]})


def sql(code="SELECT 1"):
    return CellRequest(code, "sql")


class Scripted:
    """A session that returns the results it is given, in order, and records each request."""

    def __init__(self, engine, *results):
        self.engine, self.notebook, self.ran, self._results = engine, "nb", [], list(results)

    def execute(self, request, on_output=None):
        self.ran.append(request)
        return self._results.pop(0)


def _remote(monkeypatch, *, result: bool, note: str = ""):
    caps = {"execute": Support("native", evidence="unit_tests")}
    caps["result"] = (Support("native", evidence="unit_tests") if result
                      else Support("unsupported", note=note))
    engine = EngineDescriptor("fake-remote", "Fake warehouse (remote)", "remote", "databricks", "databricks_dbu",
                              adapter=True, preview=True, capabilities=caps)
    monkeypatch.setitem(registry.ENGINES, engine.id, engine)
    return engine


def test_the_default_row_limit_is_one_million():
    assert cmp.DEFAULT_ROWS == 1_000_000
    left = Scripted("local-duckdb", CellResult("error", [], 0.0, None, "local-duckdb"))
    cmp.compare(left, Scripted("local-sqlite"), sql())
    assert left.ran[0].result_rows == 1_000_000


def test_one_row_is_a_valid_limit():
    left = Scripted("local-duckdb", CellResult("ok", [], 0.0, 1, "local-duckdb", table=pa.table({"id": [1]})))
    right = Scripted("local-sqlite", CellResult("ok", [], 0.0, 1, "local-sqlite", table=pa.table({"id": [1]})))
    report = cmp.compare(left, right, sql(), rows=1)
    assert report["result"] == "match"
    assert left.ran[0].result_rows == right.ran[0].result_rows == 1


def test_seconds_are_rounded_to_four_places():
    left = Scripted("local-duckdb", CellResult("ok", [], 1.234567, 2, "local-duckdb", table=TABLE))
    right = Scripted("local-sqlite", CellResult("ok", [], 0.0, 2, "local-sqlite", table=TABLE))
    report = cmp.compare(left, right, sql())
    assert report["left"]["seconds"] == 1.2346


def test_an_ok_left_side_with_no_table_doesnt_run_the_right():
    left = Scripted("local-duckdb", CellResult("ok", [], 0.0, 0, "local-duckdb"))
    right = Scripted("local-sqlite", CellResult("ok", [], 0.0, 2, "local-sqlite", table=TABLE))
    report = cmp.compare(left, right, sql())
    assert right.ran == [] and report["right"]["status"] == "not_run"
    assert report["problem"].startswith("The left engine (") and report["problem"].endswith(
        ") returned no rows to compare.")
    assert "didn't give an answer" not in report["problem"]


def test_a_failed_side_quotes_its_last_error():
    outputs = [{"type": "error", "ename": "First", "evalue": "one"},
               {"type": "stream", "text": "x"},
               {"type": "error", "ename": "Last", "evalue": "two"}]
    left = Scripted("local-duckdb", CellResult("error", outputs, 0.0, None, "local-duckdb"))
    report = cmp.compare(left, Scripted("local-sqlite"), sql())
    assert report["left"]["error"] == "Last: two"
    assert report["problem"].endswith("didn't give an answer. Last: two")
    assert "returned no rows" not in report["problem"]


def test_an_error_without_a_name_or_value_says_error():
    left = Scripted("local-duckdb", CellResult("error", [{"type": "error"}], 0.0, None, "local-duckdb"))
    report = cmp.compare(left, Scripted("local-sqlite"), sql())
    assert report["left"]["error"] == "Error: "


def test_the_refusal_quotes_the_engines_note_only_when_it_has_one():
    polars = registry.get("local-polars")
    assert polars.capability("result").note
    assert cmp.refusal(polars, False) == (f"{polars.label} can't return a whole result to compare. "
                                          f"{polars.capability('result').note}")


def test_the_refusal_has_no_trailing_text_without_a_note(monkeypatch):
    engine = _remote(monkeypatch, result=False, note="")
    assert cmp.refusal(engine, True) == "Fake warehouse (remote) can't return a whole result to compare."


def test_a_remote_engine_without_result_is_unsupported_not_a_missing_confirm(monkeypatch):
    _remote(monkeypatch, result=False, note="No result here.")
    left, right = Scripted("local-duckdb"), Scripted("fake-remote")
    with pytest.raises(Unsupported, match="can't return a whole result") as info:
        cmp.compare(left, right, sql())
    assert not isinstance(info.value, cmp.CopyNotConfirmed)
    assert left.ran == [] and right.ran == []
