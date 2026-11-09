<#
.SYNOPSIS
  Checks install-agent.ps1's folder rights on a real Windows machine (CI runs it elevated).

.DESCRIPTION
  Runs install-agent.ps1 -FoldersOnly against folders under -Root, never C:\ProgramData:
  - a fresh install gets only the rights docs/agent.md describes, owned by Administrators;
  - a second run on that install passes, with files the agent's account made in its home;
  - a file someone else made first is refused, and nothing is changed;
  - a link (junction) where a folder should be is refused, and its target is left alone;
  - a file with a second name elsewhere is refused, and that file is left alone;
  - a file whose names can't be listed is refused, not passed (where fsutil fails on it).
  It creates two local accounts (fd-test-agent, fd-test-other) and removes them at the end.
#>
param([string] $Root = (Join-Path ([System.IO.Path]::GetTempPath()) "fd-install-test-$PID"))
$ErrorActionPreference = "Stop"
$install = Join-Path $PSScriptRoot "install-agent.ps1"
$failures = @()
function Check([bool] $ok, [string] $what) {
    if ($ok) { Write-Host "ok   $what" } else { Write-Host "FAIL $what"; $script:failures += $what }
}
function Sid([string] $name) { ([System.Security.Principal.NTAccount]$name).Translate([System.Security.Principal.SecurityIdentifier]).Value }
function Install([string] $dir) {
    try { $null = & $install -Account "$env:COMPUTERNAME\fd-test-agent" -DataDir $dir -FoldersOnly; return "" }
    catch { return $_.Exception.Message }
}
function Fmt([string] $sid, [System.Security.AccessControl.FileSystemRights] $rights) {
    "$sid=$([int]($rights -band -bnot [System.Security.AccessControl.FileSystemRights]::Synchronize))"   # Set-Acl may add Synchronize
}
function Rights([string] $path) {   # explicit rules as "SID=rights", and whether inheritance is cut
    $acl = Get-Acl -LiteralPath $path
    $rules = $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) |
        ForEach-Object { Fmt $_.IdentityReference.Value $_.FileSystemRights }
    [pscustomobject]@{ Protected = $acl.AreAccessRulesProtected; Owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
                       Rules = (@($rules) | Sort-Object) -join "; " }
}

$password = ConvertTo-SecureString ("Aa1!" + [guid]::NewGuid()) -AsPlainText -Force
foreach ($name in "fd-test-agent", "fd-test-other") {
    if (-not (Get-LocalUser -Name $name -ErrorAction SilentlyContinue)) { New-LocalUser -Name $name -Password $password | Out-Null }
}
$agent, $other = (Sid "$env:COMPUTERNAME\fd-test-agent"), (Sid "$env:COMPUTERNAME\fd-test-other")
$admins, $system, $users = "S-1-5-32-544", "S-1-5-18", "S-1-5-32-545"
New-Item -ItemType Directory -Force -Path $Root | Out-Null
try {
    # 1. A fresh install.
    $fresh = Join-Path $Root "fresh"
    $err = Install $fresh
    Check ($err -eq "") "a fresh install runs ($err)"
    $top, $home_ = (Rights $fresh), (Rights "$fresh\agent")
    $want = (@((Fmt $admins FullControl), (Fmt $system FullControl), (Fmt $users ReadAndExecute), (Fmt $agent ReadAndExecute)) | Sort-Object) -join "; "
    Check ($top.Protected -and $top.Owner -eq $admins -and $top.Rules -eq $want) "the settings folder: $($top.Rules), owner $($top.Owner)"
    $want = (@((Fmt $admins FullControl), (Fmt $system FullControl), (Fmt $agent Modify)) | Sort-Object) -join "; "
    Check ($home_.Protected -and $home_.Owner -eq $admins -and $home_.Rules -eq $want) "the agent's home: $($home_.Rules), owner $($home_.Owner)"
    $toml = Rights "$fresh\agent.toml"
    Check (-not $toml.Protected -and $toml.Owner -eq $admins) "agent.toml takes the folder's rights, owned by Administrators"

    # 2. Run again on that install, after the agent wrote its queue and logs.
    Set-Content -LiteralPath "$fresh\agent\queue.sqlite3" -Value "x"
    icacls "$fresh\agent\queue.sqlite3" /setowner "*$agent" /Q | Out-Null
    Check ((Rights "$fresh\agent\queue.sqlite3").Owner -eq $agent) "(set up) a file the agent owns in its home"
    $err = Install $fresh
    Check ($err -eq "") "a second run on an installed agent passes ($err)"
    Check ((Rights "$fresh\agent\queue.sqlite3").Owner -eq $admins) "and re-owns the agent's own files to Administrators"

    # 3. A policy.json someone else made before the install.
    $planted = Join-Path $Root "planted"
    New-Item -ItemType Directory -Force -Path $planted | Out-Null
    Set-Content -LiteralPath "$planted\policy.json" -Value '{"engine_download_url": "https://example.invalid/"}'
    icacls "$planted\policy.json" /setowner "*$other" /Q | Out-Null
    Check ((Rights "$planted\policy.json").Owner -eq $other) "(set up) a policy.json another account owns"
    $err = Install $planted
    Check ($err -match "created by" -and $err -match "policy\.json") "it is refused and named ($err)"
    Check ((Rights "$planted\policy.json").Owner -eq $other) "its owner is left as it was"
    Check (-not (Rights $planted).Protected) "the folder's rights are left as they were"
    Check (-not (Test-Path "$planted\agent")) "nothing is created in it"

    # 4. A junction where the agent's home should be.
    $linked, $target = (Join-Path $Root "linked"), (Join-Path $Root "elsewhere")
    New-Item -ItemType Directory -Force -Path $linked, $target | Out-Null
    cmd /c mklink /J "$linked\agent" "$target" | Out-Null
    $err = Install $linked
    Check ($err -match "is a link") "a junction is refused ($err)"
    Check (-not (Rights $target).Protected) "its target's rights are left alone"
    Check (-not (Test-Path "$target\agent.toml")) "nothing is written through it"

    # 5. A file in the agent's home that is also a file somewhere else.
    $twice, $outside = (Join-Path $Root "twice"), (Join-Path $Root "outside.txt")
    New-Item -ItemType Directory -Force -Path "$twice\agent" | Out-Null
    Set-Content -LiteralPath $outside -Value "x"
    $before = Rights $outside
    cmd /c mklink /H "$twice\agent\queue.sqlite3" "$outside" | Out-Null
    Check (@(fsutil hardlink list $outside).Count -eq 2) "(set up) a file with two names"
    $err = Install $twice
    Check ($err -match "more than one name") "it is refused ($err)"
    Check ((Rights $outside).Rules -eq $before.Rules -and (Rights $outside).Owner -eq $before.Owner) "the file's rights and owner are left alone"
    Check (-not (Rights $twice).Protected) "the folder's rights are left as they were"

    # 6. A file whose names can't be listed (held open with no sharing): refused, not passed.
    $locked = Join-Path $Root "locked"
    New-Item -ItemType Directory -Force -Path "$locked\agent" | Out-Null
    Set-Content -LiteralPath "$locked\agent\queue.sqlite3" -Value "x"
    $hold = [System.IO.File]::Open("$locked\agent\queue.sqlite3", 'Open', 'ReadWrite', 'None')
    try {
        $code = try { $null = fsutil hardlink list "$locked\agent\queue.sqlite3" 2>$null; $LASTEXITCODE } catch { -1 }
        if ($code -ne 0) {
            $err = Install $locked
            Check ($err -match "Couldn't check whether") "a file whose names can't be listed is refused ($err)"
            Check (-not (Rights $locked).Protected) "and the folder's rights are left as they were"
        } else { Write-Host "skip a file whose names can't be listed (fsutil can still list a file held open here)" }
    } finally { $hold.Dispose() }
} finally {
    cmd /c rmdir "$Root\linked\agent" 2>$null
    Remove-Item -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue
    foreach ($name in "fd-test-agent", "fd-test-other") { Remove-LocalUser -Name $name -ErrorAction SilentlyContinue }
}
if ($failures) { throw "$($failures.Count) check(s) failed: $($failures -join ' | ')" }
Write-Host "All install-agent.ps1 folder checks passed."
