"""%autoreload and %aimport: your own modules reload when their files change, as with IPython's
autoreload extension; the engine and installed packages never do."""

import os
import sys
import time

import pytest

from fabric_desktop.notebook import Kernel


def out_text(out):
    assert out["status"] == "ok", out
    return "".join(o.get("text", "") for o in out["outputs"]).strip()


def write(path, text):
    path.write_text(text)
    later = time.time() + 2  # a new mtime even on a coarse clock, and a stale .pyc can't be used
    os.utime(path, (later, later))


@pytest.fixture
def mod(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    yield tmp_path
    for name in ("fd_helpers", "fd_other"):
        sys.modules.pop(name, None)


def test_autoreload_2_picks_up_an_edited_module_and_from_imports(mod):
    (mod / "fd_helpers.py").write_text("def double(x):\n    return x * 2\n")
    k = Kernel()
    assert out_text(k.run("%load_ext autoreload\n%autoreload 2\nimport fd_helpers\nfrom fd_helpers import double\n"
                          "print(fd_helpers.double(2), double(2))")) == "4 4"
    write(mod / "fd_helpers.py", "def double(x):\n    return x * 20\n")
    assert out_text(k.run("print(fd_helpers.double(2), double(2))")) == "40 40"


def test_autoreload_off_and_explicit_follow_aimport(mod):
    (mod / "fd_helpers.py").write_text("V = 1\n")
    (mod / "fd_other.py").write_text("V = 1\n")
    k = Kernel()
    out_text(k.run("%autoreload 1\n%aimport fd_helpers\nimport fd_other\n"))
    write(mod / "fd_helpers.py", "V = 2\n")
    write(mod / "fd_other.py", "V = 2\n")
    assert out_text(k.run("print(fd_helpers.V, fd_other.V)")) == "2 1"
    assert "fd_helpers" in out_text(k.run("%aimport"))
    out_text(k.run("%autoreload off"))
    write(mod / "fd_helpers.py", "V = 3\n")
    assert out_text(k.run("print(fd_helpers.V)")) == "2"
    assert out_text(k.run("%autoreload\nprint(fd_helpers.V)")) == "3"  # no mode: reload changed modules now


def test_a_module_that_fails_to_reload_says_so_and_the_cell_still_runs(mod):
    (mod / "fd_helpers.py").write_text("V = 1\n")
    k = Kernel()
    out_text(k.run("%autoreload 2\nimport fd_helpers"))
    write(mod / "fd_helpers.py", "V = (\n")
    out = k.run("print(fd_helpers.V)")
    assert out["status"] == "ok" and "autoreload of fd_helpers failed" in str(out["outputs"]) and "1" in str(out["outputs"])


def test_the_engine_and_installed_packages_are_never_reloaded():
    from fabric_desktop import autoreload, notebook
    import polars
    r = autoreload.Reloader()
    assert r.own_file(notebook) is None and r.own_file(polars) is None and r.own_file(os) is None


def test_other_extensions_and_bad_modes_say_why():
    k = Kernel()
    assert "needs IPython's own shell" in out_text(k.run("%load_ext sql"))
    out = k.run("%autoreload 7")
    assert out["status"] == "error" and "0, 1, 2 or 3" in out["outputs"][-1]["evalue"]


def test_a_reload_leaves_notebook_values_that_only_equal_a_module_constant_alone(mod):
    (mod / "fd_helpers.py").write_text("DEBUG = False\nVERBOSE = False\nLIMIT = 10\nNAME = 'x'\n")
    k = Kernel()
    out_text(k.run("%autoreload 2\nfrom fd_helpers import LIMIT, VERBOSE\nflag = False\nn = 10\nname = 'x'\n"
                   "limit_copy = LIMIT"))
    write(mod / "fd_helpers.py", "DEBUG = True\nVERBOSE = True\nLIMIT = 20\nNAME = 'y'\n")
    # from-imports follow the module (VERBOSE even though DEBUG had the same old value); copies don't
    assert out_text(k.run("print(LIMIT, VERBOSE, flag, n, name, limit_copy)")) == "20 True False 10 x 10"


def test_user_site_packages_arent_reloaded():
    import site

    from fabric_desktop import autoreload
    assert os.path.normcase(os.path.abspath(site.getusersitepackages())) + os.sep in autoreload._system_roots()
