"""%autoreload (autoreload.py): mode words, the first mode, which modules are looked at, a file whose time
hasn't moved, the %aimport listing and a reload that fails. Modules are written to tmp_path and removed
from sys.modules after each test."""

from __future__ import annotations

import importlib
import os
import sys
import time

import pytest

from fabric_desktop import autoreload


def own_module(tmp_path, monkeypatch, name, body="VALUE = 1\n"):
    path = tmp_path / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, name, raising=False)
    importlib.invalidate_caches()
    module = importlib.import_module(name)
    monkeypatch.setitem(sys.modules, name, module)  # removed again when the test ends
    return module, path


def touch_later(path, seconds=100):
    later = time.time() + seconds
    os.utime(path, (later, later))
    return os.path.getmtime(path)


@pytest.mark.parametrize(
    ("word", "mode"),
    [("0", 0), ("off", 0), ("1", 1), ("explicit", 1), ("2", 2), ("all", 2), ("3", 3), ("complete", 3)],
)
def test_each_mode_word_sets_its_mode(word, mode):
    r = autoreload.Reloader()
    r.mode = 99
    r.command(word)
    assert r.mode == mode


def test_a_new_reloader_is_off_even_for_a_module_named_with_aimport(tmp_path, monkeypatch):
    module, path = own_module(tmp_path, monkeypatch, "kf_ar_off")
    r = autoreload.Reloader()
    assert r.mode == 0
    r.aimport("kf_ar_off", {})
    path.write_text("VALUE = 2\n", encoding="utf-8")
    touch_later(path)
    assert r.check(namespace={}) == []
    assert module.VALUE == 1


def test_a_module_named_with_aimport_whose_time_has_not_moved_is_not_reloaded(tmp_path, monkeypatch):
    r = autoreload.Reloader()
    r.command("1")  # before the module exists, so only %aimport remembers its time
    _module, path = own_module(tmp_path, monkeypatch, "kf_ar_same")
    mtime = touch_later(path)  # later than when the reloader started
    r.aimport("kf_ar_same", {})
    assert r._seen["kf_ar_same"] == mtime
    assert r.check(namespace={}) == []  # the same time as remembered: unchanged


def test_a_changed_module_named_with_aimport_is_reloaded_in_mode_1(tmp_path, monkeypatch):
    module, path = own_module(tmp_path, monkeypatch, "kf_ar_changed")
    r = autoreload.Reloader()
    r.command("1")
    r.aimport("kf_ar_changed", {})
    path.write_text("VALUE = 2\n", encoding="utf-8")
    touch_later(path)
    assert r.check(namespace={}) == ["kf_ar_changed"]
    assert module.VALUE == 2


def test_off_returns_an_empty_list():
    r = autoreload.Reloader()
    result = r.check(namespace={})
    assert result == [] and isinstance(result, list)


def test_skipped_main_and_empty_entries_are_never_reloaded(tmp_path, monkeypatch):
    _skipped, skipped_path = own_module(tmp_path, monkeypatch, "kf_ar_skipped")
    _main, main_path = own_module(tmp_path, monkeypatch, "kf_ar_main")
    _kept, kept_path = own_module(tmp_path, monkeypatch, "kf_ar_kept")
    monkeypatch.setitem(sys.modules, "__main__", sys.modules["kf_ar_main"])
    monkeypatch.setitem(sys.modules, "kf_ar_none", None)
    r = autoreload.Reloader()
    r.command("2")
    r.aimport("-kf_ar_skipped", {})
    for path in (skipped_path, main_path, kept_path):
        touch_later(path)
    reloaded = r.check(namespace={})
    assert "kf_ar_kept" in reloaded
    assert "kf_ar_skipped" not in reloaded and "__main__" not in reloaded and "kf_ar_none" not in reloaded


def test_aimport_with_no_names_lists_both_sets_and_says_none_when_empty():
    r = autoreload.Reloader()
    assert r.aimport("", {}) == "Modules to reload: none\nModules to skip: none"
    r.explicit.update({"b", "a"})
    r.skipped.add("c")
    assert r.aimport("  ", {}) == "Modules to reload: a, b\nModules to skip: c"


def test_a_reload_that_fails_shows_the_last_frame_and_the_cell_still_runs(tmp_path, monkeypatch, capsys):
    _module, path = own_module(tmp_path, monkeypatch, "kf_ar_broken")
    r = autoreload.Reloader()
    r.command("2")
    path.write_text("VALUE = 1\nraise RuntimeError('broken on purpose')\n", encoding="utf-8")
    touch_later(path)
    assert r.check(namespace={}) == []
    err = capsys.readouterr().err
    assert "[autoreload of kf_ar_broken failed]" in err
    assert "RuntimeError: broken on purpose" in err
    assert "kf_ar_broken.py" in err  # the frame where it failed is shown
