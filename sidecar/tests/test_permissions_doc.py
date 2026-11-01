"""Least privilege: every delegated permission the app can ask for is listed, with why, in
docs/permissions.md, and that table lists nothing the app doesn't ask for. A new scope in the
code fails here until the table says what it's for."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The resource each scope is for, as Entra names the API in "Add a permission".
API = {
    "https://api.fabric.microsoft.com": "Power BI Service",
    "https://analysis.windows.net/powerbi/api": "Power BI Service",
    "https://graph.microsoft.com": "Microsoft Graph",
    "https://storage.azure.com": "Azure Storage",
    "https://database.windows.net": "Azure SQL Database",
    "https://kusto.kusto.windows.net": "Azure Data Explorer",
    "https://vault.azure.net": "Azure Key Vault",
}
# A delegated scope as code writes it: https://<resource>/<Permission>, where the permission is
# user_impersonation or a dotted name (Item.ReadWrite.All, User.Read, Mail.Send). /.default isn't
# a permission of its own.
SCOPE = re.compile(r"[\"'](https://[a-z0-9.\-]+(?:/powerbi/api)?)/(user_impersonation|[A-Z][A-Za-z]*(?:\.[A-Z][A-Za-z]*)+)[\"']")
ROW = re.compile(r"^\| ([^|]+?) \| `([^`]+)` \|", re.M)


def _code_scopes() -> set[tuple[str, str]]:
    found = set()
    for path in (ROOT / "sidecar" / "fabric_desktop").rglob("*.py"):
        if "_vendor" in path.parts:
            continue
        for resource, permission in SCOPE.findall(path.read_text(encoding="utf-8")):
            assert resource in API, f"{path.name} asks {resource} for {permission}: add that API here and to docs/permissions.md"
            found.add((API[resource], permission))
    return found


def _doc_rows() -> set[tuple[str, str]]:
    text = (ROOT / "docs" / "permissions.md").read_text(encoding="utf-8")
    table = text.split("| API (as Entra shows it) | Permission |", 1)[1].split("\n\n", 1)[0]
    return set(ROW.findall(table))


def test_every_permission_the_app_asks_for_is_listed_with_why():
    missing = _code_scopes() - _doc_rows()
    assert not missing, f"add these to the permissions table in docs/permissions.md, with what each is for: {sorted(missing)}"


def test_the_table_lists_nothing_the_app_does_not_ask_for():
    extra = _doc_rows() - _code_scopes()
    assert not extra, f"docs/permissions.md lists permissions no code asks for: {sorted(extra)}"


def test_the_scan_finds_every_scope_sign_in_knows():
    from fabric_desktop import auth
    scopes = {s for name, v in vars(auth).items() if name.endswith("_SCOPES") and isinstance(v, list) for s in v}
    assert len(scopes) >= 15
    code = _code_scopes()
    for s in scopes:
        resource, permission = s.rsplit("/", 1)
        assert (API[resource], permission) in code, s


def test_a_scope_that_does_not_end_in_all_is_found_too():
    text = 'X = ["https://graph.microsoft.com/User.Read", "https://graph.microsoft.com/Mail.Send"]\n' \
           'Y = ["https://storage.azure.com/user_impersonation", "https://api.fabric.microsoft.com/.default"]'
    assert set(SCOPE.findall(text)) == {("https://graph.microsoft.com", "User.Read"),
                                        ("https://graph.microsoft.com", "Mail.Send"),
                                        ("https://storage.azure.com", "user_impersonation")}
