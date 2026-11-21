"""Publishing a job's result as a Delta table (publish.py), the paths the main tests don't reach: a
lakehouse the job's target names but that isn't there yet, schema modes that can't be used, the
compression setting, an overwrite of the same rows that writes nothing, a log whose files can't be
listed, the lease for a OneLake table, and a whole job run (or refused) end to end. Tables are written
to tmp_path; nothing reaches OneLake."""

from __future__ import annotations

import json

import pyarrow as pa
import pytest
from deltalake import DeltaTable

from fabric_desktop import publish as P
from fabric_desktop.jobs import Job, Target
from fabric_desktop.onelake import TableRef


class Ref:
    """A table on disk, named like a lakehouse table (publish only needs its address and name)."""

    def __init__(self, path):
        self.uri, self.schema, self.table = str(path), "dbo", "orders"


class Fabric:
    def __init__(self, lakehouses=()):
        self.lakehouses = list(lakehouses)
        self.created = []

    def find_lakehouse(self, workspace_id, name):
        return next((lh for lh in self.lakehouses if lh["displayName"] == name), None)

    def create_lakehouse(self, workspace_id, name):
        self.created.append(name)
        return {"id": "lh-new", "displayName": name}


def job(tmp_path, sql="SELECT 1 AS id, 'a' AS name"):
    return Job("orders", "duckdb", tmp_path, Target("ws1", "LH_Sales", "orders"), sql=sql)


# ---- the target


def test_a_lakehouse_that_isnt_there_yet_is_created():
    fabric = Fabric()
    ref = P.resolve_target(job(None), fabric)
    assert fabric.created == ["LH_Sales"]
    assert ref == TableRef("ws1", "lh-new", "dbo", "orders")
    fabric = Fabric([{"id": "lh1", "displayName": "LH_Sales"}])
    assert P.resolve_target(job(None), fabric).lakehouse_id == "lh1" and fabric.created == []


@pytest.mark.parametrize("mode, schema_mode, needle", [
    ("append", "replace", "schema_mode must be one of"),
    ("append", "overwrite", "can only be replaced by an overwrite"),
])
def test_schema_modes_that_cant_be_used_are_refused(mode, schema_mode, needle):
    with pytest.raises(ValueError, match=needle):
        P.resolve_schema_mode(mode, schema_mode)


# ---- how the files are written


def test_snappy_is_used_when_the_setting_says_so_and_zstd_when_settings_cant_be_read(monkeypatch):
    from fabric_desktop import app_settings
    monkeypatch.setattr(app_settings, "value", lambda key: "Snappy")
    assert P.writer_properties().compression == "SNAPPY"

    def broken(key):
        raise ValueError("settings file isn't JSON")

    monkeypatch.setattr(app_settings, "value", broken)
    assert P.writer_properties().compression == "ZSTD(3)"


def test_an_overwrite_of_the_same_rows_writes_nothing(tmp_path):
    ref = Ref(tmp_path / "orders")
    data = pa.table({"id": [1, 2]})
    first = P.publish(data, ref, "overwrite", "token", rows="fp1", skip_unchanged=True)
    assert first.skipped is False and first.version() == 0
    again = P.publish(data, ref, "overwrite", "token", rows="fp1", skip_unchanged=True)
    assert again.skipped is True and DeltaTable(ref.uri).version() == 0
    other = P.publish(data, ref, "overwrite", "token", rows="fp2", skip_unchanged=True)
    assert other.skipped is False and other.version() == 1


def test_a_log_whose_files_cant_be_listed_leaves_the_byte_count_out(tmp_path, monkeypatch):
    said = []
    monkeypatch.setattr(P._log, "info", lambda msg, *args: said.append(msg % args))

    class Broken:
        table_uri = "file:///orders"

        def get_add_actions(self, flatten=True):
            raise OSError("log unreadable")

    assert P._files(Broken()) is None
    assert said == ["Couldn't list the files of file:///orders: OSError: log unreadable"]


def test_a_onelake_table_takes_its_onelake_lease(monkeypatch):
    seen = []
    monkeypatch.setattr(P, "onelake_table_lease",
                        lambda ws, lh, schema, table, token, owner: seen.append((ws, lh, schema, table, token(), owner)))
    P._lease(TableRef("ws1", "lh1", "dbo", "orders"), lambda: "tok")
    assert seen == [("ws1", "lh1", "dbo", "orders", "tok", "KratosForge")]


# ---- a whole job


def test_a_job_the_guard_refuses_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "run_local", lambda j: pytest.fail("the job ran"))
    with pytest.raises(PermissionError, match="Prod is read only"):
        P.run_job(job(tmp_path), Fabric(), lambda: "tok", tmp_path / "work",
                  guard=lambda ws, lh, schema, table: "Prod is read only")


def test_a_job_runs_writes_its_table_and_keeps_a_record(tmp_path, monkeypatch):
    ref = Ref(tmp_path / "lake" / "orders")
    monkeypatch.setattr(P, "resolve_target", lambda j, fabric: ref)
    history = tmp_path / "runs.jsonl"
    record = P.run_job(job(tmp_path), Fabric(), lambda: "tok", tmp_path / "work", history_file=history,
                       guard=lambda *a: None)
    assert (record.job, record.table_uri, record.mode, record.rows, record.delta_version) == (
        "orders", ref.uri, "overwrite", 1, 0)
    assert DeltaTable(ref.uri).to_pyarrow_table().to_pylist() == [{"id": 1, "name": "a"}]
    lines = [json.loads(line) for line in history.read_text().splitlines() if line.strip()]
    assert lines[-1]["job"] == "orders" and lines[-1]["rows"] == 1
    assert not any((tmp_path / "work" / "staging").glob("orders*"))  # the staged copy is gone


def test_a_job_whose_token_expired_is_written_again_with_a_fresh_one(tmp_path, monkeypatch):
    """HD-034: nothing was committed, so the job's write goes once more, asking for a new token."""
    ref = Ref(tmp_path / "lake" / "orders")
    monkeypatch.setattr(P, "resolve_target", lambda j, fabric: ref)
    real, tokens = P.publish, []

    def expires_once(data, ref_, mode, token, *a, **k):
        tokens.append(token())
        if len(tokens) == 1:
            raise P.WriteTokenExpired("Writing dbo.orders: refused. Nothing was committed.")
        return real(data, ref_, mode, token, *a, **k)
    monkeypatch.setattr(P, "publish", expires_once)
    fresh = iter(f"tok{n}" for n in range(100))
    record = P.run_job(job(tmp_path), Fabric(), lambda: next(fresh), tmp_path / "work", guard=lambda *a: None)
    assert tokens == ["tok0", "tok1"] and record.delta_version == 0  # the token function, not one token


def test_a_job_whose_token_expired_twice_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "resolve_target", lambda j, fabric: Ref(tmp_path / "lake" / "orders"))
    tries = []

    def expires(*a, **k):
        tries.append(1)
        raise P.WriteTokenExpired("Writing dbo.orders: refused. Nothing was committed.")
    monkeypatch.setattr(P, "publish", expires)
    with pytest.raises(P.WriteTokenExpired):
        P.run_job(job(tmp_path), Fabric(), lambda: "tok", tmp_path / "work", guard=lambda *a: None)
    assert len(tries) == 2
