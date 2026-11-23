"""Mutation pins for delta_read (docs/quality/mutation-testing.md): which features make the pyarrow reader
unsafe, the deleted row ranges a deletion vector gives at each edge, and the schema_mode a write gets on
a column-mapped table. Fake tables stand in for the protocol, settings, schema and vectors."""

from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa
import pytest

from fabric_desktop import delta_read

DV = ["deletionVectors"]


def _fake(config=None, readers=None, writers=None, columns=(), vectors=None, to_arrow=False):
    meta = SimpleNamespace(configuration=config)
    proto = SimpleNamespace(reader_features=readers, writer_features=writers)
    schema = pa.schema([pa.field(c, pa.int64()) for c in columns])
    raw = SimpleNamespace(to_arrow=lambda: schema) if to_arrow else SimpleNamespace(to_pyarrow=lambda: schema)

    def deletion_vectors():
        if vectors is None:
            raise AssertionError("deletion vectors read on a table without them")
        return {"filepath": [p for p, _ in vectors], "selection_vector": [k for _, k in vectors]}

    return SimpleNamespace(metadata=lambda: meta, protocol=lambda: proto, schema=lambda: raw,
                           deletion_vectors=deletion_vectors)


# ---- needs_kernel


@pytest.mark.parametrize(("kw", "want"), [
    ({}, []),
    ({"config": None}, []),
    ({"readers": DV}, ["deletion vectors"]),
    ({"writers": DV}, ["deletion vectors"]),
    ({"config": {"delta.enableDeletionVectors": "true"}}, []),  # vectors need the protocol feature
    ({"config": {"delta.columnMapping.mode": "name"}}, ["column mapping"]),
    ({"config": {"delta.columnMapping.mode": "id"}}, ["column mapping"]),
    ({"config": {"delta.columnMapping.mode": "ID"}}, ["column mapping"]),
    ({"config": {"delta.columnMapping.mode": "NONE"}}, []),
    ({"config": {"delta.columnMapping.mode": None}}, []),
    ({"readers": ["columnMapping"]}, ["column mapping"]),
    ({"writers": ["appendOnly", "invariants"]}, []),
    ({"readers": ["columnMapping", "deletionVectors"], "config": {"delta.columnMapping.mode": "name"}},
     ["deletion vectors", "column mapping"]),
], ids=repr)
def test_needs_kernel_for_each_feature(kw, want):
    assert delta_read.needs_kernel(_fake(**kw)) == want


@pytest.mark.parametrize(("mode", "want"), [("name", "name"), ("Name", "name"), ("id", "id"), ("none", ""),
                                            ("None", ""), ("", ""), (None, "")])
def test_column_mapping_mode(mode, want):
    assert delta_read.column_mapping(_fake({"delta.columnMapping.mode": mode})) == want
    assert delta_read.column_mapping(_fake(None)) == ""


# ---- deleted_positions


@pytest.mark.parametrize(("keep", "ranges"), [
    ([], None),
    ([True, True, True], None),
    ([False], [(0, 0)]),
    ([False, False, False], [(0, 2)]),
    ([True, False, False], [(1, 2)]),
    ([True, True, False], [(2, 2)]),
    ([False, True, True], [(0, 0)]),
    ([True, False, False, True], [(1, 2)]),
    ([True, False, True, False, True], [(1, 1), (3, 3)]),
    ([False, True, False], [(0, 0), (2, 2)]),
    ([False, False, True, False, False], [(0, 1), (3, 4)]),
], ids=repr)
def test_deleted_positions_at_each_edge(keep, ranges):
    table = _fake(readers=DV, writers=DV, vectors=[("f.parquet", keep)])
    assert delta_read.deleted_positions(table) == ({} if ranges is None else {"f.parquet": ranges})
    assert delta_read.deleted_rows(table) == keep.count(False)


def test_deleted_positions_keep_each_file_apart_and_leave_out_files_with_none_deleted():
    table = _fake(writers=DV, vectors=[("a", [True, False]), ("b", [True, True]), ("c", [False, False, True])])
    assert delta_read.deleted_positions(table) == {"a": [(1, 1)], "c": [(0, 1)]}
    assert delta_read.deleted_rows(table) == 3


def test_no_vectors_are_read_from_a_table_without_the_feature():
    table = _fake(config={"delta.enableDeletionVectors": "true"})
    assert delta_read.deleted_positions(table) == {}
    assert delta_read.deleted_rows(table) == 0
    assert delta_read.deleted_positions(_fake(writers=DV, vectors=[])) == {}


# ---- schema_mode_for


CM = {"delta.columnMapping.mode": "name"}
HAVE = ["id", "total", "the label"]


@pytest.mark.parametrize("wanted", [None, "merge", "overwrite"])
def test_no_table_or_no_column_mapping_keeps_the_wanted_mode(wanted):
    assert delta_read.schema_mode_for(None, ["x"], wanted) == wanted
    plain = _fake({"delta.columnMapping.mode": "none"}, columns=HAVE)
    assert delta_read.schema_mode_for(plain, ["x", "y"], wanted) == wanted
    assert delta_read.schema_mode_for(_fake(columns=HAVE), HAVE, wanted) == wanted


@pytest.mark.parametrize("wanted", ["append", "Merge", "OVERWRITE", "", "none"])
def test_an_unknown_mode_is_refused_even_with_no_table(wanted):
    with pytest.raises(ValueError) as err:
        delta_read.schema_mode_for(None, [], wanted)
    assert str(err.value) == f"schema_mode must be merge, overwrite or None, got {wanted!r}"


@pytest.mark.parametrize("wanted", ["merge", "overwrite"])
@pytest.mark.parametrize("to_arrow", [False, True])
def test_the_same_columns_in_any_order_go_without_a_schema_mode(wanted, to_arrow):
    table = _fake(CM, columns=HAVE, to_arrow=to_arrow)
    assert delta_read.schema_mode_for(table, HAVE, wanted) is None
    assert delta_read.schema_mode_for(table, ["the label", "id", "total"], wanted) is None
    assert delta_read.schema_mode_for(_fake({"delta.columnMapping.mode": "id"}, columns=HAVE),
                                      list(reversed(HAVE)), wanted) is None


def test_with_no_mode_wanted_a_column_mapped_table_is_left_to_the_write():
    assert delta_read.schema_mode_for(_fake(CM, columns=HAVE), ["other"], None) is None


def test_a_case_only_rename_says_which_names_differ():
    table = _fake(CM, columns=HAVE)
    with pytest.raises(ValueError) as err:
        delta_read.schema_mode_for(table, ["ID", "total", "The Label"], "merge", name="dbo.sales")
    assert str(err.value) == ("dbo.sales uses column mapping, and this write names ID where the table has id; The Label "
                              "where the table has the label. Nothing was written. Use the table's column names, with "
                              "the same capital letters.")
    with pytest.raises(ValueError) as err:
        delta_read.schema_mode_for(table, ["id", "TOTAL", "the label"], "overwrite")
    assert str(err.value).startswith("The table uses column mapping, and this write names TOTAL where the table has "
                                     "total. Nothing was written.")


TAIL = (". KratosForge can't change a column-mapped table's columns yet, so nothing was written. Write the same "
        "columns, or change them in Fabric Spark first.")


@pytest.mark.parametrize(("columns", "change"), [
    (HAVE + ["z", "a"], "adds a, z"),
    (["id"], "leaves out the label, total"),
    (["id", "total", "new"], "adds new; leaves out the label"),
    (["x", "y"], "adds x, y; leaves out id, the label, total"),
    (["ID", "total"], "adds ID; leaves out id, the label"),
])
@pytest.mark.parametrize("wanted", ["merge", "overwrite"])
def test_a_change_of_columns_says_what_it_adds_and_leaves_out(columns, change, wanted):
    with pytest.raises(ValueError) as err:
        delta_read.schema_mode_for(_fake(CM, columns=HAVE), columns, wanted, name="sales")
    assert str(err.value) == f"sales uses column mapping, and this write {change}{TAIL}"
    with pytest.raises(ValueError) as err:
        delta_read.schema_mode_for(_fake(CM, columns=HAVE), columns, wanted)
    assert str(err.value) == f"The table uses column mapping, and this write {change}{TAIL}"
