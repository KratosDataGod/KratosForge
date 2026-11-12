"""A program or package file that Windows' application control blocks says so in plain words.

Under AppLocker or App Control for Business (WDAC), Windows refuses to start a program (error 1260
or 4551) or to load an unsigned ``.pyd`` (4551 or 577, which Python reports as "DLL load failed").
The engine then names the policy and what IT can do, instead of a raw "[WinError 1260]". These
simulate Windows' errors; a real policy on a pilot PC is UNVERIFIED.
"""

import json

import pytest

from fabric_desktop import app_control, notebook, server


def _winerror(code: int, text: str) -> OSError:
    exc = OSError(None, text)
    exc.winerror = code  # type: ignore[attr-defined]  # what Python sets on Windows
    return exc


@pytest.mark.parametrize("code,by", [(1260, "AppLocker"), (4551, "WDAC"), (577, "WDAC")])
def test_a_program_the_policy_blocks_names_the_policy_and_what_it_can_do(code, by):
    text = app_control.explain(_winerror(code, "refused"))
    assert text and text.startswith("Your organization's application control (") and by in text
    assert "Ask IT to allow" in text and "Application control" in text


def test_a_blocked_package_file_names_the_package():
    exc = ImportError("DLL load failed while importing _duckdb: An Application Control policy has blocked this file.")
    text = app_control.explain(exc)
    assert text and "duckdb package" in text and "WDAC" in text
    unsigned = ImportError("DLL load failed while importing lib: Windows cannot verify the digital signature for this file.")
    assert "lib package" in (app_control.explain(unsigned) or "")


def test_the_reason_is_found_behind_another_error():
    try:
        try:
            raise _winerror(1260, "This program is blocked by group policy.")
        except OSError as inner:
            raise RuntimeError("The notebook's Python process didn't start.") from inner
    except RuntimeError as outer:
        assert "AppLocker" in (app_control.explain(outer) or "")


def test_other_errors_and_a_line_already_said_are_left_alone():
    assert app_control.explain(_winerror(5, "Access is denied")) is None
    assert app_control.explain(ImportError("DLL load failed while importing x: The specified module could not be found.")) is None
    assert app_control.explain(ValueError("bad")) is None
    said = app_control.explain(_winerror(4551, "x")) or ""
    assert app_control.explain(OSError(said)) is None


def test_a_cell_whose_import_is_blocked_says_so_first():
    exc = ImportError("DLL load failed while importing _polars: An Application Control policy has blocked this file.")
    text = notebook.plain_error(exc)
    assert text.startswith("Your organization's application control") and text.endswith(f"({exc})")


def test_an_rpc_call_that_starts_a_blocked_program_gets_the_line(monkeypatch, tmp_path):
    class Blocked:
        def environment(self):
            raise _winerror(1260, "This program is blocked by group policy. For more information, contact your system administrator")

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    reply = server.handle(Blocked(), json.dumps({"id": 1, "method": "environment", "params": {}}))  # type: ignore[arg-type]
    assert reply["error"].startswith("Your organization's application control (AppLocker)")
    assert "blocked by group policy" in reply["error"]  # Windows' own words stay for IT, after the line
