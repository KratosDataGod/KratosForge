"""X-ray's thresholds, ranking, scan totals and what a finished scan shares, pinned where mutation
testing (docs/quality/mutation-testing.md, batch 84) found nothing that would notice a change.
Stand-ins for Fabric and OneLake; nothing reaches a tenant."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop import xray

NOW = dt.datetime(2026, 11, 11, tzinfo=dt.timezone.utc)
TABLE = {"workspace_id": "ws", "workspace_name": "Sales", "lakehouse_id": "lh", "lakehouse_name": "LH",
         "schema": "dbo", "table": "orders"}


def health(**kw):
    return {"exists": True, "advice": [], "files": 0, "bytes": 0, "avg_file_bytes": 0} | kw


def kinds(found):
    return [(f["kind"], f["severity"]) for f in found]


# ---------------------------------------------------------------- the thresholds


def test_the_thresholds_are_the_documented_ones():
    assert (xray.MAX_TABLES, xray.STALE_DAYS, xray.BIG_SMALL_FILES, xray.VACUUM_VERSIONS,
            xray.STAGING_QUIET_DAYS) == (500, 30, 200, 30, 30)
    assert xray.SEVERITY == {"high": 0, "medium": 1, "low": 2}


def test_two_hundred_small_files_is_high_and_one_fewer_is_medium():
    small = ["Many small files: compact them"]
    assert kinds(xray.table_findings(TABLE, health(advice=small, files=199), NOW)) == [("small_files", "medium")]
    assert kinds(xray.table_findings(TABLE, health(advice=small, files=200), NOW)) == [("small_files", "high")]


def test_only_small_files_advice_makes_a_small_files_finding():
    assert xray.table_findings(TABLE, health(advice=["Statistics are out of date"], files=500), NOW) == []


def test_a_table_is_stale_from_thirty_days_without_a_write():
    day29 = (NOW - dt.timedelta(days=29)).isoformat()
    day30 = (NOW - dt.timedelta(days=30)).isoformat()
    assert xray.table_findings(TABLE, health(last_write=day29), NOW) == []
    [stale] = xray.table_findings(TABLE, health(last_write=day30), NOW)
    assert stale["kind"] == "stale" and stale["days"] == 30


def test_vacuum_is_suggested_from_thirty_versions():
    assert xray.table_findings(TABLE, health(versions_since_vacuum=29), NOW) == []
    [vac] = xray.table_findings(TABLE, health(versions_since_vacuum=30, last_vacuum="2026-01-01"), NOW)
    assert vac["versions"] == 30 and vac["title"] == "dbo.orders has 30 versions since its last vacuum"


def test_a_kilobyte_is_shown_as_one():
    assert xray._size(1023) == "1023 bytes"
    assert xray._size(1024) == "1.0 KB"
    assert xray._size(1024 * 1024) == "1.0 MB"


def cost(name, amount):
    return {"kind": "Notebook run", "name": name, "cost": amount, "fabric_runs": 1, "cu_hours": 1.0}


def test_a_cost_from_a_cent_is_listed_and_from_ten_dollars_is_medium():
    report = {"days": 30, "by_item": [cost("cent", 0.01), cost("under", 0.009), cost("ten", 10.0),
                                      cost("nine", 9.99)]}
    found = {f["notebook"]: f["severity"] for f in xray.cost_findings(report, set())}
    assert found == {"ten": "medium", "nine": "low", "cent": "low"}


def test_only_the_five_costliest_notebooks_are_listed():
    report = {"days": 30, "by_item": [cost(f"nb{i}", float(i + 1)) for i in range(6)]}
    assert [f["notebook"] for f in xray.cost_findings(report, set())] == ["nb5", "nb4", "nb3", "nb2", "nb1"]


# ---------------------------------------------------------------- staging names near today


class Fixed(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 1, tzinfo=tz)


def staging(name, lid):
    return {"workspace_id": "w", "workspace_name": "W", "lakehouse_id": lid, "lakehouse_name": name}


@pytest.fixture
def jan_first(monkeypatch):
    class Clock:
        datetime, timezone, timedelta = Fixed, dt.timezone, dt.timedelta
    monkeypatch.setattr(xray, "dt", Clock)


def test_a_name_dated_up_to_a_day_ahead_can_be_the_newest(jan_first):
    old = staging("StagingLakehouseForDataflows_20250101000000", "old")
    half_day = staging("StagingLakehouseForDataflows_20260101120000", "half")
    assert xray.staging_split([old, half_day]) == (half_day, [old])
    exactly_a_day = staging("StagingLakehouseForDataflows_20260102000000", "day")
    assert xray.staging_split([old, exactly_a_day]) == (exactly_a_day, [old])
    past_a_day = staging("StagingLakehouseForDataflows_20260102000001", "later")
    assert xray.staging_split([old, past_a_day]) == (old, [])


# ---------------------------------------------------------------- ranking and counting


def finding(kind, title, severity="low", **kw):
    return {"kind": kind, "severity": severity, "title": title} | kw


def test_findings_of_one_severity_rank_by_kind_before_title():
    order = ["small_files", "unidentified", "access", "cost", "stale", "vacuum", "staging", "empty", "adopt",
             "something new"]
    titles = [chr(ord("z") - i) for i in range(len(order))]  # reverse alphabetical: the title can't decide
    ranked = xray.rank([finding(k, t) for k, t in zip(order, titles, strict=True)][::-1])
    assert [f["kind"] for f in ranked] == order


def test_severity_comes_before_kind():
    ranked = xray.rank([finding("adopt", "a", "low"), finding("adopt", "b", "medium"), finding("adopt", "c", "high")])
    assert [f["severity"] for f in ranked] == ["high", "medium", "low"]


def test_more_files_rank_first_and_a_finding_without_a_count_counts_none():
    ranked = xray.rank([finding("stale", "a"), finding("stale", "z", files=1)])
    assert [f["title"] for f in ranked] == ["z", "a"]


def test_counts_count_each_kind():
    assert xray.counts([finding("stale", "a"), finding("stale", "b"), finding("cost", "c")]) == {"stale": 2, "cost": 1}
    assert xray.counts([]) == {}


# ---------------------------------------------------------------- a scan over stand-ins


class Fabric:
    def __init__(self, lakehouses):
        self.lakehouses = lakehouses

    def list_workspaces(self):
        return [{"id": "ws", "displayName": "Sales"}]

    def list_lakehouses(self, ws):
        return [{"id": lid, "displayName": lid.upper()} for lid in self.lakehouses]

    def list_role_assignments(self, ws):
        return []


def scanner(lakehouses, healths=None, broken=(), progress=None):
    """``lakehouses``: id -> (table names, whether Files has anything)."""
    def files(ws, lh, directory):
        if lh in broken:
            raise OSError("OneLake said 503")
        tables, has_files = lakehouses[lh]
        if directory == "Tables":
            return [{"name": t, "is_dir": True} for t in tables]
        if directory == "Files":
            return [{"name": "x.csv", "is_dir": False}] if has_files else []
        return [{"name": "_delta_log", "is_dir": True}]

    def read(ws, lh, schema, table):
        h = (healths or {}).get(table)
        if isinstance(h, Exception):
            raise h
        return h or health()
    return xray.Scanner(Fabric(lakehouses), files, read, progress=progress or (lambda _p: None))


def test_a_scanner_reads_eight_at_a_time_by_default():
    assert scanner({}).workers == 8


def test_a_scan_reports_its_progress_from_zero_and_every_tenth_table():
    seen = []
    scan = scanner({"a": ([f"t{i:02d}" for i in range(19)], True)}, progress=seen.append)
    scan.workers = 1  # one reader, so the count goes up one table at a time
    scan.scan(NOW)
    assert seen == [{"phase": "Finding lakehouses", "done": 0, "total": 1},
                    {"phase": "Listing tables", "done": 0, "total": 1},
                    {"phase": "Reading table logs", "done": 0, "total": 19},
                    {"phase": "Reading table logs", "done": 10, "total": 19}]


def test_a_scan_reads_at_most_max_tables_and_says_when_it_stopped(monkeypatch):
    monkeypatch.setattr(xray, "MAX_TABLES", 3)
    exactly = scanner({"a": (["t1", "t2", "t3"], True)}).scan(NOW)
    assert exactly["tables"] == 3 and exactly["partial"] is False
    over = scanner({"a": (["t1", "t2", "t3", "t4"], True)}).scan(NOW)
    assert over["tables"] == 3 and over["partial"] is True


def test_each_lakehouse_row_adds_up_its_tables(monkeypatch):
    result = scanner({"a": (["t1", "t2"], True), "b": ([], True)},
                     healths={"t1": health(files=3, bytes=100), "t2": health(files=4, bytes=50)}).scan(NOW)
    rows = {r["lakehouse_id"]: r for r in result["lakehouse_rows"]}
    assert {k: rows["a"][k] for k in ("tables", "files", "bytes", "issues")} == {"tables": 2, "files": 7,
                                                                                 "bytes": 150, "issues": 0}
    assert {k: rows["b"][k] for k in ("tables", "files", "bytes", "issues")} == {"tables": 0, "files": 0,
                                                                                 "bytes": 0, "issues": 0}
    assert result["bytes"] == 150


def test_a_table_whose_log_cant_be_read_adds_nothing_and_isnt_called_unidentified():
    result = scanner({"a": (["t1"], True)}, healths={"t1": OSError("log missing")}).scan(NOW)
    [row] = result["lakehouse_rows"]
    assert (row["tables"], row["files"], row["bytes"], row["issues"]) == (1, 0, 0, 0)
    assert result["findings"] == []


def test_a_health_without_sizes_counts_as_none():
    result = scanner({"a": (["t1"], True)}, healths={"t1": {"exists": True, "advice": [],
                                                           "files": None, "bytes": None}}).scan(NOW)
    [row] = result["lakehouse_rows"]
    assert (row["files"], row["bytes"]) == (0, 0) and result["bytes"] == 0


def test_only_a_lakehouse_with_neither_tables_nor_files_is_empty():
    result = scanner({"tables-only": (["t1"], False), "files-only": ([], True), "nothing": ([], False)}).scan(NOW)
    assert [(f["kind"], f["lakehouse_id"]) for f in result["findings"]] == [("empty", "nothing")]


def test_a_lakehouse_that_cant_be_listed_is_skipped_not_called_empty():
    result = scanner({"broken": ([], False)}, broken={"broken"}).scan(NOW)
    assert result["findings"] == [] and result["skipped"] == ["Sales / BROKEN"]


# ---------------------------------------------------------------- what a scan shares


def test_shared_findings_leave_out_people_costs_and_findings_without_a_lakehouse():
    result = {"scanned": "2026-11-11T00:00:00+00:00", "lakehouse_rows": [], "findings": [
        finding("access", "Guest has access", "medium", workspace_id="ws", lakehouse_id="lh"),
        finding("cost", "nb cost", workspace_id="ws", lakehouse_id="lh"),
        finding("stale", "no lakehouse", workspace_id="ws"),
        finding("stale", "flat table", workspace_id="ws", lakehouse_id="lh", schema=None, table="events"),
        finding("empty", "an empty lakehouse", workspace_id="ws", lakehouse_id="lh")]}
    shared = xray.optimize_records(result)["findings"]
    assert [(f["title"], f["target"]) for f in shared] == [("flat table", "events"), ("an empty lakehouse", "")]
