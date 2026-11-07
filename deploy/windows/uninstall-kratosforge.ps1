<#
.SYNOPSIS
  Removes KratosForge for the Windows account that runs it, keeping that person's data unless told not to.

.DESCRIPTION
  Run as the person (not elevated), with KratosForge closed. It removes:
    - the app's scheduled tasks (Task Scheduler folder \FabricDesktop\, except the agent's task, \FabricDesktop\Agent)
    - the kratosforge:// link handler (HKCU\Software\Classes\kratosforge)
    - the engine (<data>\engine) and the launchers AI tools and VS Code run (<data>\bin)
    - every saved sign-in and token: the Fabric sign-in (msal_cache.bin), the Fabric view's saved sign-in
      and browser profile (fabric-view-signin.bin, fabric-view), the app window's browser profile
      (%LOCALAPPDATA%\com.kratosbi.fabricdesktop), the link listener's token (link-port), and the
      mail server password in Windows Credential Manager (KratosForge/mail-server)
    - kratosforge.exe, when you give -AppPath
  It keeps notebooks, settings, stores and backups in the data folder unless you pass -RemoveData,
  which deletes the whole folder. It never touches Fabric, %ProgramData%\FabricDesktop (IT's policy
  file and the agent's folder), Documents\FabricDesktop, or VS Code's own settings and extensions.
  See docs/install.md, "Uninstall".

.EXAMPLE
  .\uninstall-kratosforge.ps1 -AppPath "$env:LOCALAPPDATA\Programs\KratosForge\kratosforge.exe"

.EXAMPLE
  .\uninstall-kratosforge.ps1 -RemoveData -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string] $AppPath,
    [switch] $RemoveData,
    [string] $DataDir = (Join-Path $env:LOCALAPPDATA "FabricDesktop"),
    # The next three are for tests: a scratch task folder, link key and credential, never the real ones.
    [string] $TaskFolder = "\FabricDesktop\",
    [string] $LinkKey = "HKCU:\Software\Classes\kratosforge",
    [string[]] $Credentials = @("KratosForge/mail-server"),
    [string] $WebViewDir = (Join-Path $env:LOCALAPPDATA "com.kratosbi.fabricdesktop")
)
$ErrorActionPreference = "Stop"
$removed = New-Object System.Collections.Generic.List[string]

$running = Get-Process -Name kratosforge, fabric-desktop -ErrorAction SilentlyContinue
if ($running -and -not $WhatIfPreference) {
    throw "Close KratosForge first (it is running as process $($running.Id -join ', '))."
}

function Remove-IfThere([string] $Path) {
    if (Test-Path -LiteralPath $Path) {
        if ($PSCmdlet.ShouldProcess($Path, "Remove")) {
            Remove-Item -LiteralPath $Path -Recurse -Force
            $removed.Add($Path)
        }
    }
}

# Scheduled tasks the app registered for its schedules. The agent's task is IT's, from install-agent.ps1.
if (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue) {
    foreach ($task in @(Get-ScheduledTask -TaskPath $TaskFolder -ErrorAction SilentlyContinue)) {
        if ($task.TaskName -eq "Agent") { continue }
        if ($PSCmdlet.ShouldProcess("$TaskFolder$($task.TaskName)", "Unregister scheduled task")) {
            Unregister-ScheduledTask -TaskPath $TaskFolder -TaskName $task.TaskName -Confirm:$false
            $removed.Add("task $TaskFolder$($task.TaskName)")
        }
    }
}

Remove-IfThere $LinkKey

foreach ($target in $Credentials) {
    & cmdkey.exe "/list:$target" | Out-Null
    if ($LASTEXITCODE -eq 0 -and (& cmdkey.exe "/list:$target" | Select-String -SimpleMatch $target)) {
        if ($PSCmdlet.ShouldProcess($target, "Delete Windows credential")) {
            & cmdkey.exe "/delete:$target" | Out-Null
            $removed.Add("credential $target")
        }
    }
}

Remove-IfThere $WebViewDir
if ($RemoveData) {
    Remove-IfThere $DataDir
} else {
    foreach ($name in @("engine", "bin", "fabric-view", "msal_cache.bin", "msal_cache.bin.lock",
                        "fabric-view-signin.bin", "link-port", "instance.lock", "instance.handover")) {
        Remove-IfThere (Join-Path $DataDir $name)
    }
}
if ($AppPath) { Remove-IfThere $AppPath }

if ($removed.Count) {
    Write-Host "Removed:"
    $removed | ForEach-Object { Write-Host "  $_" }
} else {
    Write-Host "Nothing to remove."
}
if (-not $RemoveData -and (Test-Path -LiteralPath $DataDir)) {
    Write-Host "Kept your notebooks, settings and stores in $DataDir. Delete that folder to remove them too."
}
if (-not $AppPath) {
    Write-Host "Delete kratosforge.exe (or fabric-desktop.exe) yourself, and any shortcut to it."
}
