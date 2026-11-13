"""Warehouse queries: the refusals, the token handed to the driver, and a run's time limit, with a
stand-in driver and connection. Nothing reaches a warehouse."""

import struct
import sys
from types import SimpleNamespace

import pytest

from fabric_desktop import warehouse


@pytest.mark.parametrize("ref, says", [
    ({"kind": "lakehouse", "workspace_id": "w", "item_id": "i"}, "kind must be one of"),
    ({"kind": "warehouse", "workspace_id": "w", "item_id": ""}, "Pick a warehouse"),
    ({"kind": "endpoint", "item_id": "i"}, "Pick a warehouse"),
])
def test_a_reference_that_names_no_warehouse_is_refused_before_fabric_is_asked(ref, says):
    with pytest.raises(ValueError, match=says):
        warehouse.resolve(None, ref)  # None: asking Fabric would fail the test


@pytest.mark.parametrize("query", ["-- only a comment", ";"])
def test_a_query_with_no_statement_is_empty(query):
    with pytest.raises(ValueError, match="The query is empty."):
        warehouse.verdicts(query)


def test_a_command_sqlglot_cant_read_is_a_write_named_by_its_verb():
    [v] = warehouse.verdicts("RENAME OBJECT dbo.a TO b", writes=True)
    assert (v.kind, v.reason) == ("warehouse_write", "RENAME changes the warehouse")


def test_connect_hands_the_token_to_the_driver_as_its_access_token_and_turns_pooling_off(monkeypatch):
    seen = {}

    def connect(text, attrs_before, autocommit, timeout):
        seen.update(text=text, attrs=attrs_before, autocommit=autocommit, timeout=timeout)
        return "connection"

    driver = SimpleNamespace(pooling=lambda enabled: seen.setdefault("pooling", enabled), connect=connect)
    monkeypatch.setitem(sys.modules, "mssql_python", driver)
    con = warehouse.connect("abc.datawarehouse.fabric.microsoft.com", "WH}Gold", "tok-1", login_timeout=7)
    assert con == "connection" and seen["pooling"] is False
    assert seen["text"].startswith("Server=tcp:abc.datawarehouse.fabric.microsoft.com,1433;Database={WH}}Gold};")
    assert "Encrypt=yes;TrustServerCertificate=no" in seen["text"] and "tok-1" not in seen["text"]
    raw = "tok-1".encode("utf-16-le")
    assert seen["attrs"] == {warehouse.SQL_COPT_SS_ACCESS_TOKEN: struct.pack(f"<I{len(raw)}s", len(raw), raw)}
    assert (seen["autocommit"], seen["timeout"]) == (False, 7)


def test_a_run_with_a_time_limit_sets_it_on_the_connection(monkeypatch, tmp_path):
    class Cursor:
        description = None

        def execute(self, q):
            pass

    class Con:
        timeout = 0
        closed = rolled_back = False

        def cursor(self):
            return Cursor()

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    con = Con()
    monkeypatch.setattr(warehouse, "open_connection", lambda server, database, token: con)
    said = []
    job = {"query": "SELECT 1", "out": str(tmp_path / "out.arrow"), "time_limit_s": "45",
           "warehouse": {"server": "abc.datawarehouse.fabric.microsoft.com", "database": "WH"}}
    warehouse.run(job, lambda: "tok", lambda kind, **f: said.append(kind))
    assert con.timeout == 45 and con.rolled_back and con.closed
    assert said == ["started", "done"]
