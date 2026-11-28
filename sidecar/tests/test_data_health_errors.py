"""Data health when something is wrong: a Delta log cut short, cleaned up behind a checkpoint or
holding no load yet; schemas retyped, renamed, or dropped and added again; a saved list whose fields
have the wrong shape; times written without a zone; and a disk that refuses the save.

Every Delta log here is a few small JSON commits on tmp_path (or delta-rs's own, for a checkpoint), so
nothing reaches Fabric or OneLake and nothing waits."""

import datetime as dt
import errno
import json
import os
import time
from types import SimpleNamespace

import polars as pl
import pytest
from deltalake import DeltaTable

from fabric_desktop import dataops_service as ds
from fabric_desktop import jsonfile
from fabric_desktop.meta import data_health as dh
from fabric_desktop.service import Service

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 17, 12, tzinfo=UTC)
ENTITY = "silver.orders"


# ---- helpers


def _field(name, typ, column_id=None):
    """One column as a Delta schema holds it; with ``column_id`` the table maps columns by name, as
    Fabric does once a column has been renamed or dropped."""
    meta = {}
    if column_id is not None:
        meta = {
            "delta.columnMapping.id": column_id,
            "delta.columnMapping.physicalName": f"col-{column_id}",
        }
    return {"name": name, "type": typ, "nullable": True, "metadata": meta}


def _write_log(root, commits, mapping=False):
    """A Delta log as Fabric's Spark writes it: ``commits`` are (operation, columns or None, rows or
    None), one an hour apart, the last an hour before NOW. Columns start a new schema at that version;
    rows are the commit's numOutputRows, as a string, as Spark records it."""
    log = root / "_delta_log"
    log.mkdir(parents=True)
    first = NOW - dt.timedelta(hours=len(commits))
    for v, (op, fields, rows) in enumerate(commits):
        lines = []
        if v == 0:
            lines.append(
                {
                    "protocol": {
                        "minReaderVersion": 2 if mapping else 1,
                        "minWriterVersion": 5 if mapping else 2,
                    }
                }
            )
        if fields is not None:
            conf = (
                {
                    "delta.columnMapping.mode": "name",
                    "delta.columnMapping.maxColumnId": "10",
                }
                if mapping
                else {}
            )
            lines.append(
                {
                    "metaData": {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "format": {"provider": "parquet", "options": {}},
                        "schemaString": json.dumps(
                            {"type": "struct", "fields": fields}
                        ),
                        "partitionColumns": [],
                        "configuration": conf,
                        "createdTime": int(first.timestamp() * 1000),
                    }
                }
            )
        when = first + dt.timedelta(hours=v)
        lines.append(
            {
                "commitInfo": {
                    "timestamp": int(when.timestamp() * 1000),
                    "operation": op,
                    "operationParameters": {"mode": "Append"},
                    "operationMetrics": {}
                    if rows is None
                    else {"numOutputRows": str(rows)},
                }
            }
        )
        (log / f"{v:020}.json").write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
        )
    return str(root)


def _check(uri, within_hours=None):
    return dh.check(lambda v: DeltaTable(uri, version=v), within_hours, now=NOW)


class _Project:
    """A metadata project with one entity whose table is at ``uri`` (no metadata files on disk)."""

    def __init__(self, uri, spec=None):
        self.entities = {
            ENTITY: SimpleNamespace(
                layer="silver", schema="dbo", table="orders", spec=spec or {}
            )
        }
        self.store = SimpleNamespace(
            uri=lambda lakehouse, schema, table: uri, options=lambda: {}
        )

    def layer(self, name):
        return SimpleNamespace(lakehouse="lh_silver")


@pytest.fixture
def app(tmp_path, monkeypatch):
    """The service with a one-entity project pointing at ``tmp_path / "t"``; its Delta log is up to each test."""
    project = _Project(str(tmp_path / "t"))
    s = Service(root=tmp_path / "app")
    monkeypatch.setattr(type(s), "_project", lambda self: project)
    monkeypatch.setattr(type(s), "_store", lambda self, p, env, create=False: p.store)
    s.table_dir = tmp_path / "t"
    return s


@pytest.fixture
def far_from_utc():
    """This PC's clock fourteen hours ahead of UTC, so a time read as local is half a day out."""
    if not hasattr(time, "tzset"):
        pytest.skip(
            "no time.tzset on this OS (Windows): the local zone can't be pinned"
        )
    before = os.environ.get("TZ")
    os.environ["TZ"] = "XYZ-14"
    time.tzset()
    try:
        assert dt.datetime.now().astimezone().utcoffset() == dt.timedelta(hours=14)
        yield
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


# ---- the Delta log: cleaned up, cut short, or holding no load yet


def test_a_log_cleaned_up_behind_a_checkpoint_still_reports_what_it_can(tmp_path):
    # Delta's log retention deletes old JSON commits once a checkpoint covers them; only the newest is left.
    uri = str(tmp_path / "t")
    for _ in range(12):
        pl.DataFrame({"a": list(range(100))}).write_delta(uri, mode="append")
    DeltaTable(uri).create_checkpoint()
    for commit in (tmp_path / "t" / "_delta_log").glob("*.json"):
        if int(commit.stem) < 11:
            commit.unlink()
    out = dh.check(lambda v: DeltaTable(uri, version=v), within_hours=24)
    assert out["version"] == 11
    assert (
        out["freshness"]["state"] == "fresh" and out["freshness"]["last_version"] == 11
    )
    assert out["volume"]["state"] == "not_enough_history" and [
        x["version"] for x in out["volume"]["loads"]
    ] == [11]
    # The version before the load is behind the checkpoint: unread, said plainly, with no raw delta-rs text.
    assert out["schema"]["state"] == "unread" and out["schema"]["since_version"] == 10
    assert (
        "version 10" in out["schema"]["why"]
        and "kernel" not in out["schema"]["why"].lower()
    )
    assert out["alerts"] == []


def test_a_commit_cut_short_is_an_error_never_a_healthy_table(app):
    uri = _write_log(
        app.table_dir,
        [("WRITE", [_field("id", "long")], 100)] + [("WRITE", None, 100)] * 2,
    )
    last = app.table_dir / "_delta_log" / f"{2:020}.json"
    # A crash part-way through the newest commit.
    last.write_text(last.read_text(encoding="utf-8")[:40], encoding="utf-8")
    with pytest.raises(Exception):  # noqa: B017 - delta-rs's own error type; what matters is it isn't swallowed
        _check(uri)
    app.meta_data_health_watch(ENTITY, "dev")
    [t] = app.meta_data_health_list("dev")["tables"]
    assert t["state"] == "error" and t["entity"] == ENTITY and t["why"]
    assert "\n" not in t["why"] and "freshness" not in t and "alerts" not in t


def test_a_log_folder_with_no_commit_yet_is_no_table(app):
    # The log folder is there but the first commit isn't written yet.
    (app.table_dir / "_delta_log").mkdir(parents=True)
    out = app.meta_data_health(ENTITY, "dev")
    assert (
        out["state"] == "no_table" and ENTITY in out["why"] and "freshness" not in out
    )


def test_a_table_created_but_never_loaded_is_unread_not_fresh_and_raises_nothing(app):
    _write_log(app.table_dir, [("CREATE TABLE", [_field("id", "long")], None)])
    out = _check(str(app.table_dir), within_hours=24)
    assert (
        out["freshness"]["state"] == "unread" and out["freshness"]["age_hours"] is None
    )
    assert out["volume"]["state"] == "no_loads" and out["volume"]["unread"] == 0
    assert out["schema"] == {"state": "no_earlier_version"} and out["alerts"] == []


def test_only_maintenance_in_the_log_window_is_unread_not_fresh(tmp_path):
    uri = _write_log(
        tmp_path / "t",
        [("WRITE", [_field("id", "long")], 5)] + [("OPTIMIZE", None, None)] * 4,
    )

    class Window:  # the log read returns only the newest commits, none of them a change of data
        def __init__(self, v):
            self.t = DeltaTable(uri, version=v)

        def history(self, n):
            return [e for e in self.t.history(n) if e.get("version") != 0]

        def version(self):
            return self.t.version()

        def schema(self):
            return self.t.schema()

    out = dh.check(lambda v: Window(v), within_hours=24, now=NOW)
    assert (
        out["freshness"]["state"] == "unread"
        and "changed data" in out["freshness"]["why"]
    )
    assert out["volume"]["state"] == "no_loads" and out["alerts"] == []


# ---- schema changes, from real logs


def test_a_column_retyped_between_loads_is_named_with_both_types(tmp_path):
    uri = _write_log(
        tmp_path / "t",
        [
            ("WRITE", [_field("id", "long"), _field("amount", "integer")], 5),
            ("WRITE", None, 5),
            ("WRITE", [_field("id", "long"), _field("amount", "string")], 5),
        ],
    )
    s = _check(uri)["schema"]
    assert s["state"] == "changed" and s["since_version"] == 1 and s["version"] == 2
    assert (
        s["retyped"] == [{"name": "amount", "from": "integer", "to": "string"}]
        and not s["added"]
        and not s["removed"]
    )


def test_a_renamed_column_shows_as_one_added_and_one_removed(tmp_path):
    uri = _write_log(
        tmp_path / "t",
        [
            ("WRITE", [_field("id", "long", 1), _field("cust", "string", 2)], 5),
            (
                "RENAME COLUMN",
                [_field("id", "long", 1), _field("customer", "string", 2)],
                None,
            ),
            ("WRITE", None, 5),
        ],
        mapping=True,
    )
    out = _check(uri)
    assert out["schema"]["added"] == ["customer"] and out["schema"]["removed"] == [
        "cust"
    ]
    assert [a["title"] for a in out["alerts"]] == [
        "Schema changed: 1 column added, 1 removed"
    ]
    # The rename itself isn't a change of data.
    assert out["freshness"]["last_version"] == 2


def test_a_column_dropped_and_added_again_as_another_type_is_retyped(tmp_path):
    uri = _write_log(
        tmp_path / "t",
        [
            ("WRITE", [_field("id", "long", 1), _field("x", "string", 2)], 5),
            ("DROP COLUMNS", [_field("id", "long", 1)], None),
            ("ADD COLUMNS", [_field("id", "long", 1), _field("x", "integer", 3)], None),
            ("WRITE", None, 5),
        ],
        mapping=True,
    )
    s = _check(uri)["schema"]
    assert s["state"] == "changed" and s["since_version"] == 0
    assert s["retyped"] == [{"name": "x", "from": "string", "to": "integer"}]


def test_a_field_retyped_inside_a_struct_is_a_change_of_its_column(tmp_path):
    def struct(t):
        return {"type": "struct", "fields": [_field("zip", t)]}

    uri = _write_log(
        tmp_path / "t",
        [
            ("WRITE", [_field("id", "long"), _field("address", struct("integer"))], 5),
            ("WRITE", [_field("id", "long"), _field("address", struct("string"))], 5),
        ],
    )
    [retyped] = _check(uri)["schema"]["retyped"]
    assert (
        retyped["name"] == "address"
        and json.loads(retyped["to"])["fields"][0]["type"] == "string"
    )


# ---- the saved list: fields of the wrong shape


@pytest.mark.parametrize(
    "state",
    [
        {"watched": {"entity": ENTITY, "env": "dev"}},
        {"watched": ENTITY},
        {"quiet": []},
        {
            "watched": [{"entity": ENTITY, "env": "dev"}],
            "quiet": {f"dev|{ENTITY}|volume|3": {"until": 5}},
        },
    ],
)
def test_a_list_with_odd_but_iterable_fields_still_lists_and_saves(
    app, monkeypatch, state
):
    monkeypatch.setattr(
        app,
        "meta_data_health",
        lambda name, env: {
            "entity": name,
            "env": env,
            "alerts": [{"kind": "volume", "version": 3, "title": "x"}],
        },
    )
    app.root.mkdir(parents=True)
    (app.root / "data_health.json").write_text(json.dumps(state), encoding="utf-8")
    out = app.meta_data_health_list("dev")
    # A watched field that isn't a list, or a quiet field that isn't a map, is set aside;
    # an empty one is just empty.
    shaped = isinstance(state.get("watched") or [], list) and isinstance(
        state.get("quiet") or {}, dict
    )
    assert ("reset" in out) is not shaped
    for t in out["tables"]:
        # A snooze whose end isn't a time doesn't hide the alert.
        assert "quiet" not in t["alerts"][0]
    app.meta_data_health_watch(ENTITY, "dev")
    assert [t["entity"] for t in app.meta_data_health_list("dev")["tables"]] == [ENTITY]


@pytest.mark.parametrize(
    "state",
    [
        {"watched": 5},
        {"watched": True},
        {"quiet": "acknowledged"},
        {"quiet": [[f"dev|{ENTITY}|volume|3", {}]]},
        {"watched": [{"entity": [ENTITY], "env": "dev"}]},
    ],
)
def test_a_list_with_fields_of_the_wrong_type_starts_again_instead_of_failing(
    app, monkeypatch, state
):
    monkeypatch.setattr(
        app,
        "meta_data_health",
        lambda name, env: {"entity": name, "env": env, "alerts": []},
    )
    app.root.mkdir(parents=True)
    (app.root / "data_health.json").write_text(json.dumps(state), encoding="utf-8")
    app.meta_data_health_list("dev")
    app.meta_data_health_watch(ENTITY, "dev")
    assert [t["entity"] for t in app.meta_data_health_list("dev")["tables"]] == [ENTITY]


# ---- times without a zone are UTC, whatever this PC's zone


def test_a_snooze_end_without_a_zone_is_read_as_utc_not_local(
    app, monkeypatch, far_from_utc
):
    monkeypatch.setattr(
        app,
        "meta_data_health",
        lambda name, env: {
            "entity": name,
            "env": env,
            "alerts": [{"kind": "volume", "version": 3, "title": "x"}],
        },
    )
    app.meta_data_health_watch(ENTITY, "dev")
    now = dt.datetime.now(UTC).replace(tzinfo=None)

    def quiet_until(until):
        state = app._dh_read()
        state["quiet"] = {
            f"dev|{ENTITY}|volume|3": {
                "at": now.isoformat(),
                "until": until.isoformat(),
            }
        }
        app._dh_write(state)
        return "quiet" in app.meta_data_health_list("dev")["tables"][0]["alerts"][0]

    # Read as local (UTC+14) these would be 12 hours ago and 14 hours on: the opposite answers.
    assert quiet_until(now + dt.timedelta(hours=2))
    assert not quiet_until(now - dt.timedelta(hours=2))


def test_a_reset_note_without_a_zone_is_dated_in_utc(app, far_from_utc):
    def said(at):
        app._dh_write(
            {"reset": {"at": at.isoformat(), "copy": "data_health.json.corrupt-1"}}
        )
        return "reset" in app._dh_read()

    week = dt.timedelta(days=ds.DATA_HEALTH_RESET_DAYS)
    now = dt.datetime.now(UTC).replace(tzinfo=None)
    assert said(now - week + dt.timedelta(hours=2))  # as local, 12 hours past the week
    assert not said(now - week - dt.timedelta(hours=2))


# ---- a disk that refuses the save


@pytest.mark.parametrize(
    "refusal",
    [
        OSError(errno.ENOSPC, "No space left on device"),
        PermissionError(errno.EACCES, "Access is denied"),
    ],
)
def test_a_save_the_disk_refuses_says_so_and_leaves_the_last_saved_list(
    app, monkeypatch, refusal
):
    app.meta_data_health_watch(ENTITY, "dev")
    path = app.root / "data_health.json"
    before, real = path.read_bytes(), os.fsync

    def refuse(fd):
        raise refusal

    monkeypatch.setattr(jsonfile.os, "fsync", refuse)
    with pytest.raises(OSError):
        app.meta_data_health_quiet(ENTITY, "dev", "volume", 3)
    with pytest.raises(OSError):
        app.meta_data_health_watch(ENTITY, "dev", watch=False)
    monkeypatch.setattr(jsonfile.os, "fsync", real)
    assert path.read_bytes() == before and not list(
        app.root.glob(".data_health.json.*.tmp")
    )
    assert app._dh_read()["quiet"] == {} and [
        w["entity"] for w in app._dh_read()["watched"]
    ] == [ENTITY]


def test_a_damaged_list_whose_fresh_start_cant_be_saved_is_refused_not_shown_empty(
    app, monkeypatch
):
    app.meta_data_health_watch(ENTITY, "dev")
    (app.root / "data_health.json").write_text("{cut", encoding="utf-8")

    def full(path, data, indent=2):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(jsonfile, "write_json", full)
    with pytest.raises(OSError):
        app.meta_data_health_list("dev")
    # What was there is kept.
    assert len(list(app.root.glob("data_health.json.corrupt-*"))) == 1


def test_a_damaged_list_whose_fresh_start_cant_be_saved_still_says_so_once_the_disk_has_room(
    app, monkeypatch
):
    app.meta_data_health_watch(ENTITY, "dev")
    (app.root / "data_health.json").write_text("{cut", encoding="utf-8")
    real = jsonfile.write_json

    def full(path, data, indent=2):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(jsonfile, "write_json", full)
    with pytest.raises(OSError):
        app.meta_data_health_list("dev")
    monkeypatch.setattr(jsonfile, "write_json", real)
    out = app.meta_data_health_list("dev")
    assert "reset" in out and out["reset"]["copy"].startswith(
        "data_health.json.corrupt-"
    )


def test_a_list_whose_tidy_up_cant_be_saved_still_shows_its_checks(app, monkeypatch):
    monkeypatch.setattr(
        app,
        "meta_data_health",
        lambda name, env: {
            "entity": name,
            "env": env,
            "alerts": [
                {
                    "kind": "freshness",
                    "version": 6,
                    "title": "Not updated for 30 h (promise 24 h)",
                }
            ],
        },
    )
    app.meta_data_health_watch(ENTITY, "dev")
    # For an older version, so the next list forgets it and has to save.
    app.meta_data_health_quiet(ENTITY, "dev", "freshness", 5)

    def full(path, data, indent=2):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(jsonfile, "write_json", full)
    [t] = app.meta_data_health_list("dev")["tables"]
    assert t["alerts"][0]["kind"] == "freshness"
