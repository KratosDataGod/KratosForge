"""localdata/tsql_merge.py: the mutants that test_tsql_merge_mutation.py and the module's other tests leave
alive. A source query with no alias names nothing, so a WHEN NOT MATCHED BY SOURCE clause that reads the
table's columns isn't refused."""

from __future__ import annotations

import sqlglot

from fabric_desktop.localdata import tsql_merge


def parse(sql):
    return sqlglot.parse_one(sql, read="tsql")


def test_a_source_query_with_no_alias_names_nothing():
    tsql_merge.check(parse("MERGE dbo.tgt AS t USING (SELECT 1 AS id) ON t.id = 1 "
                           "WHEN NOT MATCHED BY SOURCE AND t.v = 'x' THEN DELETE;"))
