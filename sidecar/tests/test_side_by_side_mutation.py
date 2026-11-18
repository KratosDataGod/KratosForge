"""Mutation batch 90: side_by_side.py, comparing what a notebook wrote on this PC and in Fabric. Pins
what the earlier tests let through: how each column type enters the checksum and the checksum's
range, the type names a person sees, the note on a table one side appended to, the verdict when a
run failed, which side is called faster on a tie, and a %%configure cell asking for no cores."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pytest

from fabric_desktop import side_by_side as sbs


@pytest.fixture(autouse=True)
def _own_home(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


# ---- the checksum


def test_an_integer_column_is_summed_as_an_integer_unless_named_as_a_number():
    assert sbs._normalized("x", pa.int64()) == 'CAST("x" AS HUGEINT)'
    assert sbs._normalized("x", pa.int64(), as_number=True).startswith('CASE WHEN CAST("x" AS DOUBLE) IS NULL')


def test_integers_named_as_numbers_match_the_same_values_as_decimals():
    ints = pa.table({"N": pa.array([1, 2, 300], pa.int64())})
    doubles = pa.table({"N": pa.array([1.0, 2.0, 300.0], pa.float64())})
    assert sbs.fingerprint(ints, frozenset({"n"}))["checksum"] == sbs.fingerprint(doubles)["checksum"]
    assert sbs.fingerprint(ints)["checksum"] != sbs.fingerprint(doubles)["checksum"]


def test_a_date_and_the_same_date_as_text_give_the_same_checksum():
    days = [dt.date(2026, 11, 1), dt.date(2026, 11, 2)]
    as_dates = sbs.fingerprint(pa.table({"day": days}))
    as_text = sbs.fingerprint(pa.table({"day": [d.isoformat() for d in days]}))
    assert as_dates["checksum"] == as_text["checksum"]
    large = sbs.fingerprint(pa.table({"day": pa.array([d.isoformat() for d in days], pa.large_string())}))
    assert large["checksum"] == as_text["checksum"]


def test_the_checksum_is_the_sum_of_the_row_hashes_kept_to_64_bits():
    import duckdb
    t = pa.table({"k": list(range(200)), "s": [f"row {i}" for i in range(200)]})
    con = duckdb.connect()
    try:
        con.register("t", t)
        total = con.execute('SELECT sum(CAST(hash(CAST("k" AS HUGEINT), "s") AS HUGEINT)) FROM t').fetchone()[0]
    finally:
        con.close()
    assert total >= 2 ** 64  # enough rows that the sum needs reducing
    out = sbs.fingerprint(t)
    assert out["checksum"] == int(total) % 2 ** 64
    assert sbs._MOD == 2 ** 64


# ---- the types a person sees


@pytest.mark.parametrize("typ, name", [
    (pa.string(), "string"), (pa.large_string(), "string"), (pa.timestamp("us"), "timestamp"),
    (pa.timestamp("ms", tz="UTC"), "timestamp"), (pa.int32(), "integer"), (pa.decimal128(10, 2), "number"),
])
def test_each_type_is_named_as_a_person_compares_it(typ, name):
    assert sbs.kind(typ) == name


# ---- the comparison


class MemoryTables(sbs.Tables):
    def __init__(self, data, history):
        self.data, self._history = data, history

    def history(self, table):
        return self._history.get(table, [])

    def dataset(self, table, version):
        return self.data[table][version]


def test_a_table_one_side_appended_to_is_noted_as_appended():
    history = {"dbo.t": [{"version": 1, "operation": "WRITE", "operationParameters": {"mode": "Append"}},
                         {"version": 2, "operation": "WRITE", "operationParameters": {"mode": "Overwrite"}}]}
    data = {"dbo.t": {0: pa.table({"n": [1]}), 1: pa.table({"n": [1, 2]}), 2: pa.table({"n": [2]})}}
    [row] = sbs.compare(MemoryTables(data, history), {"dbo.t": 0}, {"dbo.t": 1}, {"dbo.t": 2})
    assert (row["local"]["mode"], row["fabric"]["mode"]) == ("append", "overwrite")
    assert row["note"] == "Appended: compared the rows each run added."
    assert row["result"] == "match"  # the row this PC added is the table Fabric wrote


@pytest.mark.parametrize("local, fabric, failed", [("ok", "failed", "Fabric"), ("error", "ok", "this PC")])
def test_a_failed_run_means_the_results_are_not_the_same(local, fabric, failed):
    assert sbs.verdict([], local, fabric) == {
        "same": False, "text": f"The run failed on {failed}, so the results can't be the same."}


def test_a_tie_is_called_for_this_pc():
    out = sbs.speed(10.0, 10.0, 10.0)
    assert out["end_to_end"] == "This PC was 1.0x faster end to end"
    assert out["run"] == "This PC was 1.0x faster on run time alone, leaving out Fabric's session start"


def test_processing_is_only_compared_when_it_is_shorter_than_the_whole_run():
    assert sbs.speed(None, 10.0, 5.0, local_processing=0.5)["processing"] == ""
    assert sbs.speed(2.0, 10.0, 5.0, local_processing=0.5)["processing"].startswith("This PC was 10.0x faster")


# ---- cores


def test_a_configure_cell_asking_for_no_cores_leaves_the_python_default():
    cells = [{"source": '%%configure\n{"vCores": 0}'}]
    assert sbs.fabric_vcores(cells, "python") == sbs.PYTHON_VCORES == 2
    assert sbs.fabric_vcores([{"source": '%%configure\n{"vCores": 8}'}], "python") == 8
