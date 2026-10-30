"""Schedules on this PC that also run while you're signed out of Windows (PB-048, Chris chose the
Windows password prompt on 2026-10-28).

Task Scheduler runs such a task with the "Password" logon type: Windows keeps your password with the
task, so the run gets your full profile and your saved Fabric sign-in (DPAPI) even with nobody signed
in. The password is asked for by Windows' own credential prompt, in a PowerShell process of its own
that hands it straight to Task Scheduler: the engine never reads, logs or stores it.

The script below is fixed text. What changes between calls (the task's name and its definition file)
reaches it through environment variables, never by building a command line, and the name is only
ever a schedule id (hex) checked here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from typing import Any

_log = logging.getLogger(__name__)
_ID = re.compile(r"[0-9a-f]{1,32}")

# Exit codes the scripts use, besides 0.
CANCELLED = 3
OTHER_ACCOUNT = 4
FAILED = 5

# Task Scheduler's last result when the task couldn't log on: the password changed (1326), or this
# account may not log on as a batch job, which an organization can turn off (1385).
LOGON_FAILURE = {0x8007052E, 1326}
NOT_ALLOWED = {0x80070569, 1385}

_PRELUDE = """
$ErrorActionPreference = 'Stop'
Import-Module "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\Modules\\ScheduledTasks"
$folder = '\\{folder}\\'
"""

PROMPT_SECONDS = 5 * 60  # a prompt nobody answers (or one stuck on a hidden console) ends here

REGISTER = _PRELUDE + """
$me = "$env:USERDOMAIN\\$env:USERNAME"
try { $c = Get-Credential -UserName $me -Message 'KratosForge: run this schedule while you are signed out of Windows. Windows keeps your password with the task; KratosForge never sees it.' }
catch { exit 3 }
if ($null -eq $c) { exit 3 }
if ($c.UserName -ne $me) { exit 4 }
try {
  $xml = Get-Content -LiteralPath $env:KF_TASK_XML -Raw -Encoding Unicode
  Register-ScheduledTask -TaskPath $folder -TaskName $env:KF_TASK_ID -Xml $xml -User $c.UserName -Password $c.GetNetworkCredential().Password -Force | Out-Null
} catch {
  [Console]::Error.WriteLine("hresult=$($_.Exception.HResult) $($_.Exception.Message)")
  exit 5
}
"""

SET_ENABLED = _PRELUDE + """
try {
  if ($env:KF_ENABLED -eq '1') { Enable-ScheduledTask -TaskPath $folder -TaskName $env:KF_TASK_ID | Out-Null }
  else { Disable-ScheduledTask -TaskPath $folder -TaskName $env:KF_TASK_ID | Out-Null }
} catch {
  [Console]::Error.WriteLine("hresult=$($_.Exception.HResult) $($_.Exception.Message)")
  exit 5
}
"""

LAST_RESULTS = _PRELUDE + """
$out = @{}
Get-ScheduledTask -TaskPath $folder -ErrorAction SilentlyContinue | ForEach-Object {
  $info = Get-ScheduledTaskInfo -TaskPath $folder -TaskName $_.TaskName
  $out[$_.TaskName] = @{ result = [int64]$info.LastTaskResult; logon = [string]$_.Principal.LogonType }
}
$out | ConvertTo-Json -Compress
"""


class PasswordNotSaved(RuntimeError):
    """Windows didn't take the password; nothing about the task changed."""


def check_id(schedule_id: str) -> str:
    if not _ID.fullmatch(schedule_id or ""):
        raise ValueError(f"Not a schedule id: {schedule_id!r}")
    return schedule_id


def _powershell(script: str, folder: str, env: dict[str, str], prompt: bool = False) -> subprocess.CompletedProcess[str]:
    """Windows PowerShell 5.1 by full path (never whichever powershell is first on PATH). Only the
    password script may show a prompt; the others run -NonInteractive."""
    system = os.environ.get("SystemRoot", r"C:\Windows")
    exe = os.path.join(system, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    args = [exe, "-NoProfile", *([] if prompt else ["-NonInteractive"]), "-ExecutionPolicy", "Bypass",
            "-Command", script.replace("{folder}", folder)]
    return subprocess.run(args, capture_output=True, text=True, creationflags=flags, env={**os.environ, **env},
                          check=False, timeout=PROMPT_SECONDS if prompt else 120)


def _explain(res: subprocess.CompletedProcess[str]) -> str:
    if res.returncode == CANCELLED:
        return "You closed the Windows prompt; nothing changed."
    if res.returncode == OTHER_ACCOUNT:
        return "Use your own Windows account in the prompt; nothing changed."
    text = (res.stderr or res.stdout).strip()
    low = text.lower()
    if "0x8007052e" in low or "-2147023570" in low or "password is incorrect" in low:
        return "Windows didn't accept that password; nothing changed."
    if "0x80070569" in low or "-2147023511" in low or "logon type" in low:
        return ("Your organization doesn't allow runs while you're signed out (Windows refused the "
                "'Log on as a batch job' right); nothing changed.")
    return f"Task Scheduler: {text or 'the task was not saved'}"


def register(folder: str, schedule_id: str, xml: str) -> None:
    """Asks for the password with Windows' prompt and saves the task with it. Raises PasswordNotSaved
    (with words for the person) when the prompt is closed or Windows refuses."""
    check_id(schedule_id)
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as f:
        f.write(xml)
    try:
        res = _powershell(REGISTER, folder, {"KF_TASK_XML": f.name, "KF_TASK_ID": schedule_id}, prompt=True)
    except subprocess.TimeoutExpired as e:
        raise PasswordNotSaved("Windows' password prompt wasn't answered within 5 minutes; nothing changed. It may have opened on another screen.") from e
    finally:
        os.unlink(f.name)
    if res.returncode != 0:
        raise PasswordNotSaved(_explain(res))


def set_enabled(folder: str, schedule_id: str, enabled: bool) -> None:
    """Pause or resume without touching the saved password (re-registering would ask for it)."""
    res = _powershell(SET_ENABLED, folder, {"KF_TASK_ID": check_id(schedule_id), "KF_ENABLED": "1" if enabled else "0"})
    if res.returncode != 0:
        raise RuntimeError(_explain(res))


def last_results(folder: str) -> dict[str, dict[str, Any]]:
    """{task name: {"result": Task Scheduler's last result, "logon": its logon type}} for the folder.
    Empty off Windows or when PowerShell can't be asked (the rows then just don't warn)."""
    if sys.platform != "win32":
        return {}
    try:
        res = _powershell(LAST_RESULTS, folder, {})
        data = json.loads(res.stdout or "{}") if res.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        _log.warning("signed-out schedules: could not read Task Scheduler's results: %s", e)
        return {}
    return data if isinstance(data, dict) else {}


def needs_password(result: Any) -> str | None:
    """What a row says about a signed-out task's last result: "password" when Windows needs the new
    password, "not_allowed" when the organization refuses runs while signed out, else None."""
    try:
        code = int(result) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return None
    if code in LOGON_FAILURE:
        return "password"
    if code in NOT_ALLOWED:
        return "not_allowed"
    return None
