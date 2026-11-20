"""VACUUM's retention at its edges, on every path that vacuums or sets a retention: maintenance.check_retention,
compact_local, vacuum_preview, set_retention and fabric_retention, a notebook's VACUUM t RETAIN n HOURS
(lakehouse.maintain_table behind meta_service's guard) and X-ray's Vacuum.

Time is never waited on: each commit is dated by rewriting its commitInfo timestamp and the deletionTimestamp
of each removal in it (as its writer's clock would have stamped them), and its file's mtime; a data file no
commit names is dated by its mtime. Where the engine reads the clock in Python, the clock is held still.
"""

import json
import math
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, maintenance, server
from fabric_desktop.meta_service import MetaMixin
from fabric_desktop.notebook import Kernel
from tests.test_maintenance_failure_paths import RUNNING, engine, recorded
from tests.test_spark_writer import lake  # noqa: F401 - a fixture
from tests.test_xray import _optimize_capacity, vacuum_svc  # noqa: F401 - a fixture

HOUR = 3600
DAY = 24 * HOUR
CUTOFF = maintenance.retention_hours(7) * HOUR  # 7 days and the hour of margin, in seconds

# Less than 7 days, as hours and as days: 167 hours, 167.99 hours, and 6 days 23:59.
SHORT_HOURS = [167, 167.99, 6 * 24 + 23 + 59 / 60]
SHORT_DAYS = [h / 24 for h in SHORT_HOURS]
NOT_A_LENGTH = [0, -1, -7, -0.5, math.nan, math.inf, -math.inf, "7", "seven", None, True, [7]]


# ---- dating a table's history

def _stamp(uri: str, version: int, seconds_ago: float, now: float | None = None) -> None:
    """Date commit ``version`` ``seconds_ago``, as its writer's clock would: its commitInfo, each of its
    removals, and the commit file's mtime. Negative is in the future (that writer's clock is ahead)."""
    commit = Path(uri) / "_delta_log" / f"{version:020d}.json"
    when = (time.time() if now is None else now) - seconds_ago
    ms = int(when * 1000)
    text = re.sub(r'"deletionTimestamp":\d+', f'"deletionTimestamp":{ms}', commit.read_text())
    commit.write_text(re.sub(r'"timestamp":\d+', f'"timestamp":{ms}', text))
    os.utime(commit, (when, when))


def _history(uri: str, ages: tuple[float, float, float, float], existing: bool = False) -> dict[int, str]:
    """Versions 0 to 3, each an overwrite (after the first) removing the file the one before wrote, and commit v
    dated ``ages[v]`` seconds ago. Each version holds one row, id = the version (version 0 keeps the table's own
    rows when ``existing``). Returns the data file each version names: version v's file is removed by v + 1."""
    for i in range(1 if existing else 0, 4):
        write_deltalake(uri, pa.table({"id": [i], "region": ["EU"]}), mode="overwrite")
    for v, age in enumerate(ages):
        _stamp(uri, v, age)
    named = {}
    for v in range(4):
        (u,) = DeltaTable(uri, version=v).file_uris()
        named[v] = u.rsplit("/", 1)[-1]
    return named


def _parquet(uri: str) -> set[str]:
    return {p.name for p in Path(uri).glob("*.parquet")}


def _ids(uri: str, version: int | None = None) -> list[int]:
    return sorted(r["id"] for r in DeltaTable(uri, version=version).to_pyarrow_table().to_pylist())


def _snapshot(uri: str) -> dict[str, tuple[int, int, bytes]]:
    """Every file of the table folder, log included, with its size, mtime and bytes."""
    return {p.relative_to(uri).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes())
            for p in Path(uri).rglob("*") if p.is_file()}


def _orphan(uri: str, mtime: float) -> Path:
    """A data file no commit names, as a write stopped before its commit leaves, last written at ``mtime``."""
    root = Path(uri)
    path = root / f"part-00000-{uuid.uuid4()}-c000.snappy.parquet"
    shutil.copy(next(root.glob("*.parquet")), path)
    os.utime(path, (mtime, mtime))
    return path


def _own(uri: str, value: str) -> None:
    """The table's own delta.deletedFileRetentionDuration."""
    DeltaTable(uri).alter.set_table_properties({"delta.deletedFileRetentionDuration": value})


def _config(uri: str) -> dict[str, str]:
    return DeltaTable(uri).metadata().configuration


@pytest.fixture
def table(tmp_path):
    """Version 0 thirty days ago and three overwrites after it, 20, 10 and 2 days ago."""
    uri = str(tmp_path / "t")
    return uri, _history(uri, (30 * DAY, 20 * DAY, 10 * DAY, 2 * DAY))


@pytest.fixture
def guarded(lake, tmp_path, monkeypatch):  # noqa: F811
    """dbo.sales of the attached lakehouse, behind meta_service's guard over a real audit log."""
    m = engine(tmp_path, RUNNING)
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: MetaMixin._maintenance_refusal(m, op, ws, t))
    return str(lake / "dbo" / "sales"), m


def _xray(svc, monkeypatch):
    """X-ray's Vacuum on vacuum_svc's table, outside any metadata project, on a running capacity."""
    from fabric_desktop.xray_service import NoMetadataProject

    def no_project():
        raise NoMetadataProject("none")
    monkeypatch.setattr(svc, "_wl_token", lambda _s: "token", raising=False)
    monkeypatch.setattr(svc, "_risk_capacity", lambda *a: _optimize_capacity("running"), raising=False)
    monkeypatch.setattr(svc, "_project", no_project)
    params = {"workspace_id": "ws-other", "lakehouse_id": "lh-silver", "table": "orders", "schema": "dbo",
              "lakehouse_name": "LH_X"}
    preview = server.handle(svc, json.dumps({"id": 1, "method": "xray_vacuum_preview", "params": params}))
    reply = server.handle(svc, json.dumps({"id": 2, "method": "xray_vacuum", "params": params}))
    assert "result" in preview and "result" in reply, (preview, reply)
    return preview["result"], reply["result"]


# ---- 1. seven days exactly is allowed; anything less, or not a length, is refused before anything changes

def test_seven_days_exactly_is_allowed_on_every_path(table, guarded):
    uri, _named = table
    for days in (7, 7.0):
        maintenance.check_retention(days)
        assert maintenance.fabric_retention(days) == "7.01:00:00"
        assert maintenance.retention_hours(days) == 169
    maintenance.check_retain_hours(168)
    maintenance.check_retain_hours(168.0)
    assert isinstance(maintenance.vacuum_preview(uri, 7), list)
    assert maintenance.set_retention(uri, 7) is True
    assert _config(uri)["delta.deletedFileRetentionDuration"] == "interval 7 days"
    assert maintenance.compact_local(uri, 7)["files_after"] == 1
    sales, m = guarded
    r = lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=168, dry_run=True)
    assert r.metrics["retentionHours"] == 168
    assert recorded(m) == [("started", "vacuum", "dbo.sales", "")]


@pytest.mark.parametrize("days", SHORT_DAYS + NOT_A_LENGTH, ids=repr)
def test_less_than_seven_days_or_not_a_number_is_refused_before_anything_changes(table, days):
    uri, _named = table
    before = _snapshot(uri)
    calls = {"check_retention": lambda: maintenance.check_retention(days),
             "fabric_retention": lambda: maintenance.fabric_retention(days),
             "set_retention": lambda: maintenance.set_retention(uri, days),
             "vacuum_preview": lambda: maintenance.vacuum_preview(uri, days),
             "compact_local": lambda: maintenance.compact_local(uri, days)}
    for name, call in calls.items():
        with pytest.raises(ValueError, match="at least 7 days") as said:
            call()
        assert "Nothing was changed" in str(said.value), name
        assert _snapshot(uri) == before, name  # no file deleted, no commit, no property written
    assert DeltaTable(uri).version() == 3 and _ids(uri, 0) == [0]


@pytest.mark.parametrize("days", [math.nan, math.inf, -1, -0.01, "0", None])
def test_a_value_that_isnt_a_length_is_refused_even_where_a_shorter_retention_is_allowed(table, days):
    """allow_shorter (tests that crash a writer use it) never lets through a negative or non-numeric value."""
    uri, _named = table
    before = _snapshot(uri)
    with pytest.raises(ValueError, match="at least 7 days"):
        maintenance.check_retention(days, allow_shorter=True)
    with pytest.raises(ValueError, match="at least 7 days"):
        maintenance.compact_local(uri, days, allow_shorter=True)
    assert _snapshot(uri) == before


@pytest.mark.parametrize("hours", SHORT_HOURS + [h for h in NOT_A_LENGTH if h is not None], ids=repr)
def test_notebook_vacuum_retain_less_than_168_hours_is_refused_before_the_guard_records_it(guarded, hours):
    """Refused before meta_service's guard writes its "started" line, and before the table is touched."""
    uri, m = guarded
    _history(uri, (30 * DAY, 20 * DAY, 10 * DAY, 2 * DAY), existing=True)
    before = _snapshot(uri)
    for dry_run in (True, False):
        with pytest.raises(ValueError, match="at least 7 days"):
            lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=hours, dry_run=dry_run)
    assert _snapshot(uri) == before
    assert recorded(m) == []  # nothing audited as started


@pytest.mark.parametrize("code", [
    "spark.sql('VACUUM dbo.sales RETAIN 167 HOURS')",
    "spark.sql('VACUUM dbo.sales RETAIN 167.99 HOURS DRY RUN')",
    "spark.sql('VACUUM dbo.sales RETAIN 0 HOURS')",
    "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'dbo.sales').vacuum(167.9833)",
    "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'dbo.sales').vacuum(float('nan'))",
    "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'dbo.sales').vacuum(float('inf'))",
    "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'dbo.sales').vacuum('a week')",
])
def test_a_notebook_cell_asking_for_less_is_refused_and_nothing_is_audited_as_started(guarded, code):
    uri, m = guarded
    _history(uri, (30 * DAY, 20 * DAY, 10 * DAY, 2 * DAY), existing=True)
    before = _snapshot(uri)
    out = Kernel().run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "error" and "at least 7 days" in text, text
    assert _snapshot(uri) == before and recorded(m) == []


def test_a_notebook_cell_asking_for_168_hours_runs(guarded):
    uri, m = guarded
    named = _history(uri, (30 * DAY, 20 * DAY, 10 * DAY, 2 * DAY), existing=True)
    out = Kernel().run("spark.sql('VACUUM dbo.sales RETAIN 168 HOURS')")
    assert out["status"] == "ok", out
    assert named[0] not in _parquet(uri) and named[2] in _parquet(uri)
    assert recorded(m) == [("started", "vacuum", "dbo.sales", "")]


# ---- 2. a removal at, just inside and just outside the cutoff

def test_compact_and_preview_keep_a_removal_just_inside_the_cutoff_and_delete_one_just_outside(tmp_path):
    """Keeping 7 days is 169 hours (an hour of margin): removed 169 hours and a minute ago goes; 169 hours less
    a minute ago, or exactly 7 days ago, stays."""
    uri = str(tmp_path / "t")
    named = _history(uri, (30 * DAY, CUTOFF + 60, CUTOFF - 60, 7 * DAY))
    assert maintenance.vacuum_preview(uri, 7) == [named[0]]  # removed by version 1, just outside
    before = _parquet(uri)
    result = maintenance.compact_local(uri, 7)
    assert before - _parquet(uri) == {named[0]} and result["vacuumed"] == 1
    assert _ids(uri, 1) == [1] and _ids(uri, 2) == [2]  # removed just inside, and exactly 7 days ago: kept
    with pytest.raises(FileNotFoundError):
        _ids(uri, 0)


def test_notebook_vacuum_retain_168_hours_keeps_just_inside_and_deletes_just_outside(guarded):
    uri, _m = guarded
    named = _history(uri, (30 * DAY, 168 * HOUR + 60, 168 * HOUR - 60, HOUR), existing=True)
    dry = lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=168, dry_run=True)
    assert dry.files == [named[0]] and dry.metrics["numDeletedFiles"] == 0
    real = lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=168)
    assert real.files == [named[0]] and named[0] not in _parquet(uri)
    assert _ids(uri, 1) == [1] and _ids(uri, 2) == [2]


def test_a_data_file_no_commit_names_goes_only_once_strictly_older_than_the_cutoff(table, monkeypatch):
    """The age of a file no commit names is read with Python's clock, held still here, so the cutoff itself is
    tested: a file exactly at it stays; a second older goes; a second newer stays."""
    uri, _named = table
    now = time.time()
    monkeypatch.setattr(maintenance, "time", SimpleNamespace(time=lambda: now))
    at = _orphan(uri, now - CUTOFF)
    inside = _orphan(uri, now - CUTOFF + 1)
    outside = _orphan(uri, now - CUTOFF - 1)
    result = maintenance.compact_local(uri, 7)
    assert at.exists() and inside.exists() and not outside.exists()
    assert result["unnamed_removed"] == 1


# ---- 3. a clock out of step never costs a version inside the retention its files

def test_a_commit_dated_before_the_one_ahead_of_it_doesnt_cost_that_version_its_files(table, guarded):
    """Version 0 was written 3 days ago; version 1's writer's clock was 20 days behind, so the removal of version
    0's file says 20 days ago. Version 0 is inside the 7 days kept, so its file stays, on every local path."""
    uri, _named = table
    named = _history(uri, (3 * DAY, 20 * DAY, 2 * DAY, DAY))
    assert maintenance.vacuum_preview(uri, 7) == []
    assert maintenance.compact_local(uri, 7)["vacuumed"] == 0
    assert named[0] in _parquet(uri) and _ids(uri, 0) == [0] and _ids(uri, 1) == [1]
    sales, _m = guarded
    _history(sales, (3 * DAY, 20 * DAY, 2 * DAY, DAY), existing=True)
    assert lakehouse.maintain_table("dbo.sales", "vacuum", dry_run=True).files == []
    assert lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=168).files == []
    assert _ids(sales, 0) == [1, 2, 3]


def test_a_commit_dated_in_the_future_keeps_what_the_versions_after_it_remove(tmp_path, guarded):
    """Version 1's writer's clock is 5 days ahead; version 2's is 20 days behind. Version 1 is inside the
    retention whatever the order of the dates, so its file stays; version 0's removal is dated in the future."""
    uri = str(tmp_path / "t")
    named = _history(uri, (30 * DAY, -5 * DAY, 20 * DAY, DAY))
    assert maintenance.vacuum_preview(uri, 7) == []
    assert maintenance.compact_local(uri, 7)["vacuumed"] == 0
    assert {named[0], named[1], named[2]} <= _parquet(uri)
    assert [_ids(uri, v) for v in range(4)] == [[0], [1], [2], [3]]
    sales, _m = guarded
    _history(sales, (30 * DAY, -5 * DAY, 20 * DAY, DAY), existing=True)
    assert lakehouse.maintain_table("dbo.sales", "vacuum").files == []
    assert _ids(sales, 1) == [1]


def test_a_future_dated_latest_commit_doesnt_stop_vacuum_deleting_what_is_truly_past_the_retention(tmp_path):
    """Dates that rise with the versions are left to VACUUM as they are: only the future removal is kept."""
    uri = str(tmp_path / "t")
    named = _history(uri, (30 * DAY, 20 * DAY, 10 * DAY, -2 * DAY))
    assert sorted(maintenance.vacuum_preview(uri, 7)) == sorted([named[0], named[1]])
    assert maintenance.compact_local(uri, 7)["vacuumed"] == 2
    assert _ids(uri, 2) == [2] and _ids(uri) == [3]


def test_versions_out_of_order_but_all_past_the_retention_still_go(tmp_path):
    uri = str(tmp_path / "t")
    named = _history(uri, (40 * DAY, 20 * DAY, 30 * DAY, 25 * DAY))
    assert sorted(maintenance.vacuum_preview(uri, 7)) == sorted([named[0], named[1], named[2]])
    assert maintenance.skew_safe_days(DeltaTable(uri), 7) == 7


def test_the_versions_kept_for_a_clock_out_of_step(tmp_path):
    uri = str(tmp_path / "t")
    _history(uri, (3 * DAY, 20 * DAY, 2 * DAY, DAY))
    keep, oldest = maintenance.skewed_versions(DeltaTable(uri), 169)
    assert keep == [0] and abs(oldest / 1000 - (time.time() - 20 * DAY)) < 60
    _history(uri, (30 * DAY, 20 * DAY, 10 * DAY, 2 * DAY))  # dates rising again
    assert maintenance.skewed_versions(DeltaTable(uri), 169) == ([], None)


def test_xray_vacuum_keeps_enough_days_for_a_commit_dated_out_of_step(vacuum_svc, monkeypatch):  # noqa: F811
    """Fabric's job keeps by days only, so X-ray keeps enough that the removal dated 40 days ago (by a writer
    40 days behind, after a commit 2 days ago) isn't deleted. The preview, which is a dry run, agrees."""
    svc, uri, jobs = vacuum_svc
    for v, age in enumerate((2 * DAY, 40 * DAY, DAY)):
        _stamp(str(uri), v, age)
    preview, reply = _xray(svc, monkeypatch)
    assert preview["files"] == 0 and preview["keep_days"] == reply["keep_days"] == 41
    assert "clock out of step" in reply["why"]
    assert jobs[-1][1]["vacuum_retention"] == "41.01:00:00"
    assert _ids(str(uri), 0) == [0]


def test_xray_vacuum_keeps_seven_days_when_the_dates_rise(vacuum_svc, monkeypatch):  # noqa: F811
    svc, uri, jobs = vacuum_svc
    for v, age in enumerate((50 * DAY, 40 * DAY, DAY)):
        _stamp(str(uri), v, age)
    preview, reply = _xray(svc, monkeypatch)
    assert preview["files"] == 1 and reply["keep_days"] == 7 and reply["why"] == "Fabric's default"
    assert jobs[-1][1]["vacuum_retention"] == "7.01:00:00"


@pytest.mark.parametrize("age_days", [400, 20_000])  # past the bound; 20,000 is about a commit dated 1970
def test_xray_refuses_a_commit_dated_so_far_out_of_step_that_vacuum_would_remove_nothing(vacuum_svc, monkeypatch,  # noqa: F811
                                                                                         age_days):
    """A removal dated more than MAX_SKEW_DAYS ago by a clock out of step would make Fabric's VACUUM keep that
    many days, in effect removing nothing. X-ray refuses and says why, and submits no job."""
    from fabric_desktop.xray_service import NoMetadataProject
    svc, uri, jobs = vacuum_svc
    for v, age in enumerate((2 * DAY, age_days * DAY, DAY)):
        _stamp(str(uri), v, age)
    monkeypatch.setattr(svc, "_wl_token", lambda _s: "token", raising=False)
    monkeypatch.setattr(svc, "_risk_capacity", lambda *a: _optimize_capacity("running"), raising=False)
    monkeypatch.setattr(svc, "_project", lambda: (_ for _ in ()).throw(NoMetadataProject("none")))
    params = {"workspace_id": "ws-other", "lakehouse_id": "lh-silver", "table": "orders", "schema": "dbo",
              "lakehouse_name": "LH_X"}
    before = len(jobs)
    for i, method in enumerate(("xray_vacuum_preview", "xray_vacuum")):
        reply = server.handle(svc, json.dumps({"id": i, "method": method, "params": params}))
        assert "error" in reply, reply
        msg = reply["error"] if isinstance(reply["error"], str) else reply["error"].get("message", "")
        assert "so it would remove nothing" in msg and "out of step" in msg and "Nothing was started" in msg, msg
    assert len(jobs) == before and _ids(str(uri), 0) == [0]


def test_the_bound_is_a_year_and_a_skew_inside_it_still_keeps_enough_days(vacuum_svc):  # noqa: F811
    svc, uri, _jobs = vacuum_svc
    assert maintenance.MAX_SKEW_DAYS == 365
    for v, age in enumerate((2 * DAY, 300 * DAY, DAY)):  # skewed, but inside the bound
        _stamp(str(uri), v, age)
    safe, why = svc._xray_skew_days(str(uri), {}, 7, "Fabric's default")
    assert 300 <= safe <= maintenance.MAX_SKEW_DAYS and "out of step" in why


@pytest.mark.parametrize("days", [400, 3650])
def test_a_long_retention_with_dates_in_order_is_kept_not_refused(vacuum_svc, days):  # noqa: F811
    """A table's own retention (or a layer's Keep history) past the bound, with no clock skew, is not the skew
    the bound is for: Optimize, its check, Vacuum and its preview keep those days as asked."""
    svc, uri, _jobs = vacuum_svc
    for v, age in enumerate((50 * DAY, 40 * DAY, DAY)):
        _stamp(str(uri), v, age)
    assert maintenance.skew_safe_days(DeltaTable(str(uri)), days) == days
    assert svc._xray_skew_days(str(uri), {}, days, "the table's own setting") == (days, "the table's own setting")


def test_a_skew_past_the_bound_is_refused_even_when_the_asked_days_are_long(vacuum_svc):  # noqa: F811
    svc, uri, _jobs = vacuum_svc
    for v, age in enumerate((2 * DAY, 900 * DAY, DAY)):
        _stamp(str(uri), v, age)
    with pytest.raises(RuntimeError, match="so it would remove nothing"):
        svc._xray_skew_days(str(uri), {}, 400, "the table's own setting")


# ---- 4. the cutoff is UTC: the same whatever the PC's time zone

@pytest.fixture(params=["America/Chicago", "Asia/Kolkata", "UTC"])
def zone(request):
    """The PC's time zone set to ``request.param`` (TZ, and time.tzset where there is one), then put back."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is POSIX only")
    old = os.environ.get("TZ")
    os.environ["TZ"] = request.param
    time.tzset()
    offset = {"America/Chicago": -6 * HOUR, "Asia/Kolkata": 5 * HOUR + 30 * 60, "UTC": 0}[request.param]
    if time.localtime(datetime(2026, 11, 11, tzinfo=timezone.utc).timestamp()).tm_gmtoff != offset:
        pytest.skip(f"no time zone data for {request.param}")
    yield request.param
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def test_the_cutoff_is_the_same_in_every_time_zone(tmp_path, zone, guarded):
    uri = str(tmp_path / "t")
    named = _history(uri, (30 * DAY, CUTOFF + 60, CUTOFF - 60, 7 * DAY))
    _orphan(uri, time.time() - CUTOFF - 120)
    kept = _orphan(uri, time.time() - CUTOFF + 120)
    assert maintenance.vacuum_preview(uri, 7) == [named[0]]
    result = maintenance.compact_local(uri, 7)
    assert (result["vacuumed"], result["unnamed_removed"]) == (1, 1) and kept.exists()
    assert maintenance.fabric_retention(30) == "30.01:00:00"
    sales, _m = guarded
    named = _history(sales, (30 * DAY, 168 * HOUR + 60, 168 * HOUR - 60, HOUR), existing=True)
    assert lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=168).files == [named[0]]
    skewed = str(tmp_path / "skew")
    _history(skewed, (3 * DAY, 20 * DAY, 2 * DAY, DAY))
    assert maintenance.skewed_versions(DeltaTable(skewed), 169)[0] == [0]
    assert maintenance.skew_safe_days(DeltaTable(skewed), 7) == 21


RESTORE_AT = "2026-11-11 04:00:00"  # UTC: version 1. Read as Chicago's time it would be version 2, as Kolkata's 0.


@pytest.fixture
def dated_sales(guarded):
    """dbo.sales with versions 0 to 3 committed at fixed UTC times around RESTORE_AT."""
    uri, m = guarded
    for i in (4, 5, 6):
        write_deltalake(uri, pa.table({"id": [i], "region": ["EU"]}), mode="append")
    for v, when in enumerate(("2026-11-10T12:00:00", "2026-11-11T02:00:00", "2026-11-11T08:00:00",
                              "2026-11-12T00:00:00")):
        utc = datetime.fromisoformat(when).replace(tzinfo=timezone.utc).timestamp()
        _stamp(uri, v, 0, now=utc)
    return uri, m


@pytest.mark.parametrize("code", [
    f"spark.sql(\"RESTORE TABLE dbo.sales TO TIMESTAMP AS OF '{RESTORE_AT}'\")",
    f"from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'dbo.sales').restoreToTimestamp('{RESTORE_AT}')",
    ("import datetime\nfrom delta.tables import DeltaTable\n"
     "DeltaTable.forName(spark, 'dbo.sales').restoreToTimestamp(datetime.datetime(2026, 11, 11, 4))"),
    ("import datetime\nfrom delta.tables import DeltaTable\nist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))\n"
     "DeltaTable.forName(spark, 'dbo.sales').restoreToTimestamp(datetime.datetime(2026, 11, 11, 9, 30, tzinfo=ist))"),
])
def test_restore_timestamp_as_of_reads_a_time_without_a_zone_as_utc_in_every_time_zone(dated_sales, zone, code):
    """As Fabric's Spark, whose session time zone is UTC: a time without a zone is UTC, never the PC's local
    time, and one with a zone is that instant."""
    uri, _m = dated_sales
    out = Kernel().run(code)
    assert out["status"] == "ok", out
    assert _ids(uri) == [1, 2, 3, 4]  # version 1's rows


def test_maintain_table_never_reads_a_naive_datetime_as_the_pcs_local_time(dated_sales, zone):
    """Called directly with a datetime that has no zone, RESTORE either reads it as UTC or refuses it, never
    as the PC's local time."""
    uri, _m = dated_sales
    try:
        lakehouse.maintain_table("dbo.sales", "restore", timestamp=datetime(2026, 11, 11, 4))
    except ValueError as exc:
        assert "Nothing was changed" in str(exc)
        assert _ids(uri) == [1, 2, 3, 4, 5, 6] and DeltaTable(uri).version() == 3
    else:
        assert _ids(uri) == [1, 2, 3, 4]


# ---- 5. a table's own longer retention wins; a shorter one never goes below 7 days

def test_compact_and_preview_keep_the_tables_own_longer_retention(tmp_path):
    uri = str(tmp_path / "t")
    named = _history(uri, (50 * DAY, 40 * DAY, 10 * DAY, 2 * DAY))
    _own(uri, "interval 30 days")
    assert maintenance.vacuum_preview(uri, 7) == [named[0]]  # removed 40 days ago; 10 days ago is kept
    before = _parquet(uri)
    result = maintenance.compact_local(uri, 7)
    assert before - _parquet(uri) == {named[0]} and result["vacuumed"] == 1
    assert _ids(uri, 1) == [1] and _ids(uri, 2) == [2]
    assert _config(uri)["delta.deletedFileRetentionDuration"] == "interval 30 days"  # not lowered to 7


@pytest.mark.parametrize("own", ["interval 1 days", "interval 0 days", "interval 1 hours", "interval 167 hours",
                                 "interval 6 days"])
def test_a_tables_own_shorter_retention_is_never_honoured_below_seven_days(tmp_path, own):
    uri = str(tmp_path / "t")
    named = _history(uri, (50 * DAY, 40 * DAY, 10 * DAY, 2 * DAY))
    _own(uri, own)
    assert sorted(maintenance.vacuum_preview(uri, 7)) == sorted([named[0], named[1]])
    maintenance.compact_local(uri, 7)
    assert _ids(uri, 2) == [2]  # removed 2 days ago: kept
    assert _config(uri)["delta.deletedFileRetentionDuration"] == "interval 7 days"


def test_set_retention_raises_a_shorter_retention_and_refuses_to_go_below_seven_days(tmp_path):
    uri = str(tmp_path / "t")
    write_deltalake(uri, pa.table({"id": [1]}))
    _own(uri, "interval 1 days")
    with pytest.raises(ValueError, match="at least 7 days"):
        maintenance.set_retention(uri, 6)
    assert _config(uri)["delta.deletedFileRetentionDuration"] == "interval 1 days"  # refused before the change
    assert maintenance.set_retention(uri, 7) is True
    assert _config(uri) == maintenance.retention_properties(7)
    assert maintenance.set_retention(uri, 30) is True
    assert _config(uri)["delta.deletedFileRetentionDuration"] == "interval 30 days"
    assert maintenance.fabric_retention(30) == "30.01:00:00"


def test_notebook_vacuum_keeps_the_tables_own_longer_retention_and_never_less_than_seven_days(guarded):
    uri, _m = guarded
    named = _history(uri, (50 * DAY, 40 * DAY, 10 * DAY, 2 * DAY), existing=True)
    _own(uri, "interval 30 days")
    dry = lakehouse.maintain_table("dbo.sales", "vacuum", dry_run=True)
    assert dry.metrics["retentionHours"] == maintenance.retention_hours(30) and dry.files == [named[0]]
    with pytest.raises(ValueError, match="keeps 30 days"):
        lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=168)
    _own(uri, "interval 1 days")
    dry = lakehouse.maintain_table("dbo.sales", "vacuum", dry_run=True)
    assert dry.metrics["retentionHours"] == 169 and sorted(dry.files) == sorted([named[0], named[1]])
    with pytest.raises(ValueError, match="at least 7 days"):
        lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=24)  # the table's 1 day doesn't allow it
    assert sorted(lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=168).files) == sorted(
        [named[0], named[1]])
    assert _ids(uri, 2) == [2]


@pytest.mark.parametrize("own, days", [("interval 45 days", 45), ("interval 1 days", 7), ("interval 0 days", 7),
                                       ("interval 167 hours", 7)])
def test_xray_vacuum_keeps_the_tables_own_longer_retention_and_never_less_than_seven_days(vacuum_svc, monkeypatch,
                                                                                          own, days):  # noqa: F811
    svc, uri, jobs = vacuum_svc
    _own(str(uri), own)
    _preview, reply = _xray(svc, monkeypatch)
    assert reply["keep_days"] == days
    assert reply["why"] == ("the table's own retention setting" if days > 7 else "Fabric's default")
    assert jobs[-1][1]["vacuum_retention"] == f"{days}.01:00:00"
