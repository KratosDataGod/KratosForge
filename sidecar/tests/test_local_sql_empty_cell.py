"""A DuckDB engine cell with no statement answers the same on DuckDB 1.x and 2.0 (#2549).

DuckDB 1.x's execute returns None, not a cursor, for SQL that is only comments or semicolons, so
reading its description raised AttributeError. The next-DuckDB CI run covers 2.0."""

from __future__ import annotations

import pytest

from fabric_desktop.engines import local_sql
from fabric_desktop.engines.contract import CellRequest, SessionConfig


@pytest.mark.parametrize("code", ["-- nothing", ";"])
def test_a_cell_with_no_statement_is_done_without_rows(code):
    s = local_sql.DuckDBSession(SessionConfig("nb"))
    try:
        result = s.execute(CellRequest(code, "sql"))
        assert result.status == "ok" and result.rows is None
        assert [o["type"] for o in result.outputs] == ["text"]
        assert result.outputs[0]["text"].startswith("Done in ")
        follow = s.execute(CellRequest("SELECT 41 + 1 AS n", "sql"))
        assert follow.status == "ok" and follow.outputs[0]["rows"] == [[42]]
    finally:
        s.close()
