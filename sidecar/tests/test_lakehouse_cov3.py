"""lakehouse.py: DuckDB results as tables and streams, a streamed write in a proof or appended, a token
OneLake keeps refusing, tables a proof can't read, and OPTIMIZE/VACUUM/RESTORE refusals. Local only."""

import datetime as dt

import duckdb
import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import TableNotFoundError

from fabric_desktop import delta_dml, delta_profile, lakehouse, onelake
from fabric_desktop import publish as publish_mod
from fabric_desktop.proof_runtime import ProofTableMissing
from fabric_desktop.publish import WriteTokenExpired


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path / "work",
                                        history_file=tmp_path / "history.jsonl"))
    yield root
    lakehouse.attach(None)


def _in_a_proof(monkeypatch, proof):
    monkeypatch.setattr(lakehouse, "proof", proof)
    monkeypatch.setattr(lakehouse, "proof_exempt_thread", None)


# ---------------------------------------------------------------- what a write is given


def test_a_duckdb_result_is_a_table():
    with duckdb.connect() as con:
        out = lakehouse._to_arrow(con.sql("SELECT 1 AS a UNION ALL SELECT 2"))
    assert isinstance(out, pa.Table) and sorted(out.column("a").to_pylist()) == [1, 2]


def test_a_duckdb_result_without_to_arrow_table_uses_arrow():
    class Relation:  # an older DuckDB relation: .arrow() only
        def arrow(self):
            return pa.table({"a": [3]})
    Relation.__module__ = "duckdb.fake"
    assert lakehouse._to_arrow(Relation()).column("a").to_pylist() == [3]


def test_a_duckdb_batch_reader_is_a_stream():
    reader = pa.table({"id": [1, 2, 3]}).to_reader(max_chunksize=2)

    class Reader:
        def read_next_batch(self):
            return reader.read_next_batch()

        def __arrow_c_stream__(self, requested_schema=None):
            return reader.__arrow_c_stream__(requested_schema)
    Reader.__module__ = "duckdb.fake"
    schema, parts = lakehouse._stream(Reader())
    assert schema.names == ["id"]
    assert [p.column("id").to_pylist() for p in parts] == [[1, 2], [3]]


def test_a_streamed_write_in_a_proof_goes_to_the_proof_as_one_table(lake, monkeypatch):
    _in_a_proof(monkeypatch, object())
    seen = []
    monkeypatch.setattr(lakehouse, "_write_proof",
                        lambda p, arrow, ref, mode, schema_mode, predicate: seen.append(arrow) or "proof result")
    parts = iter([pa.table({"id": [1]}), pa.table({"id": [2, 3]})])
    assert lakehouse.write_table(parts, "dbo.t") == "proof result"
    assert seen[0].column("id").to_pylist() == [1, 2, 3]
    assert not (lake / "dbo" / "t").exists()  # the shared table isn't touched


def test_a_streamed_append_is_one_commit(lake):
    lakehouse.write_table(pa.table({"id": [0]}), "dbo.s", upload="direct")
    parts = iter([pa.table({"id": [1]}), pa.table({"id": [2]})])
    out = lakehouse.write_table(parts, "dbo.s", mode="append", skip_unchanged=True, upload="direct")
    table = DeltaTable(str(lake / "dbo" / "s"))
    assert out.rows == 2 and table.version() == 1
    assert sorted(table.to_pyarrow_table().column("id").to_pylist()) == [0, 1, 2]


def test_a_token_onelake_keeps_refusing_fails_the_write_after_the_last_try(lake, monkeypatch):
    tries = []

    def refused(*a, **kw):
        tries.append(1)
        raise WriteTokenExpired("token refused")
    monkeypatch.setattr(lakehouse, "publish", refused)
    with pytest.raises(WriteTokenExpired):
        lakehouse.write_table(pa.table({"id": [1]}), "dbo.t", upload="direct")
    assert len(tries) == lakehouse.UPLOAD_ATTEMPTS


# ---------------------------------------------------------------- proofs


class _Proof:
    def __init__(self, folder):
        self.folder, self.opened = folder, []

    def options(self):
        return {}

    def read_location(self, schema, table):
        raise OSError(f"{schema}.{table} isn't in the proof")

    def open(self, schema, table, opener):
        self.opened.append(f"{schema}.{table}")
        raise ProofTableMissing(f"{schema}.{table} isn't pinned")

    def write_location(self, schema, table, mode, seed, **kw):
        return (self.folder / schema / table).as_posix()


def test_a_change_to_a_table_the_proof_cant_read_says_it_isnt_there(lake, monkeypatch, tmp_path):
    _in_a_proof(monkeypatch, _Proof(tmp_path / "proof"))
    with pytest.raises(TableNotFoundError, match="dbo.t isn't in the proof"):
        lakehouse.change_table(delta_dml.parse("DELETE FROM dbo.t WHERE id = 1"))


def test_a_partition_overwrite_of_a_table_not_in_the_proof_starts_unpartitioned(lake, tmp_path, monkeypatch):
    proof = _Proof(tmp_path / "proof")
    _in_a_proof(monkeypatch, proof)
    ref = lakehouse.table_ref("dbo.p")
    out = lakehouse._write_proof(proof, pa.table({"id": [1], "day": ["mon"]}), ref, "overwrite", predicate="day = 'mon'")
    assert proof.opened == ["dbo.p"]
    assert out.rows == 1
    written = DeltaTable((tmp_path / "proof" / "dbo" / "p").as_posix())
    assert written.metadata().partition_columns == [] and written.to_pyarrow_table().num_rows == 1


# ---------------------------------------------------------------- OPTIMIZE, VACUUM and RESTORE


@pytest.fixture
def allowed(monkeypatch):
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, table: None)


def _two_versions(lake):
    uri = str(lake / "dbo" / "m")
    write_deltalake(uri, pa.table({"id": [1]}))
    write_deltalake(uri, pa.table({"id": [2]}), mode="append")
    return uri


def test_an_unknown_maintenance_operation_is_refused(lake):
    with pytest.raises(ValueError, match="operation is one of"):
        lakehouse.maintain_table("dbo.m", "shrink")


def test_a_table_outside_the_profile_isnt_maintained(lake, allowed, monkeypatch):
    _two_versions(lake)

    def outside(table, *a, **kw):
        raise delta_profile.ProfileViolation("it uses deletion vectors")
    monkeypatch.setattr(delta_profile, "check_table", outside)
    with pytest.raises(ValueError, match="dbo.m isn't changed on this PC: it uses deletion vectors"):
        lakehouse.maintain_table("dbo.m", "optimize")


def test_restore_needs_a_point_and_goes_to_a_time(lake, allowed):
    uri = _two_versions(lake)
    with pytest.raises(ValueError, match="RESTORE needs TO VERSION AS OF n or TO TIMESTAMP AS OF"):
        lakehouse.maintain_table("dbo.m", "restore")
    first = DeltaTable(uri).history()[-1]["timestamp"]
    at = dt.datetime.fromtimestamp(first / 1000, dt.timezone.utc)
    out = lakehouse.maintain_table("dbo.m", "restore", timestamp=at)
    assert out.delta_version == 2
    assert DeltaTable(uri).to_pyarrow_table().column("id").to_pylist() == [1]


def test_a_restore_delta_refuses_changes_nothing(lake, allowed, monkeypatch):
    uri = _two_versions(lake)

    def vacuumed(self, target, **kw):
        raise OSError("files for version 0 were removed by VACUUM")
    monkeypatch.setattr(DeltaTable, "restore", vacuumed)
    at = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    with pytest.raises(ValueError, match="can't be restored to 2026-01-01.*removed by VACUUM.*Nothing was changed"):
        lakehouse.maintain_table("dbo.m", "restore", timestamp=at)
    assert DeltaTable(uri).version() == 1
