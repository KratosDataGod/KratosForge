"""X-ray: the date and size helpers, staging names it can't date, and a scan where a table's log or
a lakehouse's Files can't be read. Stand-ins for Fabric and OneLake; nothing reaches a tenant."""

import datetime as dt

from fabric_desktop import xray

NOW = dt.datetime(2026, 11, 11, tzinfo=dt.timezone.utc)


def test_days_since_a_missing_or_zoneless_time():
    assert xray._days_since(None, NOW) is None and xray._days_since("", NOW) is None
    assert xray._days_since("2026-11-01T00:00:00", NOW) == 10  # no zone: read as UTC


def test_sizes_past_a_terabyte_stay_in_terabytes():
    assert xray._size(512) == "512 bytes"
    assert xray._size(3 * 1024 ** 5) == "3072.0 TB"


def test_staging_lakehouses_that_cant_be_dated_are_neither_newest_nor_left_over(monkeypatch):
    bad = {"workspace_id": "w", "workspace_name": "W", "lakehouse_id": "l1",
           "lakehouse_name": "StagingLakehouseForDataflows_20261399000000"}  # month 13
    assert xray.staging_split([bad]) == (None, [])
    assert xray.staging_findings([bad]) == []
    old = bad | {"lakehouse_id": "l2", "lakehouse_name": "StagingLakehouseForDataflows_20250101000000"}
    new = bad | {"lakehouse_id": "l3", "lakehouse_name": "StagingLakehouseForDataflows_20260101000000"}
    real = xray.staging_created
    asked = []

    def forgets_the_old_one(name):
        asked.append(name)
        return None if name == old["lakehouse_name"] and len(asked) > 2 else real(name)

    monkeypatch.setattr(xray, "staging_created", forgets_the_old_one)
    assert xray.staging_findings([old, new]) == []  # a name that stops reading as dated is skipped


class Fabric:
    def list_workspaces(self):
        return [{"id": "ws", "displayName": "Sales"}]

    def list_lakehouses(self, ws):
        return [{"id": "lh", "displayName": "LH_Raw"}]

    def list_role_assignments(self, ws):
        return []


def test_a_scan_reports_a_table_whose_log_cant_be_read_and_assumes_files_it_cant_list(monkeypatch):
    warned = []
    monkeypatch.setattr(xray._log, "warning", lambda msg, *a: warned.append(msg % a))
    names = [f"t{i:02d}" for i in range(12)]

    def files(ws, lh, directory):
        if directory == "Tables":
            return [{"name": n, "is_dir": True} for n in names]
        if directory == "Files":
            raise OSError("OneLake said 503")
        return [{"name": "_delta_log", "is_dir": True}]

    def health(ws, lh, schema, table):
        if table == "t03":
            raise RuntimeError("the log is damaged")
        return {"exists": True, "advice": [], "files": 1, "bytes": 10}

    progress = []
    out = xray.Scanner(Fabric(), files, health, progress=progress.append, workers=1).scan(NOW)
    assert out["tables"] == 12 and out["bytes"] == 110
    assert {"phase": "Reading table logs", "done": 10, "total": 12} in progress
    assert not [f for f in out["findings"] if f["kind"] == "empty"]  # Files couldn't be listed: not called empty
    assert any(w.startswith("Couldn't list Files in Sales / LH_Raw: ") for w in warned)
