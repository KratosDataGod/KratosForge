"""Platform definition files are swapped in whole: a save that fails part way leaves the old file."""

import json
import os
import re

import pytest

from fabric_desktop import jsonfile
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "repo" / "crm"), "3")
    return s


@pytest.fixture
def failing_replace(monkeypatch):
    """os.replace fails, as when the PC loses power or the disk fills between writing and swapping."""
    def broken(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(jsonfile.os, "replace", broken)


def entity_file(svc, name="silver.orders"):
    return svc._project().entities_dir / f"{name}.toml"


def test_a_failed_entity_save_leaves_the_file_whole(svc, failing_replace):
    path = entity_file(svc)
    before = path.read_text(encoding="utf-8")
    with pytest.raises(OSError):
        svc.meta_save_entity("silver.orders", before + "\n# edited\n")
    assert path.read_text(encoding="utf-8") == before
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]


LEAF = "gold.fact_sales"  # nothing reads it, so it can be renamed


def renamed_text(svc):
    text = entity_file(svc, LEAF).read_text(encoding="utf-8")
    renamed = re.sub(r'^name = "gold\.fact_sales"', 'name = "gold.fact_sales_v2"', text, count=1, flags=re.M)
    assert renamed != text
    return text, renamed


def test_a_failed_rename_keeps_the_old_entity(svc, failing_replace):
    text, renamed = renamed_text(svc)
    with pytest.raises(OSError):
        svc.meta_save_entity(LEAF, renamed)
    assert entity_file(svc, LEAF).read_text(encoding="utf-8") == text
    assert not entity_file(svc, "gold.fact_sales_v2").exists()


def test_a_rename_moves_the_entity_file(svc):
    _, renamed = renamed_text(svc)
    svc.meta_save_entity(LEAF, renamed)
    assert not entity_file(svc, LEAF).exists() and entity_file(svc, "gold.fact_sales_v2").exists()


def test_a_failed_layers_save_leaves_layers_whole(svc, failing_replace):
    path = svc._project().root / "layers.toml"
    before = path.read_text(encoding="utf-8")
    from fabric_desktop.meta.project import write_layers_file
    with pytest.raises(OSError):
        write_layers_file(svc._project().root, svc._project().layers, {})
    assert path.read_text(encoding="utf-8") == before


def test_torn_meta_settings_are_set_aside_not_lost(svc):
    svc.settings_file.parent.mkdir(parents=True, exist_ok=True)
    svc.settings_file.write_text('{"project": "c:/repo', encoding="utf-8")
    assert svc._settings() == {}
    assert list(svc.settings_file.parent.glob(f"{svc.settings_file.name}.corrupt-*"))
    svc._save_settings(project="x")
    assert json.loads(svc.settings_file.read_text(encoding="utf-8"))["project"] == "x"


def test_meta_saves_no_longer_write_files_in_place():
    from pathlib import Path
    base = Path(__file__).resolve().parents[1] / "fabric_desktop"
    for rel in ("meta_service.py", "meta/keys.py", "meta/mesh.py", "meta/project.py", "meta/templates.py"):
        assert ".write_text(" not in (base / rel).read_text(encoding="utf-8"), rel
    assert os.replace  # jsonfile.write_text does the swap
