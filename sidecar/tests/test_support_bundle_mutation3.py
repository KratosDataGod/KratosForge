"""support_bundle.py: the mutants that test_support_bundle_mutation.py and the module's other tests leave alive.
about.json is indented by two, the contents lines wrap at a hundred characters, and a failed write whose file
is already gone raises its own error."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from fabric_desktop import support_bundle
from tests.test_support_bundle import pc  # noqa: F401  (a fixture)


def test_about_json_is_indented_by_two(tmp_path, pc):  # noqa: F811
    path = support_bundle.create(tmp_path / "out", data_dir=pc)
    with zipfile.ZipFile(path) as z:
        about = z.read("about.json").decode()
    assert about.startswith('{\n  "engine_version": ')


def test_a_failed_write_whose_file_is_already_gone_raises_its_own_error(tmp_path, pc, monkeypatch):  # noqa: F811
    def broken(handle, *args, **kwargs):
        Path(handle.name).unlink()
        raise OSError("disk full")

    monkeypatch.setattr(support_bundle.zipfile, "ZipFile", broken)
    with pytest.raises(OSError, match="disk full"):
        support_bundle.create(tmp_path / "out", data_dir=pc)
    assert list((tmp_path / "out").iterdir()) == []


def test_contents_lines_wrap_at_a_hundred_characters():
    assert support_bundle._wrap("a" * 99 + " b") == ["a" * 99, "b"]
    assert support_bundle._wrap("a" * 98 + " b") == ["a" * 98 + " b"]
