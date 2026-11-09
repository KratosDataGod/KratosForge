<#
.SYNOPSIS
  Installs the KratosForge agent on a Windows server as a startup task that restarts on failure.

.DESCRIPTION
  Creates C:\ProgramData\FabricDesktop, installs the sidecar into a virtual environment under
  C:\Program Files\FabricDesktopAgent, and registers the Task Scheduler task \FabricDesktop\Agent.
  The task starts at boot with no one signed in, runs as the account you give (a gMSA such as
  CONTOSO\fd-agent$ is best: no password to manage), and restarts every minute if it stops.
  Run from an elevated PowerShell in the repository root. See docs/agent.md, "Windows".

  It refuses a folder that is a link (junction or symbolic link), and a settings file that someone
  other than Administrators or SYSTEM owns, since anyone may create C:\ProgramData\FabricDesktop
  before the install. Check such a file, then pass -Force to keep it.

.EXAMPLE
  .\deploy\windows\install-agent.ps1 -Account 'CONTOSO\fd-agent$'
#>
param(
    [Parameter(Mandatory = $true)] [string] $Account,
    [string] $Python = "py -3.12",
    [string] $InstallDir = "$env:ProgramFiles\FabricDesktopAgent",
    [string] $DataDir = "$env:ProgramData\FabricDesktop",
    [switch] $Force   # keep an agent.toml or policy.json that Administrators or SYSTEM don't own
)
$ErrorActionPreference = "Stop"

# Anyone may create these before the install. A link would send everything below, done elevated, to
# its target, and a settings file someone else made would be kept and then look like an admin's.
$admins = [System.Security.Principal.SecurityIdentifier]'S-1-5-32-544'
$system = [System.Security.Principal.SecurityIdentifier]'S-1-5-18'
function Assert-PlainAndAdminOwned {
  foreach ($path in @($DataDir, "$DataDir\agent", "$DataDir\agent.toml", "$DataDir\policy.json")) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if (-not $item) { continue }
    if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "$path is a link (junction or symbolic link), not a plain folder or file. Remove it, then run this again."
    }
    if (-not $item.PSIsContainer) {
        $owner = (Get-Acl -LiteralPath $path).GetOwner([System.Security.Principal.SecurityIdentifier])
        if ($owner -ne $admins -and $owner -ne $system -and -not $Force) {
            $who = try { $owner.Translate([System.Security.Principal.NTAccount]).Value } catch { $owner.Value }
            throw "$path is owned by $who, not Administrators or SYSTEM, so someone may have made it before the install. Check what it says, then run this again with -Force to keep it, or delete it."
        }
    }
  }
}
Assert-PlainAndAdminOwned   # before creating anything inside them
New-Item -ItemType Directory -Force -Path $InstallDir, $DataDir | Out-Null
Assert-PlainAndAdminOwned
New-Item -ItemType Directory -Force -Path "$DataDir\agent" | Out-Null
Assert-PlainAndAdminOwned
Invoke-Expression "$Python -m venv `"$InstallDir\venv`""
# The exact versions CI tested (SH-9): without them pip takes whatever is newest on PyPI.
& "$InstallDir\venv\Scripts\python.exe" -m pip install "$PSScriptRoot\..\..\sidecar" -c "$PSScriptRoot\..\..\sidecar\constraints.txt"
if ($LASTEXITCODE) { throw "pip couldn't install the agent (exit code $LASTEXITCODE)" }

if (-not (Test-Path "$DataDir\agent.toml")) {
    Copy-Item "$PSScriptRoot\..\agent.example.toml" "$DataDir\agent.toml"
    Write-Host "Edit $DataDir\agent.toml, then run: $InstallDir\venv\Scripts\fabric-desktop.exe agent check"
}
# Only administrators and SYSTEM may change anything here; the agent's account reads the settings and
# writes only its home (queue, logs, incident ages), and other users may read the folder (the app reads
# policy.json from it) but not its home. Inheritance from ProgramData is cut, because there every user
# may create files, which would let anyone plant one the agent trusts. Anything already in the folders
# gets the same rights and is owned by Administrators, so a file someone made first doesn't stay theirs.
$users = [System.Security.Principal.SecurityIdentifier]'S-1-5-32-545'
$agent = [System.Security.Principal.NTAccount]$Account
function Set-OnlyTheseRights([string] $Path, [object[]] $Rules) {
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)   # nothing inherited
    $acl.SetOwner($admins)
    foreach ($rule in $Rules) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            $rule[0], $rule[1], 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
    if (Get-ChildItem -LiteralPath $Path -Force) {
        # /L: a link inside is changed itself, never what it points to
        icacls "$Path\*" /setowner "*S-1-5-32-544" /T /C /L /Q | Out-Null
        icacls "$Path\*" /reset /T /C /L /Q | Out-Null   # children take the folder's rights only
        if ($LASTEXITCODE) { throw "couldn't set the rights on what is already in $Path (icacls exit code $LASTEXITCODE)" }
    }
}
Assert-PlainAndAdminOwned   # again: pip took a while
Set-OnlyTheseRights $DataDir @(@($admins, 'FullControl'), @($system, 'FullControl'), @($users, 'ReadAndExecute'), @($agent, 'ReadAndExecute'))
Set-OnlyTheseRights "$DataDir\agent" @(@($admins, 'FullControl'), @($system, 'FullControl'), @($agent, 'Modify'))

$exe = "$InstallDir\venv\Scripts\fabric-desktop.exe"
$action = New-ScheduledTaskAction -Execute $exe -Argument "agent run" -WorkingDirectory "$DataDir\agent"
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries
if ($Account.EndsWith('$')) {
    $principal = New-ScheduledTaskPrincipal -UserId $Account -LogonType Password   # gMSA: Windows fetches the password
} else {
    $principal = New-ScheduledTaskPrincipal -UserId $Account -LogonType S4U        # no stored password, no network shares
}
Register-ScheduledTask -TaskPath "\FabricDesktop\" -TaskName "Agent" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Registered \FabricDesktop\Agent. Start it now with: Start-ScheduledTask -TaskPath '\FabricDesktop\' -TaskName Agent"
