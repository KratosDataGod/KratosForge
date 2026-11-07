"""deploy/windows/uninstall-kratosforge.ps1: what it removes matches what the app leaves, and on
Windows it removes that and keeps the person's data."""

from __future__ import annotations

import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "windows" / "uninstall-kratosforge.ps1"


def test_it_removes_every_sign_in_and_token_the_app_keeps_outside_backups():
    text = SCRIPT.read_text(encoding="utf-8")
    from fabric_desktop import backup, local_schedules
    from fabric_desktop.workloads import deliver
    # Backups skip exactly the sign-in material and tokens: an uninstall that keeps data removes them.
    for name in backup.SKIP_FILES:
        assert f'"{name}"' in text, name
    assert '"fabric-view"' in text and '"engine"' in text and '"bin"' in text
    assert f'"{deliver.PASSWORD_TARGET}"' in text
    assert f'"\\{local_schedules.TASK_FOLDER}\\"' in text
    assert 'HKCU:\\Software\\Classes\\kratosforge"' in text
    assert "com.kratosbi.fabricdesktop" in text  # tauri.conf.json's identifier: the app window's profile
    assert '"identifier": "com.kratosbi.fabricdesktop"' in (ROOT / "app" / "src-tauri" / "tauri.conf.json").read_text()
    # The agent's task, IT's policy and the person's documents are never touched.
    assert '-eq "Agent") { continue }' in text
    assert "ProgramData" not in re.sub(r"(?s)<#.*?#>", "", text)
    assert "SupportsShouldProcess" in text and "Close KratosForge first" in text


def _ps(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-File", str(SCRIPT), *args], capture_output=True, text=True, timeout=180)


@pytest.mark.skipif(sys.platform != "win32", reason="runs the script on Windows")
def test_on_windows_it_removes_the_app_and_keeps_the_data_until_asked(tmp_path):
    tag = uuid.uuid4().hex[:8]
    data, webview, app = tmp_path / "FabricDesktop", tmp_path / "webview", tmp_path / "kratosforge.exe"
    for rel in ("engine/abc/python.exe", "bin/fabric-desktop-mcp.cmd", "fabric-view/Default/Cookies", "msal_cache.bin",
                "fabric-view-signin.bin", "link-port", "notebooks/keep.ipynb", "settings.json", "workloads.sqlite"):
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_text("x")
    (webview / "EBWebView").mkdir(parents=True)
    app.write_text("exe")
    folder, key, cred = f"\\KFTest{tag}\\", f"HKCU:\\Software\\Classes\\kratosforge-test-{tag}", f"KratosForge-test/{tag}"
    setup = (f"New-Item -Path '{key}' -Force | Out-Null; "
             f"Register-ScheduledTask -TaskPath '{folder}' -TaskName 'Schedule1' -Action (New-ScheduledTaskAction -Execute 'cmd.exe') | Out-Null; "
             f"Register-ScheduledTask -TaskPath '{folder}' -TaskName 'Agent' -Action (New-ScheduledTaskAction -Execute 'cmd.exe') | Out-Null; "
             f"cmdkey.exe /generic:{cred} /user:u /pass:p | Out-Null")
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", setup], check=True, timeout=120)
    common = ["-DataDir", str(data), "-TaskFolder", folder, "-LinkKey", key, "-Credentials", cred, "-WebViewDir", str(webview)]
    try:
        dry = _ps(*common, "-AppPath", str(app), "-WhatIf")
        assert dry.returncode == 0, dry.stderr
        assert (data / "msal_cache.bin").exists() and app.exists()
        done = _ps(*common, "-AppPath", str(app))
        assert done.returncode == 0, done.stdout + done.stderr
        for gone in ("engine", "bin", "fabric-view", "msal_cache.bin", "fabric-view-signin.bin", "link-port"):
            assert not (data / gone).exists(), gone
        assert not webview.exists() and not app.exists()
        for kept in ("notebooks/keep.ipynb", "settings.json", "workloads.sqlite"):
            assert (data / kept).exists(), kept
        check = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                                f"(Test-Path '{key}'); @(Get-ScheduledTask -TaskPath '{folder}' -ErrorAction SilentlyContinue).TaskName"],
                               capture_output=True, text=True, timeout=120).stdout
        assert "False" in check and "Schedule1" not in check and "Agent" in check
        creds = subprocess.run(["cmdkey.exe", f"/list:{cred}"], capture_output=True, text=True, timeout=60).stdout
        assert "NONE" in creds, creds
        assert "Kept your notebooks" in done.stdout
        everything = _ps(*common, "-RemoveData")
        assert everything.returncode == 0, everything.stderr
        assert not data.exists()
    finally:
        subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                        f"Remove-Item '{key}' -Recurse -Force -ErrorAction SilentlyContinue; "
                        f"Get-ScheduledTask -TaskPath '{folder}' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false; "
                        f"cmdkey.exe /delete:{cred} | Out-Null"], timeout=120)
