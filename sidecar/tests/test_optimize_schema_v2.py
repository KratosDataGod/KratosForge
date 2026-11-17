"""fdopt_ schema version 2 (ESI-101): additive upgrade, writes to a lakehouse nobody has upgraded,
other families on an older core, and older apps reading a newer lakehouse."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from fabric_desktop.optimize import schema, setup
from fabric_desktop.optimize.installation import Installation
from fabric_desktop.optimize.lakehouse import LocalLakehouse
from fabric_desktop.optimize.shared import Shared
from tests.test_optimize_service import svc  # noqa: F401 - fixture

V1 = schema.MIGRATIONS[:1]
NEW_TABLES = ("pricing", "optimization_link", "baseline", "validation", "attribution", "sync")


@pytest.fixture
def lake(tmp_path: Path) -> LocalLakehouse:
    return LocalLakehouse(tmp_path / "lakehouse")


def pc(tmp_path: Path, name: str = "pc1") -> Installation:
    return Installation(tmp_path / name / "optimize")


def v1_lakehouse(lake: LocalLakehouse, me: Installation, monkeypatch) -> None:
    """A lakehouse an app that knew only version 1 set up."""
    with monkeypatch.context() as m:
        m.setattr(schema, "MIGRATIONS", V1)
        setup.initialize(lake, me, approve=True)


def execution(eid: str, **kw):
    return {"record_id": eid, "execution_id": eid, "engine": "local", "seconds": 4.0, "result": "succeeded", **kw}


def test_version_2_only_adds():
    v1, v2 = schema.expected(1), schema.expected(2)
    for name, cols in v1.items():
        assert list(v2[name])[:len(cols)] == list(cols), f"{name} lost, renamed or reordered a column"
        assert all(v2[name][c] == t for c, t in cols.items()), f"{name} changed a column's type"
    assert sorted(set(v2) - set(v1)) == sorted(schema.table(t) for t in NEW_TABLES)


def test_the_tables_every_family_writes_are_the_same_in_every_version():
    # Other families write installations, audit and config whatever version the lakehouse is at.
    for name in ("installations", "audit", "config", "schema_version"):
        assert schema.expected(1)[schema.table(name)] == schema.expected()[schema.table(name)]


def test_no_identity_columns_until_decided():
    # Who did something (tenant and user ids) waits for Chris's decision D3.
    for cols in schema.expected().values():
        assert not {"tenant_id", "user_id", "upn", "email"} & set(cols)


def test_v1_lakehouse_upgrades_and_keeps_its_rows(tmp_path, lake, monkeypatch):
    me = pc(tmp_path)
    v1_lakehouse(lake, me, monkeypatch)
    Shared(lake, me).record_local_executions([execution("e1")])
    state = setup.inspect(lake)
    assert state["state"] == setup.UPGRADE and state["schema_version"] == 1
    assert state["adds"] == sorted(schema.table(t) for t in NEW_TABLES)
    with pytest.raises(PermissionError, match="Confirm"):
        setup.initialize(lake, me)
    assert setup.initialize(lake, me, approve=True)["state"] == setup.READY
    rows = lake.rows("fdopt_local_execution")
    assert rows["execution_id"].to_list() == ["e1"] and rows["definition_version"].to_list() == [None]
    assert lake.columns_of("fdopt_savings")["corrects"] == pl.String
    assert all(lake.has(schema.table(t)) for t in NEW_TABLES)
    assert sorted(lake.rows("fdopt_schema_version")["version"].to_list()) == [1, 2]
    # Running it again changes nothing.
    assert setup.initialize(lake, me, approve=True)["state"] == setup.READY
    assert len(lake.rows("fdopt_schema_version")) == 2 and len(lake.rows("fdopt_local_execution")) == 1


def test_a_v2_app_writes_to_a_v1_lakehouse_with_v1_columns(tmp_path, lake, monkeypatch):
    me = pc(tmp_path)
    v1_lakehouse(lake, me, monkeypatch)
    writer = Shared(lake, me)
    assert writer.record_local_executions([execution("e1", definition_version="def-1")]) == 1
    row = lake.rows("fdopt_local_execution").row(0, named=True)
    assert row["schema_version"] == 1 and "definition_version" not in row
    assert writer.record_local_executions([execution("e1")]) == 1  # a retry adds nothing
    assert len(lake.rows("fdopt_local_execution")) == 1


def test_a_table_the_lakehouse_does_not_have_yet_is_refused_with_the_upgrade_message(tmp_path, lake, monkeypatch):
    me = pc(tmp_path)
    v1_lakehouse(lake, me, monkeypatch)
    with pytest.raises(PermissionError, match="older Optimize schema"):
        setup.write(lake, me, "pricing", [{"record_id": "p1", "scenario": "Example"}], version=1)
    assert not lake.has("fdopt_pricing")


def test_another_family_sets_up_beside_an_older_core_without_upgrading_it(tmp_path, lake, monkeypatch):
    me = pc(tmp_path)
    v1_lakehouse(lake, me, monkeypatch)
    fam = schema.Family("fdtst_", "Test", [schema.Migration(1, "Create", {
        "event": {"what": pl.String}, "schema_version": schema.TABLES_V1["schema_version"]})])
    monkeypatch.setitem(schema.FAMILIES, fam.prefix, fam)
    assert setup.initialize(lake, me, approve=True, family=fam)["state"] == setup.READY
    setup.write(lake, me, "event", [{"record_id": "e1", "what": "opened"}], family=fam)
    assert lake.rows("fdtst_event")["what"].to_list() == ["opened"]
    # Optimize's own upgrade stays its own confirmed step.
    assert setup.inspect(lake)["state"] == setup.UPGRADE
    # Rows of the shared tables name the version they went into, not this app's.
    audit = lake.rows("fdopt_audit").filter(pl.col("target") == "fdtst_ schema 1")
    assert audit["schema_version"].to_list() == [1]
    assert set(lake.rows("fdopt_config")["schema_version"].to_list()) == {1}


def test_central_family_works_on_an_older_core(svc, monkeypatch):  # noqa: F811 - fixture
    with monkeypatch.context() as m:
        m.setattr(schema, "MIGRATIONS", V1)
        svc.optimize_create("w1", workspace_name="Platform")
    fam = schema.Family("fdtst_", "Test", [schema.Migration(1, "Create", {
        "event": {"what": pl.String}, "schema_version": schema.TABLES_V1["schema_version"]})])
    monkeypatch.setitem(schema.FAMILIES, fam.prefix, fam)
    with pytest.raises(PermissionError, match="Confirm"):
        svc.central_family(fam)  # the family's own tables still ask first
    lake, me = svc.central_family(fam, approve=True)
    setup.write(lake, me, "event", [{"record_id": "e1", "what": "opened"}], family=fam)
    assert setup.inspect(lake)["schema_version"] == 1  # Optimize's tables weren't upgraded on the way


def test_a_v1_app_opens_a_v2_lakehouse_read_only(tmp_path, lake, monkeypatch):
    setup.initialize(lake, pc(tmp_path, "new"), approve=True)
    monkeypatch.setattr(schema, "MIGRATIONS", V1)  # an installation not updated yet
    assert setup.inspect(lake)["state"] == setup.NEWER
    old = Installation(tmp_path / "old" / "optimize")
    with pytest.raises(PermissionError, match="newer Optimize schema"):
        Shared(lake, old).record_local_executions([execution("e1")])
    assert setup.check_access(lake, old)["write"] is False
