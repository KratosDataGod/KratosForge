"""#959 (PB-010 follow-up): the host list IT allows through its firewall (docs/enterprise-install.md,
"Network access") names every host the engine and the shell call.

Every https:// or http:// host written in sidecar/fabric_desktop or app/src-tauri/src must be in that
table (exactly, or under a `*.` row) or in NOT_CALLED below with the reason nothing connects to it.
Rust test modules (#[cfg(test)] mod ... { }) are left out: their hosts are made up to test the filters.
No network is used.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE = [ROOT / "sidecar" / "fabric_desktop", ROOT / "app" / "src-tauri" / "src"]
DOC = ROOT / "docs" / "enterprise-install.md"
# A name ending in letters: skips 127.0.0.1 (loopback) and cut-off templates such as "https://adb-123.7{suffix}".
HOST = re.compile(r"https?://((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?![A-Za-z0-9.-])")

_AUDIENCE = "token audience (the scope a token is for), not a host the app connects to"
_SCHEMA = "an identifier inside a file or message format; nothing fetches it"
_LINK = "a documentation link the app shows or opens in the browser"
_EXAMPLE = "an example in help text or an error message"

NOT_CALLED: dict[str, str] = {
    "analysis.windows.net": _AUDIENCE,
    "database.windows.net": _AUDIENCE,
    "kusto.kusto.windows.net": _AUDIENCE,
    "storage.azure.com": _AUDIENCE,
    "vault.azure.net": _AUDIENCE,  # Key Vault calls go to <name>.vault.azure.net, which the table lists
    "adaptivecards.io": _SCHEMA,  # the Teams card's $schema
    "developer.microsoft.com": _SCHEMA,  # Fabric item definition $schema
    "schemas.microsoft.com": _SCHEMA,  # Task Scheduler XML namespace
    "schemas.openxmlformats.org": _SCHEMA,  # Excel export XML namespaces
    "docs.databricks.com": _LINK,
    "duckdb.org": _LINK,
    "learn.microsoft.com": _LINK,
    "spark.apache.org": _LINK,
    "www.apache.org": _LINK,  # licence text in vendored code
    "evil.example": _EXAMPLE,  # a docstring on how a lookalike host is refused
    "files.contoso.com": _EXAMPLE,  # engine_download_url
}


def _without_rust_tests(text: str) -> str:
    """Drops each `#[cfg(test)] mod name { ... }`, matching braces."""
    out, i = [], 0
    for m in re.finditer(r"#\[cfg\(test\)\]\s*mod\s+\w+\s*\{", text):
        if m.start() < i:
            continue
        depth, j = 1, m.end()
        while depth and j < len(text):
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        out.append(text[i:m.start()])
        i = j
    return "".join(out) + text[i:]


def _code_hosts() -> dict[str, str]:
    found: dict[str, str] = {}
    for root in CODE:
        for path in sorted(root.rglob("*")):
            if path.suffix not in (".py", ".rs"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.suffix == ".rs":
                text = _without_rust_tests(text)
            for host in HOST.findall(text):
                found.setdefault(host.lower(), str(path.relative_to(ROOT)))
    return found


def _table_hosts() -> set[str]:
    section = DOC.read_text(encoding="utf-8").split("## Network access", 1)[1].split("\n## ", 1)[0]
    return {h.lower() for line in section.splitlines() if line.startswith("| ")
            for h in re.findall(r"`([A-Za-z0-9*.-]+\.[A-Za-z]{2,})`", line.split("|")[1])}


def _listed(host: str, table: set[str]) -> bool:
    return host in table or any(t.startswith("*.") and host.endswith(t[1:]) for t in table)


def test_every_host_the_code_calls_is_in_the_firewall_list():
    table = _table_hosts()
    assert len(table) > 15, "the Network access table wasn't found; did enterprise-install.md change?"
    missing = {h: where for h, where in _code_hosts().items() if not _listed(h, table) and h not in NOT_CALLED}
    assert not missing, ("Hosts in the code that docs/enterprise-install.md#network-access doesn't list. Add a row "
                         f"saying what the app uses each for, or add it to NOT_CALLED with why nothing connects to it: {missing}")


def test_the_not_called_list_stays_honest():
    code, table = _code_hosts(), _table_hosts()
    gone = sorted(set(NOT_CALLED) - set(code))
    assert not gone, f"NOT_CALLED lists hosts the code no longer names; remove them: {gone}"
    both = sorted(h for h in NOT_CALLED if _listed(h, table))
    assert not both, f"NOT_CALLED lists hosts the firewall table also lists; keep one: {both}"
    assert all(reason.strip() for reason in NOT_CALLED.values())
