"""PB-015 criterion 1: MERGE, UPDATE and DELETE on this PC give Apache Spark 3.5 and Delta 3.2's answers.

tests/fixtures/spark_3_5_3_delta_3_2_dml.json holds each case's Delta tables, a notebook cell that changes
one of them (spark.sql or the DeltaTable API), and what real Spark with Delta answered: the table's rows
afterwards and the counts the cell returned, or the error it raised. tools/capture_spark_dml.py makes it,
by hand, since CI has no Spark. Each cell runs here through the notebook kernel, where delta-rs makes the
change, and the Delta table on disk must hold Spark's rows. Where Spark refused, this PC must refuse too,
with its own named reason (REASONS), and leave the table as it was (same version, same rows).

KNOWN names the cases this PC answers differently from Spark on purpose (refused by name) or by a bug
(xfail, strict, so a fix shows). A case listed in KNOWN that no longer differs fails.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, onelake
from fabric_desktop import publish as publish_mod
from fabric_desktop.notebook import Kernel

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "spark_3_5_3_delta_3_2_dml.json"
_ARROW = {"int": pa.int32(), "bigint": pa.int64(), "string": pa.string(), "double": pa.float64(), "boolean": pa.bool_()}

# Where Spark refuses the cell, the reason this PC gives (a regular expression on the error's text).
REASONS = {
    "merge_two_source_rows_one_target_row": r"\[DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE\]",
    "merge_two_source_rows_conditional_delete": r"\[DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE\]",
    "api_merge_duplicate_source_rows": r"\[DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE\]",
    "merge_duplicates_a_condition_leaves_out": r"\[DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE\]",
    "merge_duplicates_by_source_only": r"\[DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE\]",
    "merge_duplicates_on_an_expression": r"\[DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE\]",
    "update_set_star": r"\* isn't column = value\.",
    "merge_insert_star_missing_source_column": r"UPDATE SET \* and INSERT \* take every column of sales from the "
                                               r"source, and the source has no name, amount",
    "merge_target_missing": r"Table nope doesn't exist, so MERGE has nothing to change",
    "merge_not_matched_then_delete": r"WHEN NOT MATCHED can only INSERT: there is no row in the table to delete",
    "merge_by_source_update_star": r"UPDATE SET \* goes with WHEN MATCHED, where there is a source row to copy",
    "merge_by_source_insert": r"INSERT goes with WHEN NOT MATCHED, where the source row has no row in the table",
    "update_where_subquery": r"has a subquery, which delta-rs can't run inside a condition or value",
    "delete_where_subquery_in": r"has a subquery, which delta-rs can't run inside a condition or value",
    "update_from": r"UPDATE \.\.\. FROM isn't supported on a Delta table\. Use MERGE INTO sales",
    "delete_using": r"DELETE \.\.\. USING isn't supported on a Delta table\. Use MERGE INTO sales",
    "update_missing_column": r"sales has no column price",
    "merge_without_when": r"MERGE INTO needs at least one WHEN clause",
}

# Spark answers, and this PC refuses by name on purpose: (reason, why).
REFUSED = {
    "merge_value_changes_to_fit_the_column": (
        r"This MERGE would change values in n to fit its type in counts",
        "EA-001: Spark's store assignment casts 2.75 to 2 quietly; this PC refuses rather than lose the fraction"),
}

# Spark refuses, and this PC runs the cell or stops without a named reason: a bug, xfail(strict=True) until
# fixed. When it is, move the case to REASONS.
BUGS = {
    "api_merge_default_alias": (
        "PB-015 bug: DeltaTable.merge without .alias() lets the condition call the source `source` on this PC; "
        "delta-spark 3.2 refuses it with DELTA_MERGE_UNRESOLVED_EXPRESSION (no alias is given), so the cell "
        "runs here and fails in Fabric"),
}


def load() -> list[dict]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["spark"].startswith("3.5") and data["delta"].startswith("3.2") and data["ansi"] is False
    return data["cases"]


CASES = load()


def _params():
    for case in CASES:
        marks = [pytest.mark.xfail(strict=True, reason=BUGS[case["id"]])] if case["id"] in BUGS else []
        yield pytest.param(case, id=case["id"], marks=marks)


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "_setting", lambda key, default: default)
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    yield root
    lakehouse.attach(None)


def make(root: Path, tables: dict) -> None:
    for name, spec in tables.items():
        schema = pa.schema([pa.field(n, _ARROW[k]) for n, k in spec["columns"]])
        data = pa.table({n: pa.array([r[i] for r in spec["rows"]], _ARROW[k])
                         for i, (n, k) in enumerate(spec["columns"])}, schema=schema)
        write_deltalake(str(root / "dbo" / name), data, partition_by=spec.get("partition_by") or None)


def ordered(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))


def state(root: Path, name: str) -> tuple[int, list[dict]]:
    table = DeltaTable(str(root / "dbo" / name))
    return table.version(), ordered(table.to_pyarrow_table().to_pylist())


def run(code: str) -> tuple[str, str, dict | None]:
    k = Kernel()
    out = k.run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    if out["status"] != "ok":
        return out["status"], text, None
    got = k.run("import json as _j\nprint(_j.dumps(result.collect()[0].asDict() if result is not None else None))")
    assert got["status"] == "ok", got
    return "ok", text, json.loads(got["outputs"][-1]["text"])


def test_the_fixture_covers_criterion_1():
    ids = {c["id"] for c in CASES}
    assert len(CASES) >= 50
    for prefix in ("merge_", "update_", "delete_", "api_merge", "api_update", "api_delete"):
        assert any(i.startswith(prefix) for i in ids), prefix
    errors = {c["id"] for c in CASES if "error" in c["spark"]}
    assert errors == set(REASONS) | set(BUGS), "each case Spark refuses names this PC's reason"
    assert not (set(REFUSED) & errors)
    assert set(REFUSED) | set(BUGS) <= ids


@pytest.mark.parametrize("case", list(_params()))
def test_the_change_gives_sparks_answer(lake, case):
    make(lake, case["tables"])
    before = state(lake, case["check"])
    status, text, result = run(case["code"])
    spark = case["spark"]
    if "error" in spark:
        assert status == "error", f"Spark refused this ({spark['message']}), and it ran here"
        assert re.search(REASONS.get(case["id"], r"MERGE INTO needs at least one WHEN clause"), text), text
        assert state(lake, case["check"]) == before, "a refused change left the table as it was"
        assert ordered(spark["rows"]) == before[1], "Spark left its table as it was too"
        return
    if case["id"] in REFUSED:
        assert status == "error" and re.search(REFUSED[case["id"]][0], text), text
        assert state(lake, case["check"]) == before
        return
    assert status == "ok", text
    version, rows = state(lake, case["check"])
    assert rows == ordered(spark["rows"])
    if rows != before[1]:
        assert version == before[0] + 1, "a change is one commit"
    if spark["result"] is not None:  # delta-spark 3.2's DeltaTable.merge().execute() returns None
        assert result == spark["result"]
