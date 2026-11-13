"""%autoreload (autoreload.py): an embedded Python with no user site, %aimport -name, and a module whose
file has gone. Modules are stand-ins registered for the test only."""

from __future__ import annotations

import sys
from types import ModuleType

from fabric_desktop import autoreload


def test_a_python_with_no_user_site_still_knows_its_own_folders(monkeypatch):
    def none():
        raise AttributeError("no user site in an embedded Python")

    monkeypatch.setattr(autoreload.site, "getusersitepackages", none)
    roots = autoreload._system_roots()
    assert roots and all(r.endswith(autoreload.os.sep) for r in roots)


def test_aimport_with_a_dash_skips_a_module_it_was_reloading():
    r = autoreload.Reloader()
    r.explicit.add("helpers")
    assert r.aimport("-helpers", {}) is None
    assert "helpers" in r.skipped and "helpers" not in r.explicit


def gone_module(tmp_path, monkeypatch, name="kf_gone_helper"):
    module = ModuleType(name)
    module.__file__ = str(tmp_path / f"{name}.py")  # never written: the file has gone
    monkeypatch.setitem(sys.modules, name, module)
    return name


def test_a_module_whose_file_has_gone_is_left_alone(tmp_path, monkeypatch):
    name = gone_module(tmp_path, monkeypatch)
    r = autoreload.Reloader()
    r.command("2")  # remembers every module it can; the gone one has no time to remember
    assert name not in r._seen
    assert name not in r.check(namespace={})
