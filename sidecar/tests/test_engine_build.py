"""The engine zip is reproducible: the same files give the same zip bytes, the .pyc files name no build
folder, and CI builds the engine twice and fails when the two zips differ (.github/scripts/engine_build.py)."""

from __future__ import annotations

import importlib.util
import marshal
import os
import re
import time
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
EPOCH = "1700000000"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("engine_build", ROOT / ".github" / "scripts" / "engine_build.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


build = _load()


def _engine(root: Path, order: list[str]) -> Path:
    """An engine folder whose files are written in the given order, each with a different mtime."""
    files = {"engine.json": '{"commit": "abc"}\n', "Lib/site-packages/pkg/__init__.py": "X = 1\n",
             "Lib/site-packages/pkg/mod.py": "def f():\n    return 2\n", "python312._pth": "python312.zip\n"}
    for i, name in enumerate(order):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(files[name], encoding="utf-8")
        os.utime(path, (1_600_000_000 + i * 1000, 1_600_000_000 + i * 1000))
    (root / "Lib" / "site-packages" / "empty").mkdir(parents=True)
    return root


def test_the_same_files_give_the_same_zip_whatever_their_times_and_order(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", EPOCH)
    names = ["engine.json", "Lib/site-packages/pkg/__init__.py", "Lib/site-packages/pkg/mod.py", "python312._pth"]
    a = _engine(tmp_path / "a", names)
    b = _engine(tmp_path / "somewhere" / "else", names[::-1])
    build.make_zip(a, tmp_path / "a.zip", [])
    build.make_zip(b, tmp_path / "b.zip", [])
    assert (tmp_path / "a.zip").read_bytes() == (tmp_path / "b.zip").read_bytes()
    assert build.compare(tmp_path / "a.zip", tmp_path / "b.zip") == []
    with zipfile.ZipFile(tmp_path / "a.zip") as z:
        infos = z.infolist()
    assert [i.filename for i in infos] == sorted(i.filename for i in infos)
    assert "Lib/site-packages/empty/" in [i.filename for i in infos]
    assert {i.date_time for i in infos} == {time.gmtime(int(EPOCH))[:6]}
    assert {(i.create_system, i.external_attr) for i in infos if not i.is_dir()} == {(0, 0x20)}


def test_the_update_zip_holds_only_the_paths_it_names_and_their_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", EPOCH)
    root = _engine(tmp_path / "e", ["engine.json", "Lib/site-packages/pkg/__init__.py", "Lib/site-packages/pkg/mod.py", "python312._pth"])
    build.make_zip(root, tmp_path / "u.zip", ["engine.json", "Lib\\site-packages\\pkg" if os.name == "nt" else "Lib/site-packages/pkg"])
    with zipfile.ZipFile(tmp_path / "u.zip") as z:
        assert z.namelist() == ["Lib/", "Lib/site-packages/", "Lib/site-packages/pkg/", "Lib/site-packages/pkg/__init__.py",
                                "Lib/site-packages/pkg/mod.py", "engine.json"]


def test_a_zip_needs_the_commit_time_and_holds_no_links(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    root = _engine(tmp_path / "e", ["engine.json"])
    with pytest.raises(SystemExit, match="SOURCE_DATE_EPOCH"):
        build.make_zip(root, tmp_path / "x.zip", [])
    monkeypatch.setenv("SOURCE_DATE_EPOCH", EPOCH)
    try:
        (root / "link.json").symlink_to(root / "engine.json")
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(SystemExit, match="is a link"):
        build.make_zip(root, tmp_path / "x.zip", [])


def test_compare_names_each_entry_that_differs_and_the_lines_that_changed(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", EPOCH)
    names = ["engine.json", "Lib/site-packages/pkg/__init__.py", "python312._pth"]
    a, b = _engine(tmp_path / "a", names), _engine(tmp_path / "b", names)
    (b / "engine.json").write_text('{"commit": "def"}\n', encoding="utf-8")
    (b / "python312._pth").unlink()
    (b / "new.txt").write_text("x", encoding="utf-8")
    build.make_zip(a, tmp_path / "a.zip", [])
    build.make_zip(b, tmp_path / "b.zip", [])
    assert build.compare(tmp_path / "a.zip", tmp_path / "b.zip") == [
        "only in the first build: python312._pth",
        "only in the second build: new.txt",
        "differs: engine.json (18 and 18 bytes)",
        '    -{"commit": "abc"}',
        '    +{"commit": "def"}']
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1800000000")
    build.make_zip(a, tmp_path / "later.zip", [])
    assert build.compare(tmp_path / "a.zip", tmp_path / "later.zip")[0] == "same contents, different zip metadata: Lib/"


def test_finish_compiles_checked_hash_pycs_that_name_no_build_folder(tmp_path):
    root = tmp_path / "engine"
    site = root / "Lib" / "site-packages"
    (site / "pkg").mkdir(parents=True)
    (site / "pkg" / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    (site / "pkg" / "py2.py").write_text("print 'old'\n", encoding="utf-8")
    info = site / "pkg-1.0.dist-info"
    info.mkdir()
    (info / "direct_url.json").write_text('{"url": "file:///D:/a/_temp/wheels/pkg.whl"}', encoding="utf-8")
    (info / "RECORD").write_text("pkg/mod.py,sha256=x,1\npkg-1.0.dist-info/direct_url.json,sha256=y,2\n"
                                 "pkg-1.0.dist-info/RECORD,,\n", encoding="utf-8")
    assert build.finish(root) == 1, "a file that doesn't compile stays as source, as pip leaves it"
    assert not (info / "direct_url.json").exists()
    assert (info / "RECORD").read_text(encoding="utf-8") == "pkg/mod.py,sha256=x,1\npkg-1.0.dist-info/RECORD,,\n"
    pyc = Path(importlib.util.cache_from_source(str(site / "pkg" / "mod.py"))).read_bytes()
    assert int.from_bytes(pyc[4:8], "little") == 0b11, "checked-hash: valid whatever the file's time"
    code = marshal.loads(pyc[16:])
    assert code.co_filename == "Lib/site-packages/pkg/mod.py"
    assert str(tmp_path).encode() not in pyc
    first = pyc
    build.finish(root)
    assert Path(importlib.util.cache_from_source(str(site / "pkg" / "mod.py"))).read_bytes() == first


def test_main_fails_and_says_why_when_the_zips_differ(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", EPOCH)
    a, b = _engine(tmp_path / "a", ["engine.json"]), _engine(tmp_path / "b", ["engine.json"])
    (b / "engine.json").write_text("{}", encoding="utf-8")
    assert build.main(["zip", str(a), str(tmp_path / "a.zip")]) == 0
    assert build.main(["zip", str(b), str(tmp_path / "b.zip")]) == 0
    assert build.main(["compare", str(tmp_path / "a.zip"), str(tmp_path / "a.zip")]) == 0
    assert build.main(["compare", str(tmp_path / "a.zip"), str(tmp_path / "b.zip")]) == 1
    assert "::error::" in capsys.readouterr().out
    assert build.main(["nope"]) == 2


def _engine_steps() -> list[str]:
    job = CI.split("\n  engine:\n", 1)[1].split("\n  shell:\n", 1)[0]
    return [name or uses for name, uses in re.findall(r"^      - (?:name: (.+)|uses: (\S+))", job, re.M)]


def test_ci_builds_the_engine_twice_and_fails_when_the_zips_differ():
    job = CI.split("\n  engine:\n", 1)[1].split("\n  shell:\n", 1)[0]
    assert len(re.findall(r"^\s+Build-Engine ", job, re.M)) == 2
    assert "--no-compile" in job and "engine_build.py finish" in job
    assert "SOURCE_DATE_EPOCH = git log -1 --format=%ct" in job
    assert "7z " not in job, "the engine zips come from engine_build.py zip, which fixes order and times"
    steps = _engine_steps()
    check = steps.index("Check the engine zip is reproducible")
    assert steps.index("Check the engine starts on its own") < check < steps.index("Sign the engine's packages")
    assert 'engine_build.py compare "$env:RUNNER_TEMP\\first.zip" "$env:RUNNER_TEMP\\second.zip"' in job
