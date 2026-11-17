<#
.SYNOPSIS
  Installs, upgrades, downgrades and uninstalls the KratosForge MSI on a CI runner, as the runner's
  own user, and checks each step against docs/enterprise-install.md.

.DESCRIPTION
  Run by CI (.github/workflows/ci.yml, job shell) after the MSI is built. It never starts the app.

  1. Downloads the current Preview MSI (the "latest" release of this repository) with gh, and checks
     its SHA-256 against that release's version.json.
  2. Puts sample settings, a notebook and a sign-in cache in %LOCALAPPDATA%\FabricDesktop (or keeps
     what is already there) and records their hashes.
  3. Installs the Preview MSI silently, then this build's MSI over it: one KratosForge entry is left,
     with this build's version and exe.
  4. Installs the Preview MSI again over this build: refused with the WiX file's downgrade message,
     and this build's exe is still in place.
  5. Uninstalls silently: the program folder, the shortcut, the two HKCU values and the Installed
     apps entry are gone.
  After every step the user data is unchanged, since the MSI never installs or removes it.

  Steps 3 and 4 need the Preview, so trouble with the Preview alone (no release, a hash that doesn't
  match, an MSI that won't install) skips them with a warning, as does a Preview that isn't older
  than this build (a rerun of an older CI run). This build is then installed fresh before step 5.
  The step gates publishing, so a broken Preview never stops the build that fixes it.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Msi,
    [Parameter(Mandatory)] [string] $Exe,
    [Parameter(Mandatory)] [string] $Work,
    [string] $Repo = $env:GITHUB_REPOSITORY
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Msi = (Resolve-Path -LiteralPath $Msi).Path
$Exe = (Resolve-Path -LiteralPath $Exe).Path
$Wxs = Join-Path $PSScriptRoot "kratosforge.wxs"
New-Item -ItemType Directory -Force -Path $Work | Out-Null

$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\KratosForge"
$Shortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\KratosForge.lnk"
$ValuesKey = "HKCU:\Software\KratosBI\KratosForge"
$DataDir = Join-Path $env:LOCALAPPDATA "FabricDesktop"
$Downgrade = ([xml](Get-Content -LiteralPath $Wxs -Raw)).Wix.Package.MajorUpgrade.DowngradeErrorMessage
if (-not $Downgrade) { throw "No DowngradeErrorMessage in $Wxs" }

function Get-MsiVersion([string] $Path) {
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $db = $installer.GetType().InvokeMember("OpenDatabase", "InvokeMethod", $null, $installer, @($Path, 0))
    $view = $db.GetType().InvokeMember("OpenView", "InvokeMethod", $null, $db,
        @("SELECT ``Value`` FROM ``Property`` WHERE ``Property``='ProductVersion'"))
    $view.GetType().InvokeMember("Execute", "InvokeMethod", $null, $view, $null) | Out-Null
    $record = $view.GetType().InvokeMember("Fetch", "InvokeMethod", $null, $view, $null)
    $value = $record.GetType().InvokeMember("StringData", "GetProperty", $null, $record, 1)
    $view.GetType().InvokeMember("Close", "InvokeMethod", $null, $view, $null) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($db) | Out-Null
    return [version] $value
}

# msiexec's exit code, with its verbose log kept in $Work and its end shown when the step fails.
function Invoke-Msi([string] $Step, [string] $Action, [string] $Path) {
    $log = Join-Path $Work "$Step.log"
    $proc = Start-Process -FilePath msiexec.exe -Wait -PassThru -ArgumentList @(
        $Action, "`"$Path`"", "/qn", "/norestart", "/l*v", "`"$log`"")
    Write-Host "$Step`: msiexec $Action exited $($proc.ExitCode)"
    return @{ Code = $proc.ExitCode; Log = $log }
}

function Show-Log($result) {
    if (Test-Path -LiteralPath $result.Log) { Get-Content -LiteralPath $result.Log -Tail 60 | Write-Host }
}

# Installed apps entries in HKCU and HKLM. Windows Installer keeps a per-user product's entry in HKLM
# on the CI runner (an administrator), so both hives are searched and the place is only reported.
# A function's empty array comes back as $null, so every caller wraps it in @() before .Count (strict mode).
function Get-Entries {
    $roots = @("HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
               "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*")
    @(Get-ItemProperty -Path $roots -ErrorAction SilentlyContinue |
        Where-Object { $_.PSObject.Properties["DisplayName"] -and $_.DisplayName -eq "KratosForge" })
}

function Get-Value([string] $Name) {
    $item = Get-ItemProperty -LiteralPath $ValuesKey -ErrorAction SilentlyContinue
    if ($item -and $item.PSObject.Properties[$Name]) { return $item.$Name }
    return $null
}

function Assert-Installed([version] $Version, [string] $ExeHash, [string] $Step) {
    $installed = Join-Path $InstallDir "kratosforge.exe"
    if (-not (Test-Path -LiteralPath $installed)) { throw "$Step`: $installed is missing" }
    $hash = (Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash
    if ($hash -ne $ExeHash) { throw "$Step`: the installed exe isn't the expected build ($hash, expected $ExeHash)" }
    if (-not (Test-Path -LiteralPath $Shortcut)) { throw "$Step`: the Start menu shortcut is missing" }
    if (-not (Get-Value "InstallFolder")) { throw "$Step`: HKCU InstallFolder (the detection rule) is missing" }
    $entries = @(Get-Entries)
    if ($entries.Count -ne 1) { throw "$Step`: $($entries.Count) KratosForge entries in Installed apps, not 1" }
    Write-Host "$Step`: Installed apps entry at $($entries[0].PSPath -replace '^.*::', '')"
    if ([version] $entries[0].DisplayVersion -ne $Version) {
        throw "$Step`: Installed apps shows $($entries[0].DisplayVersion), expected $Version"
    }
    "$Step`: KratosForge $Version installed, exe $hash"
}

# The user's data: what the app keeps outside the install folder. Files already there are kept as they are.
$samples = [ordered]@{
    "settings.json"               = '{"lifecycle_check": true}'
    "msal_cache.bin"              = "lifecycle check sign-in cache"
    "notebooks\lifecycle.ipynb"   = '{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}'
}
$userData = [ordered]@{}
foreach ($rel in $samples.Keys) {
    $path = Join-Path $DataDir $rel
    if (-not (Test-Path -LiteralPath $path)) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
        Set-Content -LiteralPath $path -Value $samples[$rel] -NoNewline -Encoding utf8
    }
    $userData[$path] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
}
function Assert-UserData([string] $Step) {
    foreach ($path in $userData.Keys) {
        if (-not (Test-Path -LiteralPath $path)) { throw "$Step`: $path was removed" }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $userData[$path]) { throw "$Step`: $path was changed" }
    }
    "$Step`: settings, notebook and sign-in cache unchanged"
}

if (@(Get-Entries).Count -or (Test-Path -LiteralPath $InstallDir)) {
    throw "KratosForge is already installed for this user, so the check can't start clean"
}

$thisVersion = Get-MsiVersion $Msi
$thisExe = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash
"This build: MSI $thisVersion, exe $thisExe"

# The current Preview, from this repository's own release only. Trouble with the Preview itself (no
# release, a hash that doesn't match, an MSI that won't install) is a warning, not a failure: this
# step gates publishing, so a broken Preview must not stop the build that fixes it. This build's own
# install, upgrade, downgrade refusal and uninstall always fail the step.
function Get-Preview {
    $prevDir = Join-Path $Work "preview"
    New-Item -ItemType Directory -Force -Path $prevDir | Out-Null
    $path = Join-Path $prevDir "kratosforge.msi"
    # A publish of main replaces the MSI before version.json, so a download in between can see the two
    # disagree for a moment: try again then, three times in all.
    foreach ($try in 1..3) {
        gh release download latest --repo $Repo --pattern kratosforge.msi --pattern version.json --dir $prevDir --clobber | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "couldn't download it from $Repo's latest release" }
        $release = Get-Content -LiteralPath (Join-Path $prevDir "version.json") -Raw | ConvertFrom-Json
        $expected = $release.msi_sha256
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower()
        if ($expected -and $actual -eq $expected) {
            $version = Get-MsiVersion $path
            "Preview: MSI $version, sha256 $actual" | Write-Host
            return @{ Msi = $path; Version = $version; Exe = $release.exe_sha256.ToUpper() }
        }
        if ($try -eq 3) {
            # Still different after three tries: CI opens an issue for it on main (ci.yml, job
            # preview-integrity), as a run warning is easy to miss.
            Write-Host "::error::The Preview release's kratosforge.msi doesn't match the SHA-256 in its version.json"
            if ($env:GITHUB_OUTPUT) { "preview_mismatch=true" | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding utf8 }
            throw "its SHA-256 ($actual) isn't the one its release lists ($expected)"
        }
        Write-Warning "The Preview MSI doesn't match its version.json yet; trying again in 30 seconds."
        Start-Sleep -Seconds 30
    }
}

function Skip-Preview([string] $Why) {
    Write-Host "::warning::The upgrade and downgrade checks were skipped: $Why"
}

$preview = $null
try { $preview = Get-Preview } catch { Skip-Preview "the Preview MSI $($_.Exception.Message)." }
if ($preview -and $preview.Version -ge $thisVersion) {
    Skip-Preview "the Preview MSI ($($preview.Version)) isn't older than this build ($thisVersion), as on a rerun of an older CI run."
    $preview = $null
}

if ($preview) {
    $r = Invoke-Msi "1-install-preview" "/i" $preview.Msi
    try {
        if ($r.Code -ne 0) { throw "exit $($r.Code)" }
        # The MSI carries the same exe the release publishes, so version.json names it too.
        Assert-Installed $preview.Version $preview.Exe "1-install-preview"
        Assert-UserData "1-install-preview"
    } catch {
        Show-Log $r
        Skip-Preview "the Preview MSI didn't install cleanly ($($_.Exception.Message))."
        Invoke-Msi "1-remove-preview" "/x" $preview.Msi | Out-Null
        if (@(Get-Entries).Count -or (Test-Path -LiteralPath $InstallDir)) {
            throw "The Preview MSI couldn't be removed after its failed install, so this build can't be checked from a clean start"
        }
        $preview = $null
    }
}

if ($preview) {
    $r = Invoke-Msi "2-upgrade" "/i" $Msi
    if ($r.Code -ne 0) { Show-Log $r; throw "This build's MSI didn't upgrade the Preview (exit $($r.Code))" }
    Assert-Installed $thisVersion $thisExe "2-upgrade"
    Assert-UserData "2-upgrade"

    $r = Invoke-Msi "3-downgrade" "/i" $preview.Msi
    if ($r.Code -eq 0) { Show-Log $r; throw "The older Preview MSI installed over this build; it should be refused" }
    $log = Get-Content -LiteralPath $r.Log -Raw
    if (-not $log.Contains($Downgrade)) { Show-Log $r; throw "The downgrade was refused, but not with the message: $Downgrade" }
    "3-downgrade: refused with: $Downgrade"
    Assert-Installed $thisVersion $thisExe "3-downgrade"
    Assert-UserData "3-downgrade"
} else {
    $r = Invoke-Msi "2-install" "/i" $Msi
    if ($r.Code -ne 0) { Show-Log $r; throw "This build's MSI didn't install silently (exit $($r.Code))" }
    Assert-Installed $thisVersion $thisExe "2-install"
    Assert-UserData "2-install"
}

$r = Invoke-Msi "4-uninstall" "/x" $Msi
if ($r.Code -ne 0) { Show-Log $r; throw "The silent uninstall failed (exit $($r.Code))" }
if (Test-Path -LiteralPath $InstallDir) { throw "4-uninstall: $InstallDir is still there" }
if (Test-Path -LiteralPath $Shortcut) { throw "4-uninstall: the Start menu shortcut is still there" }
foreach ($name in "InstallFolder", "StartMenuShortcut") {
    if ($null -ne (Get-Value $name)) { throw "4-uninstall: HKCU $name is still there" }
}
if (@(Get-Entries).Count) { throw "4-uninstall: KratosForge is still in Installed apps" }
"4-uninstall: program folder, shortcut, HKCU values and Installed apps entry removed"
Assert-UserData "4-uninstall"
"The MSI install lifecycle checks passed."
