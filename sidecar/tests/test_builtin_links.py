"""Built-in resource files that %run -b and %pip install -r read stay inside the notebook's
resources once links are followed: a symlink, a Windows junction or a linked builtin folder that
leads elsewhere is refused, as kratosforge:// links refuse a path that leaves its folder."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fabric_desktop import notebook_resources, pip_magic
from fabric_desktop.notebook import Kernel
from fabric_desktop.notebook_resources import OUTSIDE, Resources

needs_symlinks = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need Developer Mode on Windows")


@pytest.fixture
def folder(tmp_path):
    res = tmp_path / "N.resources"
    Resources(res).add("setup.py", b"value = 'inside'\n")
    (res / "builtin" / "sub").mkdir()
    (res / "builtin" / "sub" / "more.py").write_text("value = 'nested'\n", encoding="utf-8")
    (tmp_path / "outside.py").write_text("value = 'outside'\n", encoding="utf-8")
    return res


def _run(folder: Path, line: str) -> dict:
    k = Kernel()
    k.resources_dir = str(folder)
    return k.run(line + "\nprint(value)")


def _text(out: dict) -> str:
    return "".join(o.get("text", "") or o.get("evalue", "") for o in out["outputs"]).strip()


def test_a_normal_file_and_a_nested_one_still_run(folder):
    assert _text(_run(folder, "%run -b setup.py")) == "inside"
    assert _text(_run(folder, "%run -b sub/more.py")) == "nested"
    assert notebook_resources.real_path("builtin/setup.py", folder) == os.path.realpath(folder / "builtin" / "setup.py")


@needs_symlinks
def test_a_symlink_that_leads_outside_is_refused(folder, tmp_path):
    (folder / "builtin" / "link.py").symlink_to(tmp_path / "outside.py")
    out = _run(folder, "%run -b link.py")
    assert out["status"] == "error" and OUTSIDE in _text(out), out
    with pytest.raises(PermissionError, match="links outside"):
        notebook_resources.real_path("builtin/link.py", folder)


@needs_symlinks
def test_a_symlinked_folder_inside_builtin_that_leads_outside_is_refused(folder, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "x.py").write_text("value = 1\n", encoding="utf-8")
    (folder / "builtin" / "away").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(PermissionError, match="links outside"):
        notebook_resources.real_path("builtin/away/x.py", folder)


@needs_symlinks
def test_a_builtin_folder_that_is_itself_a_link_elsewhere_is_refused(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "x.py").write_text("value = 1\n", encoding="utf-8")
    res = tmp_path / "N.resources"
    res.mkdir()
    (res / "builtin").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(PermissionError, match="links outside"):
        notebook_resources.real_path("builtin/x.py", res)


@needs_symlinks
def test_a_link_that_stays_inside_builtin_still_works(folder):
    (folder / "builtin" / "alias.py").symlink_to(folder / "builtin" / "setup.py")
    assert _text(_run(folder, "%run -b alias.py")) == "inside"


@needs_symlinks
def test_pip_requirements_from_builtin_follow_the_same_rule(folder, tmp_path):
    (tmp_path / "reqs.txt").write_text("evil\n", encoding="utf-8")
    (folder / "builtin" / "requirements.txt").symlink_to(tmp_path / "reqs.txt")
    with pytest.raises(PermissionError, match="links outside"):
        pip_magic.read_text("builtin/requirements.txt", str(folder))
    (folder / "builtin" / "ok.txt").write_text("polars\n", encoding="utf-8")
    assert pip_magic.read_text("builtin/ok.txt", str(folder)) == "polars\n"


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows thing")
def test_a_junction_that_leads_outside_is_refused_on_windows(folder, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "x.py").write_text("value = 1\n", encoding="utf-8")
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(folder / "builtin" / "away"), str(tmp_path / "elsewhere")],
                          capture_output=True, text=True, timeout=30)
    if made.returncode != 0:
        pytest.skip(f"couldn't make a junction here: {made.stderr.strip() or made.stdout.strip()}")
    out = _run(folder, "%run -b away/x.py")
    assert out["status"] == "error" and OUTSIDE in _text(out), out
