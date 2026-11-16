"""Mutation batch 45: orchestration/copyjob.py's local check names each end's connection type."""

from __future__ import annotations

from fabric_desktop.orchestration import copyjob


def _end(conn_type, kind):
    return {"type": kind, "connection_type": conn_type}


def test_the_reason_names_the_connection_type_then_the_type_then_not_set():
    got = copyjob.local_check("Batch", _end("SqlServer", "SqlServerTable"), _end("", "Warehouse"))
    assert got["possible"] is False
    assert got["reasons"][0].startswith("The source is SqlServer, not a lakehouse")
    assert got["reasons"][1].startswith("The destination is Warehouse, not a lakehouse")
    got = copyjob.local_check("Batch", _end("", ""), _end("Lakehouse", "LakehouseTable"))
    assert got["reasons"] == [("The source is not set, not a lakehouse: it needs Fabric's connection, so it runs "
                              "in Fabric only.")]
    assert copyjob.local_check("Batch", _end("Lakehouse", "x"), _end("Lakehouse", "y")) == {"possible": True,
                                                                                         "reasons": []}
