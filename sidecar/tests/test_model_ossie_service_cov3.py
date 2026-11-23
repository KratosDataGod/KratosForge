"""Save TMDL to Git (model_ossie_service): the remaining edges. A file that isn't text reads as changed; a
part whose place in the folder is a folder isn't written over; and a stale file that stopped being a plain
file before it was removed is left alone. Fabric is the fake in test_model_ossie; nothing reaches a tenant."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_desktop import model_ossie_service as mos
from fabric_desktop.meta.store import OneLakeStore
from tests.test_model_ossie import FakeFabric, OssieService


@pytest.fixture
def service(tmp_path, monkeypatch):
    s = OssieService(root=tmp_path / "app")
    s.fake = FakeFabric()
    s.meta_init(str(tmp_path / "proj"), "3")
    project = s._project()
    store = OneLakeStore("test", "ws-test", {la.lakehouse: f"id-{la.lakehouse}" for la in project.layers},
                         lambda: "tok", tmp_path / "cache")
    monkeypatch.setattr(s, "_env_cfg", lambda p, env: {"workspace_id": "ws-test", "workspace_name": "WS Test",
                                                       "target": "onelake"})
    monkeypatch.setattr(s, "_store", lambda p, env, create=False: store)
    return s


def model_folder(service) -> Path:
    return Path(service._project().root) / "semantic" / "SM_Gold.SemanticModel"


def test_a_file_that_isnt_text_reads_as_changed(tmp_path):
    path = tmp_path / "model.tmdl"
    path.write_bytes(b"\xff\xfe\x00bad")
    assert mos._same_text(path, "model Model\n") is False
    assert mos._same_text(tmp_path / "missing.tmdl", "") is False


def test_a_part_whose_place_is_a_folder_isnt_written_over(service):
    (model_folder(service) / "definition" / "model.tmdl").mkdir(parents=True)
    with pytest.raises(ValueError, match="definition/model.tmdl is a link or a folder, so it isn't overwritten"):
        service.meta_model_tmdl_save("test", "SM_Gold")
    assert not (model_folder(service) / "definition.pbism").exists()  # nothing was written


def test_a_stale_file_that_stopped_being_a_plain_file_isnt_removed(service, monkeypatch):
    stale = model_folder(service) / "definition" / "tables" / "old_table.tmdl"
    stale.parent.mkdir(parents=True)
    stale.write_text("table old_table\n", encoding="utf-8")
    real = mos._real
    checked = []

    def swapped(path, kind):
        if Path(path) == stale and kind is mos.stat.S_ISREG:
            checked.append(path)
            if len(checked) > 1:  # listed as a file, then replaced by a link before the unlink
                return False
        return real(path, kind)
    monkeypatch.setattr(mos, "_real", swapped)
    out = service.meta_model_tmdl_save("test", "SM_Gold")
    assert out["removed"] == [] and out["files"] == 3 and stale.exists() and len(checked) == 2
