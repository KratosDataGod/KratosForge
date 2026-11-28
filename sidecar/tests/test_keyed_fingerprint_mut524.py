"""Mutation batch 524: keyed_fingerprint's key file, pinned one behaviour at a time.

Where the key is kept, its size, the mode it is made with, the folders made for it, and which key wins when
another process repairs an empty key file at the same moment. The tests' conftest points ``key_path`` at a
tmp file for the whole session, so the real ``key_path`` is read from a fresh copy of the module."""

import importlib.util
import os
from pathlib import Path

from fabric_desktop import keyed_fingerprint as kf


def _fresh_module():
    spec = importlib.util.spec_from_file_location("fabric_desktop._keyed_fingerprint_fresh", kf.__file__)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "fabric_desktop"
    spec.loader.exec_module(mod)
    return mod


def test_the_key_lives_under_localappdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert _fresh_module().key_path() == tmp_path / "FabricDesktop" / "keys" / "fingerprint.key"


def test_without_localappdata_the_key_lives_under_the_home_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    expected = tmp_path / ".local" / "share" / "FabricDesktop" / "keys" / "fingerprint.key"
    assert _fresh_module().key_path() == expected


def test_a_key_is_32_bytes_and_a_32_byte_file_is_read_as_the_key(tmp_path):
    path = tmp_path / "fingerprint.key"
    assert len(kf._create(path)) == 32 and len(path.read_bytes()) == 32
    path.write_bytes(b"k" * 32)
    assert kf._read(path) == b"k" * 32
    path.write_bytes(b"k" * 33)
    assert kf._read(path) is None


def test_the_key_file_is_made_readable_by_its_owner_only(tmp_path, monkeypatch):
    modes = []
    real_open = kf.os.open

    def recording_open(file, flags, mode=0o777, *args, **kwargs):
        modes.append(mode)
        return real_open(file, flags, mode, *args, **kwargs)
    monkeypatch.setattr(kf.os, "open", recording_open)
    kf._create(tmp_path / "fingerprint.key")
    assert modes == [0o600]


def test_the_folders_for_the_key_are_made_however_deep(tmp_path):
    path = tmp_path / "a" / "b" / "fingerprint.key"
    key = kf._create(path)
    assert path.read_bytes() == key


def test_a_key_made_into_a_folder_that_already_exists_is_kept(tmp_path):
    path = tmp_path / "fingerprint.key"
    key = kf._create(path)
    assert path.read_bytes() == key


def test_a_key_another_process_wrote_while_this_one_repaired_an_empty_file_is_the_one_used(tmp_path, monkeypatch):
    path = tmp_path / "fingerprint.key"
    path.write_bytes(b"")
    theirs = b"t" * 32
    real_replace = kf.jsonfile.replace

    def replace_then_them(src, dst):
        real_replace(src, dst)
        Path(dst).write_bytes(theirs)  # the other process repairs the same empty file just after this one
    monkeypatch.setattr(kf.jsonfile, "replace", replace_then_them)
    assert kf._create(path) == theirs
    assert sorted(os.listdir(tmp_path)) == ["fingerprint.key"]
