"""Export configuration (CP-010): a connection file for an environment this PC knows, and connecting
another PC with it. The file only says where to look; the manifest found there decides. Nothing here
reaches Fabric."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import audit, server
from fabric_desktop.controlplane import manifest as mf
from fabric_desktop.controlplane import store as st
from fabric_desktop.service import Service
from tests.test_controlplane_service import WS, _policy, svc  # noqa: F401 - the fixture is used by name

OTHER_ENV = "kfenv_" + "0" * 32


def _environment(svc) -> dict:  # noqa: F811
    return svc.cp_create(svc.cp_plan({"name": "Ours", "workspace_id": WS})["token"], confirmed=True)


def test_export_reads_and_connect_file_is_audited():
    assert {"cp_export", "cp_connect_file"} <= server.METHODS
    assert not audit.audited("cp_export") and audit.audited("cp_connect_file")


def test_export_gives_ids_and_labels_only(svc):  # noqa: F811
    out = _environment(svc)
    got = svc.cp_export()
    file = got["connection"]
    assert set(file) == {"format", "version", "environment_id", "name", "provider", "workspace_id", "workspace_name",
                         "lakehouse_id", "lakehouse_name"}
    assert file["format"] == mf.CONNECTION_FORMAT and file["version"] == 1 and file["provider"] == "fabric"
    assert file["environment_id"] == out["environment"]["environment_id"]
    assert (file["workspace_id"], file["lakehouse_id"]) == (WS, out["lakehouse_id"])
    assert got["file_name"] == "Ours.kfenv.json"
    assert json.loads(json.dumps(file)) == file
    assert mf.check_connection(file) == file


def test_export_by_id_and_refusals(svc):  # noqa: F811
    with pytest.raises(ValueError, match="Connect this PC"):
        svc.cp_export()
    out = _environment(svc)
    env_id = out["environment"]["environment_id"]
    assert svc.cp_export(env_id)["connection"]["environment_id"] == env_id
    with pytest.raises(ValueError, match="isn't connected to that environment"):
        svc.cp_export(OTHER_ENV)


def test_a_file_name_never_carries_a_path(svc):  # noqa: F811
    svc.cp_create(svc.cp_plan({"name": "..\\..\\evil/name", "workspace_id": WS})["token"], confirmed=True)
    name = svc.cp_export()["file_name"]
    assert name.endswith(".kfenv.json") and "/" not in name and "\\" not in name and ".." not in name[:-11]


def test_another_pc_connects_with_the_file(svc):  # noqa: F811
    _environment(svc)
    file = svc.cp_export()["connection"]
    env_id = file["environment_id"]
    svc.cp_disconnect(env_id)
    assert svc.cp_cache.active() is None
    got = svc.cp_connect_file(file)
    assert got["environment"]["environment_id"] == env_id and svc.cp_cache.active()["environment_id"] == env_id


def test_a_file_pointing_at_another_environment_connects_nothing(svc, monkeypatch):  # noqa: F811
    _environment(svc)
    file = svc.cp_export()["connection"]
    svc.cp_disconnect(file["environment_id"])
    forged = dict(file, environment_id=OTHER_ENV)
    with pytest.raises(ValueError, match="another environment than the one this file names"):
        svc.cp_connect_file(forged)
    assert svc.cp_cache.all() == []


def test_a_file_pointing_at_a_copied_manifest_connects_nothing(svc, monkeypatch):  # noqa: F811
    _environment(svc)
    file = svc.cp_export()["connection"]
    svc.cp_disconnect(file["environment_id"])
    elsewhere = dict(file, lakehouse_id="55555555-5555-4555-8555-555555555555")
    real = svc.lakes / WS / file["lakehouse_id"]
    monkeypatch.setattr(Service, "_cp_store", lambda self, ws, lh: st.local_store(real))  # the same manifest, copied
    with pytest.raises(ValueError, match="names another lakehouse"):
        svc.cp_connect_file(elsewhere)
    assert svc.cp_cache.all() == []


def test_an_empty_lakehouse_connects_nothing(svc):  # noqa: F811
    _environment(svc)
    file = dict(svc.cp_export()["connection"], lakehouse_id="55555555-5555-4555-8555-555555555555")
    with pytest.raises(ValueError, match="doesn't hold a KratosForge Environment"):
        svc.cp_connect_file(file)


@pytest.mark.parametrize("change, message", [
    (lambda f: "not a dict", "isn't a KratosForge Environment connection file"),
    (lambda f: dict(f, format="something-else"), "isn't a KratosForge Environment connection file"),
    (lambda f: dict(f, version=2), "another version"),
    (lambda f: dict(f, version=True), "another version"),
    (lambda f: dict(f, token="secret"), "fields KratosForge doesn't write: token"),
    (lambda f: dict(f, environment_id="kfenv_xyz"), "environment_id must be"),
    (lambda f: dict(f, provider="nowhere"), "provider must be one of"),
    (lambda f: dict(f, workspace_id="Finance workspace"), "workspace_id must be an id"),
    (lambda f: dict(f, lakehouse_id=""), "lakehouse_id"),
    (lambda f: dict(f, name="two\nlines"), "name must be one line"),
])
def test_a_bad_file_is_refused_before_anything_is_read(svc, monkeypatch, change, message):  # noqa: F811
    _environment(svc)
    file = svc.cp_export()["connection"]

    def never(self, ws, lh):
        raise AssertionError("a bad file must not be read")

    monkeypatch.setattr(Service, "_cp_store", never)
    with pytest.raises(ValueError, match=message):
        svc.cp_connect_file(change(file))


def test_a_file_for_another_provider_is_refused(svc, monkeypatch):  # noqa: F811
    _environment(svc)
    file = dict(svc.cp_export()["connection"], provider="local", workspace_id="ws1", lakehouse_id="lh1")
    monkeypatch.setattr(Service, "_cp_store", lambda self, ws, lh: (_ for _ in ()).throw(AssertionError("read")))
    with pytest.raises(ValueError, match="Fabric environments only"):
        svc.cp_connect_file(file)


def test_fabric_ids_are_compared_in_lower_case(svc):  # noqa: F811
    _environment(svc)
    file = svc.cp_export()["connection"]
    svc.cp_disconnect(file["environment_id"])
    upper = dict(file, workspace_id=file["workspace_id"].upper(), lakehouse_id=file["lakehouse_id"].upper())
    assert svc.cp_connect_file(upper)["environment"]["environment_id"] == file["environment_id"]


def test_policy_off_refuses_both(svc, tmp_path):  # noqa: F811
    _environment(svc)
    file = svc.cp_export()["connection"]
    _policy(tmp_path, allow_central_environment=False)
    with pytest.raises(PermissionError, match="allow_central_environment"):
        svc.cp_export()
    with pytest.raises(PermissionError, match="allow_central_environment"):
        svc.cp_connect_file(file)
