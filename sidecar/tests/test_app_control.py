"""Application control (AppLocker, App Control for Business): what the person is told when it blocks
something the app runs or loads. Windows' own refusal is simulated; a pilot PC checks the real one."""
from __future__ import annotations

import subprocess
from pathlib import Path

from fabric_desktop import app_control, environments, shell


def _env_folder(tmp_path: Path) -> Path:
    site = tmp_path / "site-packages"
    for rel in ("pyarrow/lib.cp312-win_amd64.pyd", "pyarrow/arrow.dll", "pyarrow/__init__.py",
                "numpy/_core/_multiarray_umath.pyd", "_cffi_backend.cp312-win_amd64.pyd"):
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_bytes(b"")
    return site


def test_blocked_packages_names_each_package_with_a_refused_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(app_control.sys, "platform", "win32")
    site = _env_folder(tmp_path)
    blocked = app_control.blocked_packages(site, probe=lambda p: "pyarrow" in p or "_cffi" in p)
    assert set(blocked) == {"pyarrow", "_cffi_backend"}
    assert sorted(blocked["pyarrow"]) == sorted([str(Path("pyarrow/arrow.dll")), str(Path("pyarrow/lib.cp312-win_amd64.pyd"))])
    line = app_control.blocked_line(blocked)
    assert line and "_cffi_backend, pyarrow" in line and "Application control" in line
    assert app_control.blocked_packages(site, probe=lambda p: False) == {}
    assert app_control.blocked_line({}) is None


def test_blocked_packages_checks_nothing_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(app_control.sys, "platform", "linux")
    seen = []
    assert app_control.blocked_packages(_env_folder(tmp_path), probe=lambda p: seen.append(p) or True) == {}
    assert seen == []


def test_an_environment_install_says_when_application_control_blocks_what_it_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(app_control.sys, "platform", "win32")
    monkeypatch.setattr(app_control, "_load_blocked", lambda p: "pyarrow" in p)
    site = _env_folder(tmp_path)
    monkeypatch.setattr(environments.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "Successfully installed pyarrow-20.0\n", ""))
    svc = environments.Environments(tmp_path, python="python")
    job: dict = {"pypi": True}
    pins = tmp_path / "pins.txt"
    pins.write_text("")
    assert svc._install(job, site, pins, ["pyarrow"]) is True
    assert job["log"].rstrip().endswith("(Application control).")
    assert "blocks files in: pyarrow." in job["log"]


def test_a_cmd_cell_that_policy_blocks_says_what_to_use_instead():
    out = ["This program is blocked by group policy. For more information, contact your system administrator."]
    assert shell.cmd_blocked_hint(1, out) == shell.CMD_BLOCKED
    assert "%%powershell" in shell.CMD_BLOCKED
    assert shell.cmd_blocked_hint(0, out) is None
    assert shell.cmd_blocked_hint(1, ["'foo' is not recognized as an internal or external command"]) is None
