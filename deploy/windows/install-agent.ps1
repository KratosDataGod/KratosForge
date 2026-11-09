<#
.SYNOPSIS
  Installs the KratosForge agent on a Windows server as a startup task that restarts on failure.

.DESCRIPTION
  Creates C:\ProgramData\FabricDesktop, installs the sidecar into a virtual environment under
  C:\Program Files\FabricDesktopAgent, and registers the Task Scheduler task \FabricDesktop\Agent.
  The task starts at boot with no one signed in, runs as the account you give (a gMSA such as
  CONTOSO\fd-agent$ is best: no password to manage), and restarts every minute if it stops.
  Run from an elevated PowerShell in the repository root. See docs/agent.md, "Windows".

  Anyone may create C:\ProgramData\FabricDesktop before the install, so it changes nothing while
  anything there is a link (junction or symbolic link) or was created by someone other than an
  administrator; it names those, for IT to check and remove.

.EXAMPLE
  .\deploy\windows\install-agent.ps1 -Account 'CONTOSO\fd-agent$'
#>
param(
    [Parameter(Mandatory = $true)] [string] $Account,
    [string] $Python = "py -3.12",
    [string] $InstallDir = "$env:ProgramFiles\FabricDesktopAgent",
    [string] $DataDir = "$env:ProgramData\FabricDesktop"
)
$ErrorActionPreference = "Stop"

# Anyone may create C:\ProgramData\FabricDesktop before the install and leave files in it. A link would
# send everything below, done elevated, to its target, and re-owning a planted policy.json or
# agent.toml would make the app and the agent trust it. So nothing is changed while anything there is
# a link, or is owned by someone other than Administrators, SYSTEM or TrustedInstaller (in the agent's
# home, also the agent's own account, which writes its queue and logs there).
$admins = [System.Security.Principal.SecurityIdentifier]'S-1-5-32-544'
$system = [System.Security.Principal.SecurityIdentifier]'S-1-5-18'
$trusted = @('S-1-5-32-544', 'S-1-5-18', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
$agentSid = ([System.Security.Principal.NTAccount]$Account).Translate([System.Security.Principal.SecurityIdentifier]).Value
function Get-EverythingUnder([string] $Root) {   # never into a link: that would be its target
    $todo = New-Object System.Collections.Stack
    $todo.Push((Get-Item -LiteralPath $Root -Force))
    while ($todo.Count) {
        $item = $todo.Pop()
        $item
        if ($item.PSIsContainer -and -not ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            Get-ChildItem -LiteralPath $item.FullName -Force | ForEach-Object { $todo.Push($_) }
        }
    }
}
function Assert-PlainAndAdminOwned {
    if (-not (Test-Path -LiteralPath $DataDir)) { return }
    $agentHome = (Join-Path $DataDir 'agent') + '\'
    $odd = @()
    foreach ($item in Get-EverythingUnder $DataDir) {
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "$($item.FullName) is a link (junction or symbolic link). Nothing was changed. Remove it, then run this again."
        }
        $owner = (Get-Acl -LiteralPath $item.FullName).GetOwner([System.Security.Principal.SecurityIdentifier]).Value
        $mine = $owner -eq $agentSid -and $item.FullName.StartsWith($agentHome, [StringComparison]::OrdinalIgnoreCase)
        if ($trusted -notcontains $owner -and -not $mine) {
            $who = try { ([System.Security.Principal.SecurityIdentifier]$owner).Translate([System.Security.Principal.NTAccount]).Value } catch { $owner }
            $odd += "$($item.FullName) (created by $who)"
        }
    }
    if ($odd) {
        throw ("Something under $DataDir was created by someone other than an administrator, so nothing was changed:`n  " +
               (($odd | Select-Object -First 10) -join "`n  ") +
               "`nCheck policy.json and agent.toml, remove what you didn't put there, then run this again.")
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
        if ($LASTEXITCODE) { throw "couldn't make Administrators the owner of what is in $Path (icacls exit code $LASTEXITCODE)" }
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
