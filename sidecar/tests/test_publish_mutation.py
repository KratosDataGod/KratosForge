"""publish.py: behavior that mutation testing (docs/quality/mutation-testing.md, batch 2) showed no test
pinned. Each staging folder name stage_locally refuses on its own, a staging folder that a link points
outside the staging root, the table it returns, schema modes that are allowed, an overwrite of the same
rows being written again unless asked not to, the row fingerprint kept only on an overwrite, a byte count
left out when the files before the write couldn't be listed, and the type names in a refusal. Tables are
written to tmp_path; nothing reaches OneLake."""

from __future__ import annotations

import os

import pyarrow as pa
import pytest
from deltalake import DeltaTable

from fabric_desktop import publish as P
from fabric_desktop.frames import type_name


class Ref:
    """A table on disk, named like a lakehouse table (publish only needs its address and name)."""

    def __init__(self, path):
        self.uri, self.schema, self.table = str(path), "dbo", "orders"


@pytest.mark.parametrize("part", ["", ".", "..", "_writes", "a/b", "a\\b", "c:"])
def test_each_name_a_staging_folder_cant_have_is_refused_on_its_own(tmp_path, part):
    with pytest.raises(ValueError, match="can't be a staging folder name"):
        P.stage_locally(pa.table({"id": [1]}), tmp_path / "staging", "lh", part)
    assert not (tmp_path / "staging").exists() or not any((tmp_path / "staging").rglob("*.json"))


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need extra rights on Windows")
def test_a_staging_folder_a_link_points_outside_the_staging_root_is_refused(tmp_path):
    root = tmp_path / "staging"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (root / "lh").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(ValueError, match="isn't inside the staging folder"):
        P.stage_locally(pa.table({"id": [1]}), root, "lh", "orders")


def test_the_staged_table_is_returned_loaded_with_the_datas_schema(tmp_path):
    data = pa.table({"id": pa.array([1], pa.int64()), "name": ["a"]})
    table = P.stage_locally(data, tmp_path / "staging", "lh", "orders")
    assert isinstance(table, DeltaTable)
    assert [f.name for f in table.schema().fields] == ["id", "name"]


@pytest.mark.parametrize("mode, schema_mode, how", [
    ("append", "merge", "merge"),
    ("overwrite", "merge", "merge"),
    ("overwrite", "overwrite", "overwrite"),
    ("overwrite", None, None),
    ("append", "auto", None),
    ("overwrite", "auto", "overwrite"),
])
def test_the_schema_modes_that_are_allowed(mode, schema_mode, how):
    assert P.resolve_schema_mode(mode, schema_mode) == how


def test_no_table_or_no_fingerprint_is_never_unchanged(tmp_path):
    assert P.unchanged(None, "fp") is False
    P.publish(pa.table({"id": [1]}), Ref(tmp_path / "t"), "overwrite", "token", rows="fp")
    assert P.unchanged(DeltaTable(str(tmp_path / "t")), None) is False
    assert P.unchanged(DeltaTable(str(tmp_path / "t")), "fp") is True


def test_an_overwrite_of_the_same_rows_is_written_again_unless_asked_not_to(tmp_path):
    ref = Ref(tmp_path / "orders")
    data = pa.table({"id": [1, 2]})
    P.publish(data, ref, "overwrite", "token", rows="fp1")
    again = P.publish(data, ref, "overwrite", "token", rows="fp1")
    assert again.skipped is False and DeltaTable(ref.uri).version() == 1


def test_the_row_fingerprint_is_kept_only_on_an_overwrite(tmp_path):
    ref = Ref(tmp_path / "orders")
    P.publish(pa.table({"id": [1]}), ref, "overwrite", "token", rows="fp1")
    assert DeltaTable(ref.uri).history(1)[0].get(P.ROWS_KEY) == "fp1"
    P.publish(pa.table({"id": [2]}), ref, "append", "token", rows="fp2")
    assert P.ROWS_KEY not in DeltaTable(ref.uri).history(1)[0]


def test_without_the_files_before_an_append_the_written_bytes_are_unknown(tmp_path, monkeypatch):
    ref = Ref(tmp_path / "orders")
    P.publish(pa.table({"id": [1]}), ref, "overwrite", "token")
    real, calls = P._files, []

    def files(table):
        calls.append(table)
        return None if len(calls) == 1 else real(table)  # the files before: unknown; after: listed
    monkeypatch.setattr(P, "_files", files)
    table = P.publish(pa.table({"id": [2]}), ref, "append", "token")
    assert len(calls) == 2 and table.written_bytes is None


@pytest.mark.parametrize("t, label", [
    (pa.int8(), "tinyint"), (pa.int16(), "smallint"), (pa.int32(), "int"), (pa.int64(), "bigint"),
    (pa.uint8(), "tinyint"), (pa.float16(), "float"), (pa.float32(), "float"), (pa.float64(), "double"),
])
def test_type_names_in_a_refusal(t, label):
    assert P._type_label(t) == label


def test_other_types_are_named_as_the_app_names_them():
    assert P._type_label(pa.string()) == type_name(pa.string())
    assert P._type_label(pa.date32()) == type_name(pa.date32())
    assert P._type_label(pa.string())


def test_a_write_that_fails_for_another_reason_raises_its_own_error(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(P, "write_deltalake", broken)
    with pytest.raises(RuntimeError, match="disk full"):
        P.publish(pa.table({"id": [1]}), Ref(tmp_path / "orders"), "overwrite", "token")


def test_a_job_records_how_long_it_took_to_the_hundredth_of_a_second(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace

    from fabric_desktop.jobs import Job, Target
    clock = iter([100.0, 101.23456])
    monkeypatch.setattr(P, "time", SimpleNamespace(monotonic=lambda: next(clock), strftime=time.strftime,
                                                   gmtime=time.gmtime))
    monkeypatch.setattr(P, "resolve_target", lambda j, fabric: Ref(tmp_path / "lake" / "orders"))
    job = Job("orders", "duckdb", tmp_path, Target("ws1", "LH_Sales", "orders"), sql="SELECT 1 AS id")
    record = P.run_job(job, None, lambda: "tok", tmp_path / "work")
    assert record.seconds == 1.23


def test_only_an_overwrite_of_a_table_already_there_replaces_rows_by_predicate(tmp_path):
    """Replacing every row by predicate makes Delta check for commits since the version loaded. That is
    for an overwrite of a table already there; an append, or a table's first write, commits without one."""
    ref = Ref(tmp_path / "orders")
    P.publish(pa.table({"id": [1]}), ref, "overwrite", "token")
    P.publish(pa.table({"id": [2]}), ref, "append", "token")
    P.publish(pa.table({"id": [3]}), ref, "overwrite", "token")
    params = [h["operationParameters"] for h in reversed(DeltaTable(ref.uri).history())]
    assert [p.get("predicate") for p in params] == [None, None, "true"]
