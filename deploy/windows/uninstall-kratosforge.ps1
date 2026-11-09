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
      (%LOCALAPPDATA%\com.kratosbi.fabricdesktop), the link listener's token (link-port), the VS Code
      pairing hello (editor-port), sealed copies a cut-off save left (*.tmp), and the mail server password in Windows Credential Manager (KratosForge/mail-server)
    - kratosforge.exe, when you give -AppPath
  It keeps notebooks, settings, stores and backups in the data folder unless you pass -RemoveData,
  which deletes everything in it except the audit trails (<data>\audit and <data>\integrations\audit),
  which an organization may have to keep. Add -RemoveAuditTrail to delete those too. It never touches Fabric, %ProgramData%\FabricDesktop (IT's policy
  file and the agent's folder), Documents\FabricDesktop, or VS Code's own settings and extensions.
  See docs/install.md, "Uninstall".

.EXAMPLE
  .\uninstall-kratosforge.ps1 -AppPath "$env:LOCALAPPDATA\Programs\KratosForge\kratosforge.exe"

.EXAMPLE
  .\uninstall-kratosforge.ps1 -RemoveData -WhatIf

.EXAMPLE
  .\uninstall-kratosforge.ps1 -RemoveData -RemoveAuditTrail
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string] $AppPath,
    [switch] $RemoveData,
    [switch] $RemoveAuditTrail,
    [string] $DataDir = (Join-Path $env:LOCALAPPDATA "FabricDesktop"),
    # The next three are for tests: a scratch task folder, link key and credential, never the real ones.
    [string] $TaskFolder = "\FabricDesktop\",
    [string] $LinkKey = "HKCU:\Software\Classes\kratosforge",
    [string[]] $Credentials = @("KratosForge/mail-server"),
    [string] $WebViewDir = (Join-Path $env:LOCALAPPDATA "com.kratosbi.fabricdesktop")
)
$ErrorActionPreference = "Stop"
$removed = New-Object System.Collections.Generic.List[string]

# Only ever the folders and file this script is about: a full path ending in the expected name, so an
# unset LOCALAPPDATA (a path relative to the current folder) or a mistyped -AppPath deletes nothing.
function Assert-Target([string] $Path, [string[]] $Leaves, [string] $What) {
    if (-not [IO.Path]::IsPathRooted($Path) -or $Leaves -notcontains (Split-Path -Leaf $Path)) {
        throw "Not removing $What '$Path': it must be a full path ending in $($Leaves -join ' or ')."
    }
}
Assert-Target $DataDir @("FabricDesktop") "the data folder"
Assert-Target $WebViewDir @("com.kratosbi.fabricdesktop") "the app window's profile"
if ($AppPath) { Assert-Target $AppPath @("kratosforge.exe", "fabric-desktop.exe") "the app" }

if ($RemoveAuditTrail -and -not $RemoveData) {
    throw "-RemoveAuditTrail goes with -RemoveData: without it the data folder, audit trail included, is kept."
}
# The audit trails, relative to the data folder: -RemoveData keeps them unless -RemoveAuditTrail is given.
$AuditTrails = @("audit", "integrations\audit")

$running = Get-Process -Name kratosforge, fabric-desktop -ErrorAction SilentlyContinue
if ($running -and -not $WhatIfPreference) {
    throw "Close KratosForge first (it is running as process $($running.Id -join ', '))."
}

# Deletes a file or folder without following links: a junction or symbolic link inside it is removed
# as a link, never what it points to (Remove-Item -Recurse on Windows PowerShell 5.1 can follow one).
function Remove-Tree([string] $Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        if ($item.PSIsContainer) { [IO.Directory]::Delete($item.FullName, $false) } else { [IO.File]::Delete($item.FullName) }
        return
    }
    if ($item.PSIsContainer) {
        foreach ($child in @(Get-ChildItem -LiteralPath $item.FullName -Force)) { Remove-Tree $child.FullName }
        $item.Attributes = [IO.FileAttributes]::Directory
        [IO.Directory]::Delete($item.FullName, $false)
    } else {
        $item.Attributes = [IO.FileAttributes]::Normal
        [IO.File]::Delete($item.FullName)
    }
}

function Remove-IfThere([string] $Path) {
    if (Test-Path -LiteralPath $Path) {
        if ($PSCmdlet.ShouldProcess($Path, "Remove")) {
            if ($Path -like "HK*:*") { Remove-Item -LiteralPath $Path -Recurse -Force } else { Remove-Tree $Path }
            $removed.Add($Path)
        }
    }
}

# Everything but the audit trails. A folder holding one (integrations) is emptied around it; anything
# else, a link included, is removed whole (Remove-Tree never follows a link).
function Remove-AllBut([string] $Folder, [string] $Rel) {
    foreach ($child in @(Get-ChildItem -LiteralPath $Folder -Force)) {
        $childRel = if ($Rel) { "$Rel\$($child.Name)" } else { $child.Name }
        if ($AuditTrails -contains $childRel) { continue }
        $holdsTrail = @($AuditTrails | Where-Object { $_ -like "$childRel\*" }).Count -gt 0
        if ($holdsTrail -and $child.PSIsContainer -and -not ($child.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Remove-AllBut $child.FullName $childRel
        } else {
            Remove-IfThere $child.FullName
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
if ($RemoveData -and $RemoveAuditTrail) {
    Remove-IfThere $DataDir
} elseif ($RemoveData) {
    if (Test-Path -LiteralPath $DataDir) { Remove-AllBut $DataDir "" }
} else {
    foreach ($name in @("engine", "bin", "fabric-view", "msal_cache.bin", "msal_cache.bin.lock",
                        "fabric-view-signin.bin", "fabric-view-signin.tmp", "link-port", "editor-port",
                        "instance.lock", "instance.handover")) {
        Remove-IfThere (Join-Path $DataDir $name)
    }
    # A sign-in cache save cut off part way leaves its sealed copy as msal_cache.bin.<process id>.tmp.
    if (Test-Path -LiteralPath $DataDir) {
        foreach ($left in @(Get-ChildItem -LiteralPath $DataDir -Filter "msal_cache.bin.*.tmp" -File -Force)) {
            Remove-IfThere $left.FullName
        }
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
if ($RemoveData -and -not $RemoveAuditTrail) {
    $kept = @($AuditTrails | ForEach-Object { Join-Path $DataDir $_ } | Where-Object { Test-Path -LiteralPath $_ })
    if ($kept.Count) {
        Write-Host "Kept the audit trail, which your organization may have to keep: $($kept -join ', '). Add -RemoveAuditTrail to delete it too."
    }
}
if (-not $AppPath) {
    Write-Host "Delete kratosforge.exe (or fabric-desktop.exe) yourself, and any shortcut to it."
}
