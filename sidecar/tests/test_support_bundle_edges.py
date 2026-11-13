"""Support bundles: Windows' long-path setting and a folder with every bundle name taken."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop import support_bundle
from tests.test_support_bundle import pc  # noqa: F401  (a fixture)


class _Key:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _winreg(value=None, error=None):
    def open_key(root, path):
        assert path == r"SYSTEM\CurrentControlSet\Control\FileSystem"
        if error:
            raise error
        return _Key()

    return SimpleNamespace(HKEY_LOCAL_MACHINE="HKLM", OpenKey=open_key, QueryValueEx=lambda key, name: (value, 4))


def test_long_paths_are_unknown_off_windows(monkeypatch):
    monkeypatch.setattr(support_bundle, "sys", SimpleNamespace(platform="linux"))
    assert support_bundle.long_paths_enabled() is None


@pytest.mark.parametrize(("value", "expected"), [(1, True), (0, False)])
def test_long_paths_read_windows_registry_value(monkeypatch, value, expected):
    monkeypatch.setattr(support_bundle, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setitem(sys.modules, "winreg", _winreg(value))
    assert support_bundle.long_paths_enabled() is expected


def test_long_paths_not_set_read_as_off(monkeypatch):
    monkeypatch.setattr(support_bundle, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setitem(sys.modules, "winreg", _winreg(error=FileNotFoundError("no value")))
    assert support_bundle.long_paths_enabled() is False


def test_a_folder_with_every_bundle_name_from_this_second_taken_says_so(tmp_path, pc, monkeypatch):  # noqa: F811
    real_open = Path.open

    def taken(self, mode="r", *args, **kwargs):
        if mode == "xb":
            raise FileExistsError(str(self))
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", taken)
    with pytest.raises(FileExistsError, match="already 99 support bundles"):
        support_bundle.create(tmp_path / "out", data_dir=pc)
    assert list((tmp_path / "out").iterdir()) == []
