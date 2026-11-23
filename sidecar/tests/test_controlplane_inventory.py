"""The service registry and inventory (CP-015): every registered family with its tables, and one
lakehouse's tables family by family, with unclaimed fd tables listed and other tables only counted.
Reads only. Nothing here reaches Fabric."""

from __future__ import annotations

import shutil
from pathlib import Path

import polars as pl
import pytest

from fabric_desktop import audit, server
from fabric_desktop.controlplane import inventory as inv
from fabric_desktop.controlplane import manifest as mf
from fabric_desktop.controlplane import store as st
from fabric_desktop.optimize import schema, setup
from fabric_desktop.optimize.lakehouse import NAME, LocalLakehouse
from fabric_desktop.service import Service
from tests.test_controlplane_service import WS, _policy, svc  # noqa: F401 - the fixture is used by name


def _environment(svc) -> LocalLakehouse:  # noqa: F811
    out = svc.cp_create(svc.cp_plan({"name": "Ours", "workspace_id": WS})["token"], confirmed=True)
    return LocalLakehouse(svc.lakes / WS / out["lakehouse_id"])


def _add(lake: LocalLakehouse, *names: str) -> None:
    for n in names:
        lake.create(n, {"x": pl.String})


def _files(lake: LocalLakehouse) -> list[str]:
    return sorted(str(p.relative_to(lake.root)) for p in Path(lake.root).rglob("*"))


def test_it_is_a_registered_read():
    assert "cp_inventory" in server.METHODS and not audit.audited("cp_inventory")


def test_the_registry_lists_every_family_with_its_tables():
    reg = inv.registry()
    assert [r["prefix"] for r in reg] == sorted(schema.FAMILIES)
    assert len(reg) >= 2, "the test needs a second table family"
    for r in reg:
        fam = schema.FAMILIES[r["prefix"]]
        assert r["title"] == fam.title and r["schema"] == fam.schema and r["latest_version"] == fam.latest()
        assert r["tables"] == sorted(fam.expected()) and r["prefix"] + "schema_version" in r["tables"]


def test_a_ready_lakehouse(svc):  # noqa: F811
    lake = _environment(svc)
    before = _files(lake)
    got = svc.cp_inventory()
    assert got["status"] == "Connected" and got["registry"] == inv.registry()
    assert got["environment"]["lakehouse_id"] == svc.cp_status()["active"]["lakehouse_id"]
    families = got["inventory"]["families"]
    assert [f["prefix"] for f in families] == sorted(schema.FAMILIES)
    for f in families:
        assert f["state"] == setup.READY and f["missing"] == [], f
        assert f["tables"] == sorted(schema.FAMILIES[f["prefix"]].expected())
    assert got["inventory"]["unclaimed"] == [] and got["inventory"]["other_tables"] == 0
    assert _files(lake) == before  # reads only


def test_a_lakehouse_missing_one_family(svc):  # noqa: F811
    lake = _environment(svc)
    gone = next(f for f in schema.FAMILIES.values() if f.prefix != schema.PREFIX)
    for n in gone.expected():
        shutil.rmtree(lake.uri(NAME, schema.schema_of(n), n))
    families = {f["prefix"]: f for f in svc.cp_inventory()["inventory"]["families"]}
    assert families[gone.prefix]["tables"] == []
    assert families[gone.prefix]["state"] == setup.UNRELATED
    assert families[gone.prefix]["missing"] == sorted(gone.expected())
    assert all(f["state"] == setup.READY for p, f in families.items() if p != gone.prefix)


def test_unclaimed_fd_tables_are_listed_and_other_tables_only_counted(svc):  # noqa: F811
    lake = _environment(svc)
    _add(lake, "fdold_things", "fdoptx_rows", "sales", "customers", "fd_nope", "fdabcdefghij_long")
    inventory = svc.cp_inventory()["inventory"]
    assert inventory["unclaimed"] == [
        {"table": "fdold_things", "prefix": "fdold_", "label": "Not KratosForge's, or from another version"},
        {"table": "fdoptx_rows", "prefix": "fdoptx_", "label": "Not KratosForge's, or from another version"}]
    assert inventory["other_tables"] == 4
    shown = repr(inventory)
    for name in ("sales", "customers", "fd_nope", "fdabcdefghij_long"):
        assert name not in shown
    assert "garbage" not in shown.lower()


def test_inventory_is_pure_over_names_and_inspections():
    names = ["fdopt_audit", "fdzzz_left", "other"]
    got = inv.inventory(names, [{"family": schema.PREFIX, "state": setup.INCOMPLETE, "missing": ["fdopt_config"]}])
    opt = next(f for f in got["families"] if f["prefix"] == schema.PREFIX)
    assert opt["state"] == setup.INCOMPLETE and opt["tables"] == ["fdopt_audit"] and opt["missing"] == ["fdopt_config"]
    assert [u["table"] for u in got["unclaimed"]] == ["fdzzz_left"] and got["other_tables"] == 1
    assert all(f["state"] is None for f in got["families"] if f["prefix"] != schema.PREFIX)


def test_policy_off_refuses(svc, tmp_path):  # noqa: F811
    _environment(svc)
    _policy(tmp_path, allow_central_environment=False)
    with pytest.raises(PermissionError, match="allow_central_environment"):
        svc.cp_inventory()


def test_no_active_environment_refuses(svc):  # noqa: F811
    with pytest.raises(ValueError, match="Connect this PC"):
        svc.cp_inventory()


@pytest.mark.parametrize("exc, status", [(ConnectionError("network down"), "Working offline"),
                                         (RuntimeError("403 Forbidden"), "Limited permissions")])
def test_an_unreadable_lakehouse_gives_a_status_not_an_exception(svc, monkeypatch, exc, status):  # noqa: F811
    _environment(svc)

    class Failing:
        def table_names(self):
            raise exc

    monkeypatch.setattr(Service, "_optimize_lake", lambda self, ws, lh, schemas=None, token=None: Failing())
    got = svc.cp_inventory()
    assert got["status"] == status and got["inventory"] is None and got["error"]
    assert got["registry"] == inv.registry() and got["environment"]["environment_id"]


def test_a_lakehouse_whose_manifest_is_gone_or_damaged_is_not_inventoried(svc, monkeypatch):  # noqa: F811
    _environment(svc)
    monkeypatch.setattr(Service, "_cp_store", lambda self, ws, lh: st.local_store(svc.lakes / "nowhere"))
    gone = svc.cp_inventory()
    assert gone["status"] == "Connection failed" and "isn't in its lakehouse" in gone["detail"]
    assert gone["inventory"] is None and gone["registry"] == inv.registry()

    class Damaged:
        def read(self):
            raise mf.ManifestError("not JSON")

    monkeypatch.setattr(Service, "_cp_store", lambda self, ws, lh: Damaged())
    damaged = svc.cp_inventory()
    assert damaged["status"] == "Connection failed" and "damaged" in damaged["detail"] and damaged["inventory"] is None


def test_an_unreadable_manifest_gives_a_status_not_an_exception(svc, monkeypatch):  # noqa: F811
    _environment(svc)

    class Offline:
        def read(self):
            raise ConnectionError("network down")

    monkeypatch.setattr(Service, "_cp_store", lambda self, ws, lh: Offline())
    got = svc.cp_inventory()
    assert got["status"] == "Working offline" and got["inventory"] is None and got["error"]
