"""delta_dml.py: behavior that mutation testing (docs/quality/mutation-testing.md, batch 2) showed no test
pinned. The refusals' own wording (which table the message names), a target or assignment that isn't one,
a MERGE built without USING or ON, a THEN that is neither DELETE, UPDATE nor INSERT, quoted names in the
SQL handed to delta-rs, which copied values are checked for EA-001, and the counts a change reports."""

from __future__ import annotations

import pyarrow as pa
import pytest
from sqlglot import exp

from fabric_desktop import delta_dml as D
from fabric_desktop import sql_dialects


def test_a_refused_update_from_and_delete_using_name_the_table_not_its_schema():
    with pytest.raises(ValueError) as e:
        D.parse("UPDATE dbo.sales SET a = 1 FROM s WHERE sales.id = s.id")
    assert "Use MERGE INTO sales USING" in str(e.value)
    with pytest.raises(ValueError) as e:
        D.parse("DELETE FROM dbo.sales USING s WHERE sales.id = s.id")
    assert "Use MERGE INTO sales USING" in str(e.value)


@pytest.mark.parametrize("sql, alias", [
    ("UPDATE dbo.sales SET a = 1", "sales"),
    ("UPDATE dbo.sales AS x SET a = 1", "x"),
    ("DELETE FROM dbo.sales WHERE a = 1", "sales"),
    ("DELETE FROM dbo.sales AS x WHERE a = 1", "x"),
])
def test_update_and_delete_call_the_table_by_its_alias_or_else_its_name(sql, alias):
    change = D.parse(sql)
    assert (change.schema, change.table, change.alias) == ("dbo", "sales", alias)


def test_a_target_that_is_no_named_table_is_refused():
    with pytest.raises(ValueError, match="UPDATE needs a table to change"):
        D._target(exp.Literal.number(1), "UPDATE")
    with pytest.raises(ValueError, match="DELETE needs a table to change"):
        D._target(exp.Table(), "DELETE")
    assert D._target(exp.to_table("dbo.sales"), "UPDATE") == ("dbo", "sales", "")


def test_an_assignment_to_something_that_isnt_a_column_is_refused():
    with pytest.raises(ValueError, match=r"1 = 2 isn't column = value"):
        D.parse("UPDATE t SET 1 = 2")


def _merge(sql: str) -> exp.Merge:
    return sql_dialects.parse_one(sql, read="spark")


def test_a_merge_without_using_or_on_is_refused_with_its_own_message():
    tree = _merge("MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE")
    no_using = tree.copy()
    no_using.set("using", None)
    with pytest.raises(ValueError, match=r"MERGE INTO needs USING \(the rows to merge\)"):
        D.parse_tree(no_using)
    no_on = tree.copy()
    no_on.set("on", None)
    with pytest.raises(ValueError, match=r"MERGE INTO needs ON \(how a source row finds"):
        D.parse_tree(no_on)


def test_a_then_that_is_another_keyword_is_refused_not_taken_for_delete():
    with pytest.raises(ValueError, match="WHEN ... THEN DO NOTHING isn't supported in MERGE"):
        D.parse("MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DO NOTHING")


def test_names_go_to_delta_rs_quoted_with_the_tables_own_case():
    change = D.parse("MERGE INTO dbo.Sales AS T USING src AS S ON T.Id = S.Id "
                     "WHEN MATCHED THEN UPDATE SET Amount = S.Amount")
    names = D._Names(change, ["Id", "Amount"], ["Id", "Amount"])
    assert names.render(change.on) == '"T"."Id" = "S"."Id"'
    assert names.render(change.whens[0].sets[0][1]) == '"S"."Amount"'
    update = D.parse("UPDATE Sales SET amount = amount + 1 WHERE region = 1")
    names = D._Names(update, ["Region", "Amount"], [])
    assert names.render(update.where) == '"Region" = 1'
    assert names.render(update.sets[0][1]) == '"Amount" + 1'


@pytest.mark.parametrize("value, copied", [
    ("s.x", {"x": "a"}),      # qualified with the source's alias
    ("x", {"x": "a"}),        # unqualified, and the table has no x
    ("t.b", {}),              # the table's own column: nothing comes from the source
    ("b", {}),                # unqualified, and the table has b: the table's column
    ("s.x + 1", {}),          # computed, not copied as it is
])
def test_the_source_columns_a_merge_copies_as_they_are(value, copied):
    change = D.parse(f"MERGE INTO t USING src AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET a = {value}")
    names = D._Names(change, ["id", "a", "b"], ["id", "x", "b"])
    assert D._copied(change, names) == copied


def test_a_value_out_of_the_columns_range_says_why():
    with pytest.raises(ValueError) as e:
        D._check_values("dbo.t", "c", pa.array([300]), pa.int8())
    assert "(int8): Integer value 300 not in range" in str(e.value)


def test_a_value_that_comes_back_from_the_cast_unchanged_is_fine():
    D._check_values("dbo.t", "c", pa.array([1.0, None, 12.0]), pa.int64())  # raises nothing


def test_counts_a_change_doesnt_report_are_zero():
    assert D._result({"num_deleted_rows": 2}, deleted="num_deleted_rows") == {
        "rows_inserted": 0, "rows_updated": 0, "rows_deleted": 2, "files_added": 0, "files_removed": 0}
    assert D._result({"num_target_files_added": 3, "num_target_files_removed": 4, "num_source_rows": 5},
                     source="num_source_rows") == {
        "rows_inserted": 0, "rows_updated": 0, "rows_deleted": 0, "source_rows": 5, "files_added": 3,
        "files_removed": 4}
    assert D._result({"num_added_files": 1, "num_removed_files": 2, "num_target_files_added": 9,
                      "num_target_files_removed": 9}) == {
        "rows_inserted": 0, "rows_updated": 0, "rows_deleted": 0, "files_added": 1, "files_removed": 2}


# Batch 4 (re-score after #2528).

def test_a_merge_with_no_when_clause_is_refused():
    tree = _merge("MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE")
    tree.set("whens", None)
    with pytest.raises(ValueError, match="MERGE INTO needs at least one WHEN clause"):
        D.parse_tree(tree)


def test_a_count_the_change_names_but_delta_rs_leaves_out_or_reports_as_none_is_zero():
    assert D._result({}, deleted="num_deleted_rows")["rows_deleted"] == 0
    assert D._result({"num_updated_rows": None}, updated="num_updated_rows")["rows_updated"] == 0
    assert D._result({}, source="num_source_rows")["source_rows"] == 0
