"""Side by side (side_by_side.py): the comparison edges the Delta runs don't reach. A table changed in place,
a table one side can't read, dates and booleans, an unusual column type, the abstract Tables, and this
PC's memory on Windows and when it can't be read. Tables are in memory; nothing reaches OneLake."""

from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa
import pytest

from fabric_desktop import side_by_side as sbs


class MemoryTables(sbs.Tables):
    """Each table's versions as Arrow tables, and its Delta history as given."""

    def __init__(self, data, history=None, broken=()):
        self.data, self._history, self.broken = data, history or {}, set(broken)

    def history(self, table):
        if table in self.broken:
            raise OSError(f"{table}: the _delta_log can't be read")
        return self._history.get(table, [])

    def dataset(self, table, version):
        return self.data[table][version]


def test_a_table_changed_in_place_compares_the_whole_table_after_each_run():
    merge = [{"version": 1, "operation": "MERGE"}]
    update = [{"version": 2, "operation": "UPDATE"}]
    tables = MemoryTables({"dbo.c": {1: pa.table({"id": [1, 2]}), 2: pa.table({"id": [2, 1]})}},
                          history={"dbo.c": merge + update})
    [row] = sbs.compare(tables, {"dbo.c": 0}, {"dbo.c": 1}, {"dbo.c": 2})
    assert row["local"]["mode"] == "in place" and row["fabric"]["mode"] == "in place"
    assert row["result"] == "match"
    assert row["note"] == "Changed in place (MERGE, UPDATE or DELETE): compared the whole table after each run."
    assert len(row["local"]["checksum"]) == 16  # shown as hex


def test_one_unreadable_table_is_an_error_row_and_the_rest_still_compare():
    tables = MemoryTables({"dbo.ok": {0: pa.table({"n": [1]}), 1: pa.table({"n": [1]})}},
                          history={"dbo.ok": [{"version": 1, "operation": "WRITE",
                                               "operationParameters": {"mode": "Overwrite"}}]},
                          broken={"dbo.bad"})
    rows = sbs.compare(tables, {"dbo.bad": 0}, {"dbo.bad": 1, "dbo.ok": 0}, {"dbo.bad": 1, "dbo.ok": 1})
    assert [r["table"] for r in rows] == ["dbo.bad", "dbo.ok"]
    assert rows[0] == {"table": "dbo.bad", "result": "error",
                       "note": "Couldn't read it: dbo.bad: the _delta_log can't be read"}
    assert rows[1]["result"] == "match"


def test_dates_and_booleans_are_compared_by_value_whatever_the_row_order():
    import datetime as dt
    days = [dt.date(2026, 11, 1), dt.date(2026, 11, 2)]
    a = sbs.fingerprint(pa.table({"day": days, "open": [True, False]}))
    b = sbs.fingerprint(pa.table({"day": days[::-1], "open": [False, True]}))
    assert a == b and a["columns"] == [["day", "date"], ["open", "boolean"]]
    assert sbs.fingerprint(pa.table({"day": days, "open": [True, True]}))["checksum"] != a["checksum"]


def test_a_type_with_no_plain_name_is_shown_as_arrow_names_it():
    assert sbs.kind(pa.list_(pa.int32())) == "list<item: int32>"
    assert sbs.kind(pa.binary()) == "binary"


@pytest.mark.parametrize("method, args", [("versions", ()), ("history", ("t",)), ("dataset", ("t", 0))])
def test_the_tables_a_comparison_reads_must_say_how(method, args):
    with pytest.raises(NotImplementedError):
        getattr(sbs.Tables(), method)(*args)


def test_this_pcs_memory_on_windows_comes_from_global_memory_status(monkeypatch):
    import ctypes
    asked = []

    def status_ex(status):
        asked.append(status.dwLength)
        status.ullTotalPhys = 16 * 1024 ** 3

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(GlobalMemoryStatusEx=status_ex)),
                        raising=False)
    monkeypatch.setattr(ctypes, "byref", lambda obj: obj)
    monkeypatch.setattr(sbs, "os", SimpleNamespace(name="nt"))
    assert sbs._memory_gb() == 16.0
    assert asked == [2 * ctypes.sizeof(ctypes.c_ulong) + 7 * 8]  # MEMORYSTATUSEX's size, as Windows checks it


def test_memory_that_cant_be_read_is_left_out_and_logged(monkeypatch):
    logged = []

    def sysconf(name):
        raise ValueError(f"unknown {name}")

    monkeypatch.setattr(sbs, "os", SimpleNamespace(name="posix", sysconf=sysconf))
    monkeypatch.setattr(sbs._log, "info", lambda *a: logged.append(a))
    assert sbs._memory_gb() is None
    assert logged and logged[0][0] == "Couldn't read this PC's memory: %s" and "unknown SC_PAGE_SIZE" in logged[0][1]
