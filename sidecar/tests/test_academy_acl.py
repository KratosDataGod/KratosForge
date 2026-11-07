"""The exercise runtime's grant, written in process (academy/acl.py), leaves exactly the ACLs icacls
did, and a second pass writes nothing. Two identical trees are granted, one with icacls as before and
one in process, and every path's whole DACL is compared, children included. Nothing here touches the
Python install, so no machine-wide grant is made."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fabric_desktop.academy import acl, appcontainer

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows ACLs")

CAP = "S-1-15-3-1024-1111111111-2222222222-3333333333-444444444-555555555-666666666-777777777-888888888"
OTHER = "S-1-15-3-1024-9999999999-8888888888-7777777777-666666666-555555555-444444444-333333333-222222222"
ICACLS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe")


def _icacls(*args: str) -> None:
    subprocess.run([ICACLS, *args, "/Q"], check=True, capture_output=True, timeout=60)


def _tree(root: Path) -> list[tuple[Path, bool]]:
    """A parent whose own inheritable ACE reaches every child, and below it: a folder granted with
    inheritance (with a nested folder and files), a folder granted without, a file, and a folder whose
    DACL is protected from inheritance. Returns what to grant, as runtime_paths does."""
    parent = root / "parent"
    (parent / "lib" / "nested").mkdir(parents=True)
    (parent / "lib" / "nested" / "a.py").write_text("x")
    (parent / "lib" / "b.pyd").write_text("x")
    (parent / "base").mkdir()
    (parent / "base" / "python.exe").write_text("x")
    (parent / "closed").mkdir()
    (parent / "closed" / "c.py").write_text("x")
    (parent / "python312.dll").write_text("x")
    _icacls(str(parent), "/grant", f"*{OTHER}:(OI)(CI)(R)")
    _icacls(str(parent / "closed"), "/inheritance:d")
    return [(parent / "lib", True), (parent / "base", False), (parent / "python312.dll", False),
            (parent / "closed", True)]


def _saved(root: Path) -> dict[str, str]:
    out = root.parent / f"{root.name}.acl"
    _icacls(str(root), "/save", str(out), "/T")
    lines = out.read_bytes().decode("utf-16-le").lstrip("\ufeff").splitlines()
    return dict(zip(lines[0::2], lines[1::2], strict=False))


def test_the_in_process_grant_leaves_the_same_acls_as_icacls(tmp_path):
    by_icacls, in_process = tmp_path / "a" / "t", tmp_path / "b" / "t"   # same names, so same saved paths
    for path, inherit in _tree(by_icacls):
        flags = "(OI)(CI)(RX)" if inherit and path.is_dir() else "(RX)"
        _icacls(str(path), "/grant", f"*{CAP}:{flags}")
    for path, inherit in _tree(in_process):
        appcontainer._grant(None, path, CAP, inherit)
    want, got = _saved(by_icacls), _saved(in_process)
    assert sorted(want) == sorted(got) and len(want) >= 9
    assert {p: got[p] for p in got if got[p] != want[p]} == {}
    nested = next(sddl for path, sddl in got.items() if path.endswith("nested\\a.py"))
    assert CAP in nested   # the folder's grant reached its children


def test_a_second_pass_finds_every_grant_and_writes_nothing(tmp_path, monkeypatch):
    paths = _tree(tmp_path / "t")
    for path, inherit in paths:
        appcontainer._grant(None, path, CAP, inherit)
    before = _saved(tmp_path / "t")
    writes = []
    monkeypatch.setattr(acl, "grant", lambda *a: writes.append(a))
    for path, inherit in paths:
        appcontainer._grant(None, path, CAP, inherit)
        assert acl.has_grant(path, CAP, inherit)
    assert writes == [] and _saved(tmp_path / "t") == before


def test_only_our_own_explicit_ace_with_the_same_flags_counts(tmp_path):
    paths = dict(_tree(tmp_path / "t"))
    lib = tmp_path / "t" / "parent" / "lib"
    assert not acl.has_grant(lib, CAP, True)
    _icacls(str(lib), "/grant", f"*{CAP}:(R)")   # not execute, and no inheritance
    assert not acl.has_grant(lib, CAP, True)
    appcontainer._grant(None, lib, CAP, paths[lib])
    assert acl.has_grant(lib, CAP, True)
    assert not acl.has_grant(lib, CAP, False)   # a grant without inheritance would be another ACE
    assert not acl.has_grant(lib / "nested", CAP, True)   # inherited there, not its own
    assert not acl.has_grant(lib, OTHER, True)   # another SID's (inherited) grant isn't ours


def test_a_wider_grant_already_there_is_kept_and_counts(tmp_path):
    folder = tmp_path / "wide"
    folder.mkdir()
    _icacls(str(folder), "/grant", f"*{CAP}:(OI)(CI)(F)")
    before = _saved(tmp_path)
    assert acl.has_grant(folder, CAP, True)
    appcontainer._grant(None, folder, CAP, True)
    assert _saved(tmp_path) == before   # nothing narrowed, nothing added


def test_a_path_whose_acl_cant_be_read_fails_closed(tmp_path):
    missing = tmp_path / "gone"
    with pytest.raises(OSError):
        acl.has_grant(missing, CAP, False)
    with pytest.raises(OSError, match="exercise runtime read gone"):
        appcontainer._grant(None, missing, CAP, False)
