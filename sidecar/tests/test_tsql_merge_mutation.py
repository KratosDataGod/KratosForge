"""tsql_merge.py: what mutation testing showed no test checked. A source with no alias is named by its table's
name in the column-reading rules, and a semicolon ends a MERGE at the top level, so a second MERGE after it
without its own semicolon is refused too."""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot.dialects.dialect import Dialect

from fabric_desktop.localdata import tsql_merge


def parse(sql):
    return sqlglot.parse_one(sql, read="tsql")


def tokens(sql):
    return Dialect.get_or_raise("tsql").tokenize(sql)


def test_a_source_with_no_alias_is_named_by_its_table():
    with pytest.raises(ValueError, match=r"WHEN NOT MATCHED BY SOURCE reads only the table's columns.*src\.v"):
        tsql_merge.check(parse("MERGE dbo.tgt AS t USING dbo.src ON t.id = src.id "
                               "WHEN NOT MATCHED BY SOURCE AND src.v = 'x' THEN DELETE;"))
    tsql_merge.check(parse("MERGE dbo.tgt AS t USING dbo.src ON t.id = src.id "
                           "WHEN NOT MATCHED BY SOURCE AND t.v = 'x' THEN DELETE;"))  # its own columns: fine


def test_a_source_query_with_no_alias_names_nothing():
    tsql_merge.check(parse("MERGE dbo.tgt AS t USING (SELECT 1 AS id) ON t.id = 1 "
                           "WHEN NOT MATCHED BY SOURCE AND t.v = 'x' THEN DELETE;"))


MERGE = "MERGE dbo.t AS t USING dbo.s AS s ON t.id = s.id WHEN MATCHED THEN DELETE"


def test_each_merge_after_a_semicolon_needs_its_own():
    tsql_merge.refuse_unterminated(tokens(f"{MERGE}; {MERGE};"))
    with pytest.raises(ValueError, match="must end with a semicolon"):
        tsql_merge.refuse_unterminated(tokens(f"{MERGE}; {MERGE}"))
    with pytest.raises(ValueError, match="must end with a semicolon"):
        tsql_merge.refuse_unterminated(tokens(f"SELECT (1; {MERGE} SELECT 2;"))  # ; closes the open bracket too
