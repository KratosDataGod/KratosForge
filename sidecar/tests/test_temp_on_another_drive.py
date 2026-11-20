"""A file finished in a temporary place is made next to where it goes, not in the temp folder.

On a PC whose TEMP is on another drive (D:\\Temp, or a RAM disk a VDI image sets up), os.replace from the
temp folder into the data folder fails ("The system cannot move the file to a different disk drive",
WinError 17; EXDEV elsewhere), and a move that falls back to copying can leave half a file when the disk
fills (a full disk itself: test_disk_full.py). So a module that moves files into place (os.replace,
os.rename, shutil.move or jsonfile.replace) makes its temporary files with ``dir=`` beside the target. The ones listed below use the temp folder only
for scratch they read back, never for a file they move.
"""

from __future__ import annotations

import ast
import errno
import os
from pathlib import Path


from fabric_desktop import jsonfile

PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"
_MOVES = {("os", "replace"), ("os", "rename"), ("shutil", "move"), ("jsonfile", "replace")}
_TEMPS = {"mkstemp", "NamedTemporaryFile", "mkdtemp", "TemporaryDirectory"}

SCRATCH = {
    ("backup.py", "TemporaryDirectory"): "SQLite's backup copy, read back into memory",
    ("academy/appcontainer.py", "TemporaryDirectory"): "icacls /save output, read back",
}


def test_modules_that_move_files_into_place_make_their_temporary_files_beside_them():
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        calls = [n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)]
        if not any(isinstance(c.func.value, ast.Name) and (c.func.value.id, c.func.attr) in _MOVES for c in calls):
            continue
        for c in calls:
            if c.func.attr in _TEMPS and not any(k.arg == "dir" for k in c.keywords) and (rel, c.func.attr) not in SCRATCH:
                found.append(f"{rel}:{c.lineno} {c.func.attr}")
    assert not found, f"Make these beside the file they become (dir=), or list them in SCRATCH with why: {found}"


def test_a_save_never_crosses_drives_even_when_temp_is_elsewhere(tmp_path, monkeypatch):
    elsewhere = tmp_path / "other-drive"
    elsewhere.mkdir()
    monkeypatch.setenv("TMP", str(elsewhere))
    monkeypatch.setenv("TEMP", str(elsewhere))
    monkeypatch.setenv("TMPDIR", str(elsewhere))
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(elsewhere))
    target = tmp_path / "data" / "settings.json"
    target.parent.mkdir()
    real = os.replace

    def same_drive_only(src, dst):   # what Windows does across drives
        if Path(src).parent != Path(dst).parent:
            raise OSError(errno.EXDEV, "The system cannot move the file to a different disk drive")
        return real(src, dst)

    monkeypatch.setattr(jsonfile.os, "replace", same_drive_only)
    jsonfile.write_json(target, {"a": 1})
    assert jsonfile.read_json(target, {}) == {"a": 1}
    assert list(elsewhere.iterdir()) == []
    assert sorted(p.name for p in target.parent.iterdir()) == ["settings.json"]

