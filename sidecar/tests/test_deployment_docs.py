"""The enterprise deployment docs match what the code reads: every policy key has a row in
docs/azure-setup.md ("Push settings to everyone") and every row is a key; every environment variable
docs/enterprise-install.md lists is one the code reads; and what it says about the MSI and the
registry is what the installer and the shell do."""

import re
from pathlib import Path

from fabric_desktop import app_settings

ROOT = Path(__file__).resolve().parents[2]
SETUP = (ROOT / "docs" / "azure-setup.md").read_text(encoding="utf-8")
INSTALL = (ROOT / "docs" / "enterprise-install.md").read_text(encoding="utf-8")
CODE = [*(ROOT / "sidecar" / "fabric_desktop").rglob("*.py"), *(ROOT / "app" / "src-tauri" / "src").glob("*.*")]


def _table_keys(section: str) -> set[str]:
    keys: set[str] = set()
    for line in section.splitlines():
        if line.startswith("| `"):
            keys |= set(re.findall(r"`([a-z_]+)`", line.split("|")[1]))
    return keys


def test_every_policy_key_has_a_row_and_every_row_is_a_key():
    section = SETUP.split("## 4. Push settings to everyone", 1)[1].split("\n## ", 1)[0]
    rows = _table_keys(section)
    assert sorted(set(app_settings.POLICY_KEYS) - rows) == [], "policy keys with no row in azure-setup.md"
    assert sorted(rows - set(app_settings.POLICY_KEYS)) == [], "rows in azure-setup.md that aren't policy keys"


def test_every_environment_variable_listed_is_read_by_the_code():
    section = INSTALL.split("### Environment variables", 1)[1].split("\n## ", 1)[0]
    listed = set(re.findall(r"`([A-Z][A-Z0-9_]+)`", section))
    assert len(listed) >= 8, listed
    code = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in CODE)
    proxy = {"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "REQUESTS_CA_BUNDLE"}  # read by requests and urllib
    assert sorted(v for v in listed - proxy if v not in code) == []


def test_the_msi_and_registry_are_as_the_page_says():
    wxs = (ROOT / "app" / "installer" / "kratosforge.wxs").read_text(encoding="utf-8")
    assert 'Scope="perUser"' in wxs and "Per-machine install | Not available" in INSTALL
    assert 'Root="HKLM"' not in wxs
    deep_link = (ROOT / "app" / "src-tauri" / "src" / "deep_link.rs").read_text(encoding="utf-8")
    assert r'"HKCU\\Software\\Classes\\{SCHEME}"' in deep_link
    assert r"`HKCU\Software\Classes\kratosforge`" in INSTALL
    # No registry policy: nothing reads HKLM or HKCU\Software\Policies.
    code = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in CODE)
    assert "Software\\\\Policies" not in code and r"Software\Policies" not in code
    assert r"nothing under `HKLM\Software\Policies`" in INSTALL


def test_the_engine_download_uses_the_windows_proxy_as_the_page_says():
    main = (ROOT / "app" / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    install = main.split("fn install_engine_into", 1)[1].split("\nfn ", 1)[0]
    assert 'system_tool("curl.exe")' in install and "download_args(" in install
    assert "win_proxy::curl_args(url)" in main.split("fn download_args", 1)[1].split("\n}", 1)[0]
    assert "else the proxy Windows uses for the download's address" in INSTALL


def test_the_engine_starts_with_the_proxy_windows_uses_as_the_page_says():
    main = (ROOT / "app" / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    start = main.split("fn start() -> std::io::Result<Self>", 1)[1].split("\n    }\n", 1)[0]
    assert "win_proxy::engine_env()" in start  # the engine, and so notebooks, pip, delta-rs and gRPC
    assert "win_proxy::engine_env()" in main.split("fn updater(", 1)[1].split("\n}", 1)[0]
    assert "win_proxy::engine_env()" in main.split("fn run_schedule(", 1)[1].split("\n}", 1)[0]
    assert "A PAC file, WPAD, or a proxy set for every user" in INSTALL


# Policy keys the page says are saved but not applied yet, with the words the row must use.
NOT_APPLIED = {"ui_color_mode": "not applied yet"}


def test_every_policy_key_is_read_by_the_code_or_its_row_says_it_isnt_applied():
    # A key documented as working that nothing reads would be a setting IT trusts and the app ignores.
    readers = [p for p in CODE if p.name != "app_settings.py"] + list((ROOT / "app" / "ui").glob("*.js"))
    code = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in readers)
    unread = sorted(k for k in app_settings.POLICY_KEYS if not re.search(rf"\b{k}\b", code))
    assert unread == sorted(NOT_APPLIED), "policy keys nothing outside app_settings.py reads"
    section = SETUP.split("## 4. Push settings to everyone", 1)[1].split("\n## ", 1)[0]
    for key, words in NOT_APPLIED.items():
        row = next(line for line in section.splitlines() if line.startswith(f"| `{key}`"))
        assert words in row, row


def test_every_restriction_is_named_where_the_page_says_what_fails_closed():
    bullet = SETUP.split("**A restriction has the wrong kind of value**", 1)[1].split("\n", 1)[0]
    named = set(re.findall(r"`([a-z_]+)`", bullet))
    assert sorted(set(app_settings.RESTRICTED) - named) == []
