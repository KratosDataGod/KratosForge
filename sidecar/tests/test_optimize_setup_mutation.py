"""The central lakehouse's setup (optimize/setup.py): what mutation testing (batch 87,
docs/quality/mutation-testing.md) showed no test pinned. A text column where a number belongs is a
conflict, a schema_version table with no rows reads as no versions, retry gives up after five tries,
an upgrade records only the versions it adds and audits itself as an upgrade, a 401 or 403 means no
permission whatever the message, and other errors while registering are raised. A local lakehouse
folder and fakes only: nothing reaches Fabric or OneLake."""

from __future__ import annotations

from types import SimpleNamespace

import polars as pl
import pytest

from fabric_desktop.optimize import schema, setup
from fabric_desktop.optimize.installation import Installation
from fabric_desktop.optimize.lakehouse import LocalLakehouse
from tests.test_optimize_setup import NEXT, V2


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata-env"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def lake(tmp_path):
    return LocalLakehouse(tmp_path / "lakehouse")


def pc(tmp_path, name="pc1"):
    return Installation(tmp_path / name / "optimize")


# ---- what inspect reads


def test_a_text_column_where_a_number_belongs_is_a_conflict(lake):
    name = schema.table("schema_version")
    cols = dict(schema.expected()[name])
    number = next(c for c, t in cols.items() if t != pl.String)
    lake.create(name, cols | {number: pl.String})
    out = setup.inspect(lake)
    assert out["state"] == setup.CONFLICT and out["conflicts"] == [{"table": name, "columns": [number]}]
    assert out["writable"] is False


def test_the_same_string_type_is_never_a_conflict():
    assert setup._type_ok(pl.String, pl.String) is True
    assert setup._type_ok(pl.Utf8, pl.String) is True
    assert setup._type_ok(pl.String, pl.Int64) is False
    assert setup._type_ok(pl.Int64, pl.String) is False


@pytest.mark.parametrize("rows", [None, pl.DataFrame({"version": []}, schema={"version": pl.Int64})])
def test_no_schema_version_rows_reads_as_no_versions(rows):
    lake = SimpleNamespace(rows=lambda name: rows)
    assert setup.applied_versions(lake) == []


# ---- retry


def test_retry_tries_five_times_by_default():
    from deltalake.exceptions import CommitFailedError
    calls: list[int] = []

    def lost_race():
        calls.append(1)
        raise CommitFailedError("lost the commit race")

    with pytest.raises(CommitFailedError):
        setup.retry(lost_race, sleep=lambda s: None)
    assert len(calls) == 5


# ---- an upgrade


def _upgrade(tmp_path, lake, monkeypatch):
    me = pc(tmp_path)
    setup.initialize(lake, me, approve=True)
    monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS + [schema.Migration(NEXT, "Add rule packs", V2)])
    written: list[tuple[str, list]] = []
    real = setup.write

    def spy(lake_, installation, name, rows, version=None, family=setup.CORE):
        written.append((name, [r.get("version") for r in rows] if name == "schema_version" else
                        [r.get("action") for r in rows] if name == "audit" else []))
        return real(lake_, installation, name, rows, version, family)

    monkeypatch.setattr(setup, "write", spy)
    assert setup.inspect(lake)["state"] == setup.UPGRADE
    setup.initialize(lake, me, approve=True)
    return written


def test_an_upgrade_records_only_the_versions_it_adds(tmp_path, lake, monkeypatch):
    written = _upgrade(tmp_path, lake, monkeypatch)
    assert [v for name, v in written if name == "schema_version"] == [[NEXT]]


def test_an_upgrade_is_audited_as_an_upgrade(tmp_path, lake, monkeypatch):
    written = _upgrade(tmp_path, lake, monkeypatch)
    assert [a for name, a in written if name == "audit"] == [["upgrade"]]
    actions = lake.rows(schema.table("audit"))["action"].to_list()
    assert sorted(actions) == ["initialize", "upgrade"]


def test_a_first_setup_is_audited_as_initialize(tmp_path, lake):
    setup.initialize(lake, pc(tmp_path), approve=True)
    assert lake.rows(schema.table("audit"))["action"].to_list() == ["initialize"]


# ---- access


@pytest.mark.parametrize("code", [401, 403])
def test_a_401_or_403_means_no_permission_whatever_the_message(code):
    exc = OSError("the server said no")
    exc.response = SimpleNamespace(status_code=code)
    assert setup.denied(exc) is True


def test_another_status_with_a_plain_message_is_not_a_permission_error():
    exc = OSError("the server said no")
    exc.response = SimpleNamespace(status_code=500)
    assert setup.denied(exc) is False


def test_an_error_registering_that_isnt_about_permission_is_raised(tmp_path, lake, monkeypatch):
    setup.initialize(lake, pc(tmp_path, "owner"), approve=True)

    def broken(*_a, **_k):
        raise ValueError("the disk is full")

    monkeypatch.setattr(setup, "register", broken)
    with pytest.raises(ValueError, match="disk is full"):
        setup.check_access(lake, pc(tmp_path, "viewer"))


# ---- states inspect gives, with a fake lakehouse


class FakeLake:
    """A lakehouse holding ``names``, every table with the columns it should have, and
    schema_version rows for ``versions``."""

    def __init__(self, names, versions=()):
        self.names, self.versions = list(names), list(versions)

    def table_names(self):
        return self.names

    def rows(self, name):
        return pl.DataFrame({"version": self.versions}, schema={"version": pl.Int64})

    def columns_of(self, name):
        return dict(schema.expected()[name])


def test_an_empty_lakehouse_has_no_schema_version(lake):
    out = setup.inspect(lake)
    assert out["state"] == setup.EMPTY and out["schema_version"] is None


def test_every_table_but_no_recorded_version_is_unfinished():
    out = setup.inspect(FakeLake(schema.expected()))
    assert out["state"] == setup.INCOMPLETE and out["missing"] == [] and out["schema_version"] is None


def test_a_table_missing_at_the_latest_version_is_listed_to_add():
    gone = sorted(n for n in schema.expected() if n != schema.table("schema_version"))[0]
    out = setup.inspect(FakeLake([n for n in schema.expected() if n != gone], [schema.latest()]))
    assert out["state"] == setup.INCOMPLETE and out["missing"] == [gone] and out["adds"] == [gone]


def test_no_read_access_means_no_write_access_either(tmp_path, lake, monkeypatch):
    def refuse(_self):
        raise OSError("403 Forbidden")

    monkeypatch.setattr(LocalLakehouse, "table_names", refuse)
    out = setup.check_access(lake, pc(tmp_path))
    assert out["read"] is False and out["write"] is False and out["state"] is None
