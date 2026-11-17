"""app_control.py paths that mutation testing (batch 74, docs/quality/mutation-testing.md) showed no
test pinned: the load flag's value and the kernel32 handle's last-error setting, the scan cap, how
far a chain of errors is followed and by which link, the package name an import error gives, and
an engine in IT's folder being reported as not the person's own."""

from __future__ import annotations

import ctypes
import json
from types import SimpleNamespace

from fabric_desktop import app_control


def _winerror(code: int, text: str) -> OSError:
    exc = OSError(text)
    exc.winerror = code  # type: ignore[attr-defined]  # what Python sets on Windows
    return exc


def test_the_file_is_mapped_without_its_dependencies_and_the_last_error_is_kept(monkeypatch):
    opened = []
    k = SimpleNamespace(LoadLibraryExW=lambda *a: None, FreeLibrary=lambda *a: 1)

    def windll(name, **kw):
        opened.append((name, kw))
        return k

    monkeypatch.setattr(ctypes, "WinDLL", windll, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 4551, raising=False)
    assert app_control.DONT_RESOLVE_DLL_REFERENCES == 1
    assert app_control._load_blocked("C:\\env\\a.pyd") is True
    assert opened == [("kernel32", {"use_last_error": True})]


def test_the_scan_checks_at_most_five_thousand_binaries():
    assert app_control.MAX_FILES == 5000


def _chain(depth: int, last: BaseException) -> BaseException:
    """``last`` wrapped ``depth - 1`` times, each wrapper raised ``from`` the one inside it."""
    exc = last
    for i in range(depth - 1):
        outer = RuntimeError(f"wrapper {i}")
        outer.__cause__ = exc
        exc = outer
    return exc


def test_the_twentieth_error_in_a_chain_is_read_and_the_twenty_first_is_not():
    assert app_control.explain(_chain(20, _winerror(1260, "refused"))) is not None
    assert app_control.explain(_chain(21, _winerror(1260, "refused"))) is None


def test_an_error_raised_while_handling_another_is_followed_to_the_reason():
    try:
        try:
            raise _winerror(1260, "This program is blocked by group policy.")
        except OSError:
            raise RuntimeError("the worker didn't start")  # noqa: B904  # context only, no cause
    except RuntimeError as exc:
        assert exc.__cause__ is None and exc.__context__ is not None
        assert "AppLocker" in (app_control.explain(exc) or "")


def test_an_import_names_the_package_without_its_leading_underscore():
    text = app_control.explain(_winerror(4551, "DLL load failed while importing _duckdb: refused")) or ""
    assert "blocked a file of the duckdb package," in text


def test_an_engine_inside_its_policy_folder_is_not_the_persons_own(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    it = tmp_path / "it" / "engine"
    (tmp_path / "pd" / "FabricDesktop").mkdir(parents=True)
    (tmp_path / "pd" / "FabricDesktop" / "policy.json").write_text(json.dumps({"engine_folder": str(it)}))
    assert app_control._own_engine(str(it / "python")) is False
    assert app_control._own_engine(str(tmp_path / "elsewhere")) is True
