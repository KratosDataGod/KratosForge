"""A refused or failed Delta write leaves no reader open on the project's connection.

On DuckDB 2.0 a reader that isn't read to its end keeps the connection busy, so the next query and the
CHECKPOINT on close failed with "connection has an open result". The pinned 1.x passes either way;
the next-DuckDB CI run is what catches a regression.

Each test holds on to the error, as a caller that logs or reports it does: its traceback keeps the
write's frame, and so the reader, alive, so the reader isn't closed by being garbage collected."""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture
def local(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    yield local
    local.close_all()


@pytest.fixture
def engine(local):
    e = local.engine(local.create("Refused")["id"])
    e.execute("CREATE TABLE tiny USING DELTA AS SELECT 1::TINYINT AS a")
    return e


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_value_that_doesnt_fit_leaves_the_connection_free(engine, local, dry_run):
    with pytest.raises(ValueError, match="doesn't fit its type int8") as caught:
        engine.write_delta("dbo.tiny", "SELECT 1000 AS a", mode="append", dry_run=dry_run)
    local.close_all()  # CHECKPOINT runs on close: it failed while the refused write's reader was open
    assert engine.execute("SELECT 41 + 1 AS n")["rows"] == [[42]]  # and the project opens again
    assert caught.value.__traceback__ is not None


def test_columns_that_dont_match_leave_the_connection_free(engine, local):
    with pytest.raises(ValueError, match="columns don't match") as caught:
        engine.write_delta("dbo.tiny", "SELECT 1 AS b", mode="append")
    local.close_all()
    assert engine.execute("SELECT 41 + 1 AS n")["rows"] == [[42]]
    assert caught.value.__traceback__ is not None


def test_a_partition_column_the_query_lacks_leaves_the_connection_free(engine, local):
    with pytest.raises(ValueError, match="no such column") as caught:
        engine.write_delta("dbo.fresh", "SELECT 1 AS a", partition_by=["nope"])
    local.close_all()
    assert engine.execute("SELECT 41 + 1 AS n")["rows"] == [[42]]
    assert caught.value.__traceback__ is not None
