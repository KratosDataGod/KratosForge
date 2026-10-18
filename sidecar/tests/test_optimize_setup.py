"""Optimize's shared lakehouse (PR-OPT-001): initialize, attach, migrations, concurrency, access."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

import polars as pl
import pytest
from deltalake import write_deltalake

from fabric_desktop.optimize import schema, setup
from fabric_desktop.optimize.installation import Installation
from fabric_desktop.meta.store import LocalStore
from fabric_desktop.optimize import lakehouse
from fabric_desktop.optimize.lakehouse import LocalLakehouse
from fabric_desktop.optimize.registry import Registry


@pytest.fixture
def lake(tmp_path: Path) -> LocalLakehouse:
    return LocalLakehouse(tmp_path / "lakehouse")


def pc(tmp_path: Path, name: str = "pc1") -> Installation:
    return Installation(tmp_path / name / "optimize")


def ids(lake: LocalLakehouse, table: str) -> list[str]:
    return sorted(lake.rows(schema.table(table))["installation_id"].to_list())


def test_new_lakehouse_is_empty_and_initializes(tmp_path, lake):
    assert setup.inspect(lake)["state"] == setup.EMPTY
    with pytest.raises(PermissionError, match="Confirm"):
        setup.initialize(lake, pc(tmp_path))
    assert lake.table_names() == []  # nothing written without the person's OK
    out = setup.initialize(lake, pc(tmp_path), approve=True)
    assert out["state"] == setup.READY and out["schema_version"] == schema.latest()
    assert set(lake.table_names()) == set(schema.expected())
    for name, cols in schema.expected().items():
        assert list(lake.columns_of(name)) == list(cols)
    config = lake.rows("fdopt_config")
    assert dict(zip(config["key"], config["value"])) == schema.DEFAULT_CONFIG
    assert ids(lake, "installations") == [pc(tmp_path).id]


def test_initialize_again_adds_nothing(tmp_path, lake):
    me = pc(tmp_path)
    setup.initialize(lake, me, approve=True)
    versions = {n: lake.version("optimization", "dbo", n) for n in lake.table_names()}
    assert setup.initialize(lake, me, approve=True)["state"] == setup.READY
    assert len(lake.rows("fdopt_schema_version")) == 1
    assert len(lake.rows("fdopt_installations")) == 1
    # Ready lakehouses aren't rewritten: only a no-op MERGE of the registration may commit.
    assert {n: lake.version("optimization", "dbo", n) for n in lake.table_names() if n != "fdopt_installations"} \
        == {n: v for n, v in versions.items() if n != "fdopt_installations"}


def test_second_installation_attaches_without_duplicating(tmp_path, lake):
    setup.initialize(lake, pc(tmp_path, "pc1"), approve=True)
    other = pc(tmp_path, "pc2")
    access = setup.check_access(lake, other)
    assert access["read"] and access["write"]
    assert len(lake.rows("fdopt_schema_version")) == 1
    assert ids(lake, "installations") == sorted([pc(tmp_path, "pc1").id, other.id])


def test_two_installations_initialize_at_once(tmp_path, lake):
    errors: list[BaseException] = []

    def run(name: str) -> None:
        try:
            setup.initialize(lake, pc(tmp_path, name), approve=True)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(f"pc{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert setup.inspect(lake)["state"] == setup.READY
    assert lake.rows("fdopt_schema_version")["version"].to_list() == [1]
    assert len(lake.rows("fdopt_config")) == len(schema.DEFAULT_CONFIG)
    assert len(lake.rows("fdopt_installations")) == 4



WINDOWS_RACE = OSError(
    "Generic LocalFileSystem error: Unable to open file C:\\lh\\Tables\\dbo\\fdopt_dependencies\\_delta_log"
    "\\00000000000000000000.json#1: Access is denied. (os error 5)")


def test_windows_delta_log_race_is_retried(lake, monkeypatch):
    # On Windows a second writer can briefly be refused the log file the first is renaming.
    calls = []
    real = LocalStore.create_if_absent

    def flaky(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise WINDOWS_RACE
        return real(self, *args, **kwargs)

    monkeypatch.setattr(LocalStore, "create_if_absent", flaky)
    monkeypatch.setattr(lakehouse.time, "sleep", lambda s: None)
    assert lake.create("fdopt_config", {schema.KEY: pl.String}) is True
    assert len(calls) == 2 and lake.has("fdopt_config")


def test_other_errors_are_not_retried(lake, monkeypatch):
    calls = []

    def broken(self, *args, **kwargs):
        calls.append(1)
        raise OSError("Access is denied. (os error 5)")  # not the Delta log race

    monkeypatch.setattr(LocalStore, "create_if_absent", broken)
    with pytest.raises(OSError):
        lake.create("fdopt_config", {schema.KEY: pl.String})
    assert len(calls) == 1

def test_unrelated_tables_are_left_alone(tmp_path, lake):
    other = lake.uri("optimization", "dbo", "sales")
    Path(other).mkdir(parents=True)
    write_deltalake(other, pl.DataFrame({"id": [1, 2]}).to_arrow())
    state = setup.inspect(lake)
    assert state["state"] == setup.UNRELATED and state["other_tables"] == 1
    setup.initialize(lake, pc(tmp_path), approve=True)
    assert lake.version("optimization", "dbo", "sales") == 0
    assert lake.rows("sales")["id"].to_list() == [1, 2]


def test_fdopt_table_with_other_columns_is_a_conflict(tmp_path, lake):
    uri = lake.uri("optimization", "dbo", "fdopt_findings")
    Path(uri).mkdir(parents=True)
    write_deltalake(uri, pl.DataFrame({"record_id": [1]}).to_arrow())  # an Int64 where Optimize keeps text
    state = setup.inspect(lake)
    assert state["state"] == setup.CONFLICT and state["conflicts"][0]["table"] == "fdopt_findings"
    with pytest.raises(PermissionError, match="aren't Optimize's"):
        setup.initialize(lake, pc(tmp_path), approve=True)
    assert lake.table_names() == ["fdopt_findings"]
    assert lake.version("optimization", "dbo", "fdopt_findings") == 0


def test_unfinished_setup_is_finished(tmp_path, lake):
    lake.create("fdopt_findings", schema.expected()["fdopt_findings"])
    assert setup.inspect(lake)["state"] == setup.INCOMPLETE
    assert setup.initialize(lake, pc(tmp_path), approve=True)["state"] == setup.READY


def test_lakehouse_without_schemas(tmp_path):
    lake = LocalLakehouse(tmp_path / "flat", schemas=False)
    setup.initialize(lake, pc(tmp_path), approve=True)
    assert (tmp_path / "flat" / "Tables" / "fdopt_schema_version" / "_delta_log").is_dir()
    assert setup.inspect(lake)["state"] == setup.READY


# ---------- migrations

V2 = {"rule_packs": {"pack_id": pl.String, "title": pl.String}}


@pytest.fixture
def with_v2(monkeypatch):
    calls: list[str] = []
    migrations = schema.MIGRATIONS + [schema.Migration(2, "Add rule packs", V2, steps=(lambda lake: calls.append("step"),))]
    monkeypatch.setattr(schema, "MIGRATIONS", migrations)
    return calls


def test_older_schema_is_upgraded_and_keeps_rows(tmp_path, lake, monkeypatch):
    me = pc(tmp_path)
    setup.initialize(lake, me, approve=True)
    setup.write(lake, me, "findings", [{"record_id": "f1", "fingerprint": "fp"}])
    calls: list[str] = []
    monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS
                        + [schema.Migration(2, "Add rule packs", V2, steps=(lambda lake: calls.append("step"),))])
    state = setup.inspect(lake)
    assert state["state"] == setup.UPGRADE and state["missing"] == [] and state["adds"] == ["fdopt_rule_packs"]
    with pytest.raises(PermissionError):
        setup.initialize(lake, me)  # an upgrade needs the person's OK too
    assert setup.initialize(lake, me, approve=True)["state"] == setup.READY
    assert sorted(lake.rows("fdopt_schema_version")["version"].to_list()) == [1, 2]
    assert lake.rows("fdopt_findings")["record_id"].to_list() == ["f1"]
    assert calls == ["step"]


def test_migration_adds_a_column_to_an_existing_table(tmp_path, lake, monkeypatch):
    me = pc(tmp_path)
    setup.initialize(lake, me, approve=True)
    setup.write(lake, me, "findings", [{"record_id": "f1"}])
    monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS
                        + [schema.Migration(2, "Findings get an owner", {"findings": {"owner": pl.String}})])
    assert setup.inspect(lake)["state"] == setup.UPGRADE
    setup.initialize(lake, me, approve=True)
    assert lake.columns_of("fdopt_findings")["owner"] == pl.String
    assert lake.rows("fdopt_findings")["owner"].to_list() == [None]
    setup.write(lake, me, "findings", [{"record_id": "f2", "owner": "data team"}])
    assert sorted(lake.rows("fdopt_findings")["record_id"].to_list()) == ["f1", "f2"]


def test_migration_that_fails_part_way_is_recoverable(tmp_path, lake, monkeypatch):
    me = pc(tmp_path)
    setup.initialize(lake, me, approve=True)
    attempts = {"n": 0}

    def flaky(_lake):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ValueError("lost the network half way")

    monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS + [schema.Migration(2, "Add rule packs", V2, (flaky,))])
    with pytest.raises(ValueError):
        setup.initialize(lake, me, approve=True)
    # The version isn't recorded until every step succeeded, so the lakehouse still reads as v1.
    assert setup.inspect(lake)["schema_version"] == 1
    assert setup.initialize(lake, me, approve=True)["state"] == setup.READY
    assert sorted(lake.rows("fdopt_schema_version")["version"].to_list()) == [1, 2]


def test_newer_schema_opens_read_only(tmp_path, lake, with_v2, monkeypatch):
    setup.initialize(lake, pc(tmp_path, "new"), approve=True)
    monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:1])  # an older app
    state = setup.inspect(lake)
    assert state["state"] == setup.NEWER and not state["writable"]
    access = setup.check_access(lake, pc(tmp_path, "old"))
    assert access["read"] and not access["write"]
    assert pc(tmp_path, "old").id not in ids(lake, "installations")


# ---------- access

def test_read_only_access_is_detected(tmp_path, lake, monkeypatch):
    setup.initialize(lake, pc(tmp_path, "owner"), approve=True)

    def refuse(*_a, **_k):
        raise OSError("Generic MicrosoftAzure error: 403 Forbidden AuthorizationPermissionMismatch")

    monkeypatch.setattr(LocalLakehouse, "merge_insert", refuse)
    access = setup.check_access(lake, pc(tmp_path, "viewer"))
    assert access["read"] and not access["write"] and "read-only" in access["message"]


def test_no_access_says_what_to_ask_for(tmp_path, lake, monkeypatch):
    def refuse(_self):
        raise OSError("403 Forbidden")

    monkeypatch.setattr(LocalLakehouse, "table_names", refuse)
    access = setup.check_access(lake, pc(tmp_path))
    assert not access["read"] and "Contributor" in access["message"]


def test_other_errors_are_not_mistaken_for_permissions(tmp_path, lake, monkeypatch):
    monkeypatch.setattr(LocalLakehouse, "table_names", lambda _self: (_ for _ in ()).throw(ValueError("bad path")))
    with pytest.raises(ValueError):
        setup.check_access(lake, pc(tmp_path))


def test_lost_commit_race_is_retried(tmp_path, lake, monkeypatch):
    from deltalake.exceptions import CommitFailedError
    setup.initialize(lake, pc(tmp_path), approve=True)
    real = LocalLakehouse.merge_insert
    fails = {"n": 2}

    def racing(self, *a, **k):
        if fails["n"]:
            fails["n"] -= 1
            raise CommitFailedError("another writer committed first")
        return real(self, *a, **k)

    monkeypatch.setattr(LocalLakehouse, "merge_insert", racing)
    monkeypatch.setattr(setup.time, "sleep", lambda _s: None)
    setup.write(lake, pc(tmp_path), "findings", [{"record_id": "f1"}])
    assert lake.rows("fdopt_findings")["record_id"].to_list() == ["f1"]


def test_rows_carry_provenance(tmp_path, lake):
    me = pc(tmp_path)
    setup.initialize(lake, me, approve=True)
    setup.write(lake, me, "findings", [{"record_id": "f1", "fingerprint": "fp"}])
    setup.write(lake, me, "findings", [{"record_id": "f1", "fingerprint": "fp"}])  # a retry or resync
    row = lake.rows("fdopt_findings").row(0, named=True)
    assert len(lake.rows("fdopt_findings")) == 1
    assert row["installation_id"] == me.id and row["schema_version"] == schema.latest()
    assert row["created_at"] and row["app_version"]


# ---------- this PC

def test_installation_id_is_random_and_resettable(tmp_path):
    me = pc(tmp_path)
    first = me.id
    assert uuid.UUID(first).version == 4 and me.id == first
    assert Installation(tmp_path / "pc1" / "optimize").id == first  # kept across restarts
    me.set_label("Build server")
    assert me.reset() != first and me.label == "Build server"


def test_registry_switch_and_forget_never_touch_the_lakehouse(tmp_path, lake):
    setup.initialize(lake, pc(tmp_path), approve=True)
    before = {n: lake.version("optimization", "dbo", n) for n in lake.table_names()}
    reg = Registry(tmp_path / "pc1" / "optimize")
    reg.add({"workspace_id": "w1", "lakehouse_id": "a", "lakehouse_name": "Org"})
    reg.add({"workspace_id": "w2", "lakehouse_id": "b", "lakehouse_name": "Finance"})
    assert reg.active()["lakehouse_id"] == "b"
    assert reg.activate("a")["active"]
    with pytest.raises(LookupError):
        reg.activate("zzz")
    reg.forget("a")
    assert reg.active()["lakehouse_id"] == "b" and [c["lakehouse_id"] for c in reg.all()] == ["b"]
    reg.add({"workspace_id": "w1", "lakehouse_id": "a"}, activate=False)
    assert reg.active()["lakehouse_id"] == "b"
    assert {n: lake.version("optimization", "dbo", n) for n in lake.table_names()} == before
