"""dpapi.py from a mutation run: the flag that keeps DPAPI from ever showing a prompt is Windows' own
CRYPTPROTECT_UI_FORBIDDEN, 0x1, on both calls. ctypes.WinDLL is stood in for (test_credman_dpapi_calls.Windows),
so this runs on every OS and seals nothing for real."""

import ctypes
import sys

from fabric_desktop import dpapi

from test_credman_dpapi_calls import Windows


def test_protect_and_unprotect_pass_cryptprotect_ui_forbidden(monkeypatch):
    w = Windows()
    monkeypatch.setattr(ctypes, "WinDLL", w.dll, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: w.last_error, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert dpapi._UI_FORBIDDEN == 0x1
    assert dpapi.unprotect(dpapi.protect(b"x")) == b"x"
    assert w.flags == [1, 1]
