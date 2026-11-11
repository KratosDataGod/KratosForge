"""The MSI and support bundle facts in docs/enterprise-install.md match their sources: the WiX file
(app/installer/kratosforge.wxs), the CI step that builds it, and support_bundle.py. A change to any of
them fails here until the page says the same."""

import re
from pathlib import Path

from fabric_desktop import support_bundle

ROOT = Path(__file__).resolve().parents[2]
PAGE = (ROOT / "docs" / "enterprise-install.md").read_text(encoding="utf-8")
WXS = (ROOT / "app" / "installer" / "kratosforge.wxs").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def section(title: str) -> str:
    return PAGE.split(f"#{title}\n", 1)[1].split("\n## ", 1)[0]


def test_the_msi_section_matches_the_wix_file_and_the_ci_build():
    msi = section("## Silent install, uninstall and detection".lstrip("#"))
    upgrade = re.search(r'UpgradeCode="([0-9a-f-]+)"', WXS).group(1)
    assert f"`{upgrade}`" in msi
    assert 'Scope="perUser"' in WXS and "`Scope=\"perUser\"`" in msi
    assert 'AllowSameVersionUpgrades="yes"' in WXS and "the same version can be installed again" in msi
    downgrade = re.search(r'DowngradeErrorMessage="([^"]+)"', WXS).group(1)
    assert downgrade in msi
    for value in re.findall(r'RegistryValue Root="HKCU" Key="([^"]+)" Name="(\w+)"', WXS):
        assert f"`{value[1]}`" in msi and f"HKCU\\{value[0]}" in msi
    assert 'Name="Programs"' in WXS and 'Name="KratosForge"' in WXS
    assert "%LOCALAPPDATA%\\Programs\\KratosForge\\kratosforge.exe" in msi
    assert "Advertise" not in WXS  # the page says the shortcut isn't advertised
    assert "ProductCode" not in WXS  # the page says each build gets its own
    assert '$version = "0.$([math]::Floor($run / 65536) + 1).$($run % 65536)"' in CI and "`0.<n>.<run>`" in msi
    assert "UNVERIFIED" in msi


def test_ci_runs_the_msi_lifecycle_check_the_page_describes():
    msi = section("## Silent install, uninstall and detection".lstrip("#"))
    script = (ROOT / "app" / "installer" / "test-msi-lifecycle.ps1").read_text(encoding="utf-8")
    assert "..\\installer\\test-msi-lifecycle.ps1 -Msi target\\release\\kratosforge.msi" in CI
    assert "app/installer/test-msi-lifecycle.ps1" in msi
    # The downgrade message is read from the WiX file, so the check can't drift from the MSI.
    assert "MajorUpgrade.DowngradeErrorMessage" in script and "DowngradeErrorMessage=" not in script
    # Only this repository's own Preview release is downloaded, and its hash is checked.
    assert "gh release download latest --repo $Repo" in script and "msi_sha256" in script
    assert "Invoke-WebRequest" not in script and "Start-Process -FilePath msiexec.exe" in script


def test_the_support_bundle_section_lists_what_the_bundle_holds():
    page = section("## What the support bundle collects".lstrip("#"))
    listed = set(re.findall(r"^\| `([\w.-]+)` \|", page, re.M))
    assert listed == {"about.json", "sidecar.log", "sidecar.prev.log", "app-crash.log", "agent.log", "CONTENTS.txt"}
    source = Path(support_bundle.__file__).read_text(encoding="utf-8")
    for name in listed:
        assert f'"{name}"' in source, name
    assert f"{support_bundle.LOG_LINES:,}" in page
    assert '"policy_record"' in source and "`policy_record`" in page
