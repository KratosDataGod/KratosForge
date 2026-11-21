"""Mutation batch 80: the Direct Lake check's service half. How row groups are sampled and when they're an
estimate, a Delta log without record counts, the 200 footers read by default, relationship keys whose
types differ, and the answer for a model with no Direct Lake tables."""

from __future__ import annotations

import inspect
import os

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from deltalake import DeltaTable, WriterProperties, write_deltalake

from fabric_desktop import model_directlake_service as mds
from fabric_desktop.model_directlake import TableStats
from tests.test_model_directlake import svc  # noqa: F401 - the service with a stand-in Fabric and local tables

REAL_GET_ADD_ACTIONS = DeltaTable.get_add_actions


def _table(uri, groups_per_file):
    """A Delta table with one file per entry, each with that many row groups of 10 rows."""
    for i, groups in enumerate(groups_per_file):
        write_deltalake(uri, pa.table({"k": list(range(groups * 10))}), mode="append" if i else "error",
                        writer_properties=WriterProperties(max_row_group_size=10))


def _groups_in_log_order(uri):
    paths = pa.table(DeltaTable(uri).get_add_actions(flatten=True)).column("path").to_pylist()
    return [pq.read_metadata(os.path.join(uri, p)).num_row_groups for p in paths]


def test_row_groups_are_sampled_across_the_table_not_from_its_first_files(tmp_path):
    uri = str(tmp_path / "t")
    _table(uri, [1, 2, 3, 4])
    groups = _groups_in_log_order(uri)
    assert sorted(groups) == [1, 2, 3, 4]
    est = mds.table_stats(uri, None, footers=2)
    # Four files, two footers: every second file is read (the 1st and 3rd), and the total scaled up.
    assert est.row_groups == round((groups[0] + groups[2]) / 2 * 4) and est.row_groups_estimated
    assert mds.table_stats(uri, None).row_groups == 10


def test_row_groups_are_exact_when_every_footer_is_read(tmp_path):
    uri = str(tmp_path / "t")
    _table(uri, [1, 2, 3])
    exact = mds.table_stats(uri, None, footers=3)
    assert (exact.files, exact.row_groups, exact.row_groups_estimated) == (3, 6, False)


def test_the_check_reads_200_footers_at_most_by_default():
    assert mds.FOOTERS_READ == 200
    assert inspect.signature(mds.table_stats).parameters["footers"].default == 200


def test_a_log_without_record_counts_leaves_rows_unknown(tmp_path, monkeypatch):
    uri = str(tmp_path / "t")
    _table(uri, [1, 2])

    def without_counts(self, flatten=False):
        actions = pa.table(REAL_GET_ADD_ACTIONS(self, flatten=flatten))
        return actions.drop_columns(["num_records"])

    monkeypatch.setattr(DeltaTable, "get_add_actions", without_counts)
    s = mds.table_stats(uri, None)
    assert (s.rows, s.files, s.row_groups) == (None, 2, 3)


def test_a_relationship_whose_key_types_differ_is_not_matched_row_by_row(svc, monkeypatch):  # noqa: F811
    real = mds.table_stats
    matched = []

    def stats(uri, options, footers=mds.FOOTERS_READ):
        s = real(uri, options, footers)
        if uri.endswith("dim_customer"):
            s = TableStats([("customer_sk", "string"), ("country", "string")], rows=s.rows, files=s.files,
                           bytes=s.bytes, row_groups=s.row_groups)
        return s

    monkeypatch.setattr(mds, "table_stats", stats)
    monkeypatch.setattr(mds, "unmatched_rows", lambda many, one: matched.append((many, one)) or {
        "rows": 0, "values": 0, "empty": 0})
    out = svc.meta_model_directlake_check("test", "SM_Gold", keys=True)
    assert matched == [] and out["unread"] == []
    assert not [f for f in out["findings"] if f["check"] == "Rows with no match"]


def test_a_model_with_no_direct_lake_tables_has_nothing_to_report(svc):  # noqa: F811
    out = svc.meta_model_directlake_check("test", "Import model", keys=True)
    assert out["direct_lake"] is False and out["keys_checked"] is False
    assert out["counts"] == {"fail": 0, "warn": 0, "info": 0}
    assert (out["findings"], out["tables"], out["unread"]) == ([], [], [])


def test_a_model_that_isnt_there_is_named_with_its_workspace(svc):  # noqa: F811
    with pytest.raises(LookupError, match="There's no semantic model named Nope in WS Test"):
        svc.meta_model_directlake_check("test", "Nope")
