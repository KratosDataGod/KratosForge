"""PB-062: Settings > Help and about > Save support bundle, through the engine's RPC methods."""

import zipfile
from pathlib import Path

import pytest

from fabric_desktop import audit, notebook_resources_service, server, support_bundle
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("FABRIC_DESKTOP_HOME", str(tmp_path / "agent"))  # no agent log on this PC
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    root = tmp_path / "la" / "FabricDesktop"
    root.mkdir(parents=True)
    return Service(root=root)


def test_the_methods_are_reachable_and_only_the_preview_skips_the_audit():
    assert {"support_bundle_preview", "support_bundle_save", "support_bundle_reveal"} <= server.METHODS
    assert not audit.audited("support_bundle_preview")
    assert audit.audited("support_bundle_save") and audit.audited("support_bundle_reveal")


def test_preview_says_which_log_is_missing_and_offers_downloads(svc, tmp_path):
    shown = svc.support_bundle_preview()
    assert [f["name"] for f in shown["files"]] == ["about.json", "CONTENTS.txt"]
    assert shown["missing"] == ["sidecar.log"]
    assert shown["folder"] == str(tmp_path / "home" / "Downloads")
    (svc.root / "sidecar.log").write_text("started\n", encoding="utf-8")
    assert svc.support_bundle_preview()["missing"] == []


def test_preview_includes_the_agent_log_only_on_a_server_that_has_one(svc, tmp_path):
    logs = tmp_path / "agent" / "logs"
    logs.mkdir(parents=True)
    (logs / "agent.log").write_text("agent up\n", encoding="utf-8")
    assert "agent.log" in [f["name"] for f in svc.support_bundle_preview()["files"]]


def test_save_writes_a_new_zip_each_time_and_never_replaces_one(svc, tmp_path, monkeypatch):
    class Frozen:  # two saves in the same second
        @staticmethod
        def now(tz=None):
            from datetime import datetime, timezone
            return datetime(2026, 11, 2, 17, 30, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(support_bundle, "datetime", Frozen)
    first = svc.support_bundle_save()
    second = svc.support_bundle_save()
    assert first["folder"] == str(tmp_path / "home" / "Downloads")
    assert first["name"] == "fabric-desktop-support-20261102-173000.zip"
    assert second["name"] == "fabric-desktop-support-20261102-173000-2.zip"
    for res in (first, second):
        assert support_bundle.NAME.fullmatch(res["name"])
        with zipfile.ZipFile(res["path"]) as z:
            assert {"about.json", "CONTENTS.txt"} <= set(z.namelist())


def test_save_refuses_a_relative_folder(svc):
    with pytest.raises(ValueError, match="full folder path"):
        svc.support_bundle_save("bundles")


def test_a_folder_it_cannot_write_says_so_and_leaves_nothing(svc, tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(support_bundle, "create", refuse)
    with pytest.raises(PermissionError) as err:
        svc.support_bundle_save(str(tmp_path / "locked"))
    assert str(err.value) == (f"KratosForge couldn't write to {tmp_path / 'locked'}: access denied. "
                              "Choose another folder and try again. Nothing was saved.")


def test_a_failed_write_leaves_no_half_zip(tmp_path, monkeypatch):
    def broken(self, name, text, *a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(zipfile.ZipFile, "writestr", broken)
    with pytest.raises(OSError):
        support_bundle.create(tmp_path / "out", tmp_path)
    assert list((tmp_path / "out").iterdir()) == []


def test_reveal_shows_only_a_support_bundle(svc, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(notebook_resources_service, "_reveal", lambda path, select: shown.append((path, select)))
    saved = svc.support_bundle_save()
    assert svc.support_bundle_reveal(saved["path"])["shown"] == saved["path"]
    assert shown == [(Path(saved["path"]), True)]
    other = tmp_path / "home" / "notes.txt"
    other.write_text("x", encoding="utf-8")
    for bad in (str(other), "fabric-desktop-support-20261102-173000.zip",
                str(tmp_path / "home" / "Downloads" / "fabric-desktop-support-20261102-999999.zip")):
        with pytest.raises(FileNotFoundError):
            svc.support_bundle_reveal(bad)
    assert len(shown) == 1
