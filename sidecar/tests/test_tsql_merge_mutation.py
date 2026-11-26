"""tsql_merge's clause rules, read directly (mutation testing, batch 328).

test_tsql_merge_edges.py runs each rule through the local engine with aliased tables in dbo; these pin the
rest: a source without an alias read by its name, a target without a schema, a schema with more tables than
the target, a MERGE without its semicolon after another statement, and an INSERT with no VALUES.
"""

from __future__ import annotations

import pytest
import sqlglot

from fabric_desktop.localdata import tsql_merge
from fabric_desktop.localdata.tsql import KfTSQL

ON = "ON t.id = src.id"


def tree(sql: str):
    return sqlglot.parse(sql, read=KfTSQL)[0]


def test_a_source_without_an_alias_is_read_by_its_table_name():
    sql = f"MERGE dbo.tgt AS t USING dbo.src {ON} WHEN NOT MATCHED BY SOURCE AND src.v = 'x' THEN DELETE;"
    with pytest.raises(ValueError, match=r"WHEN NOT MATCHED BY SOURCE reads only the table's columns"):
        tsql_merge.check(tree(sql))


COLUMNS = {"dbo": {"other": {"id": "int", "v": "varchar", "w": "int"}, "tgt": {"id": "int", "v": "varchar"}}}


@pytest.mark.parametrize("target", ["dbo.tgt", "tgt"])
def test_insert_values_without_columns_is_counted_against_the_target_table_only(target):
    good = f"MERGE {target} AS t USING dbo.src AS src {ON} WHEN NOT MATCHED THEN INSERT VALUES (src.id, src.v);"
    tsql_merge.check(tree(good), COLUMNS)
    bad = f"MERGE {target} AS t USING dbo.src AS src {ON} WHEN NOT MATCHED THEN INSERT VALUES (src.id);"
    with pytest.raises(ValueError, match=r"it has 2 \(id, v\), but VALUES gives 1"):
        tsql_merge.check(tree(bad), COLUMNS)


def test_a_merge_after_another_statement_still_needs_its_semicolon():
    sql = f"SELECT 1; MERGE dbo.tgt AS t USING dbo.src AS src {ON} WHEN MATCHED THEN DELETE"
    with pytest.raises(ValueError, match=r"A MERGE must end with a semicolon"):
        tsql_merge.refuse_unterminated(KfTSQL().tokenize(sql))


@pytest.mark.parametrize("insert", ["INSERT", "INSERT (id, v)"])
def test_an_insert_without_values_is_refused_by_name(insert):
    sql = f"MERGE dbo.tgt AS t USING dbo.src AS src {ON} WHEN NOT MATCHED THEN {insert};"
    with pytest.raises(ValueError, match=r"A MERGE's INSERT needs VALUES"):
        tsql_merge.check(tree(sql))
