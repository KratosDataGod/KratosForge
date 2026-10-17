"""Secrets: finding them in files about to be committed, and hiding them in messages.

The patterns look for things that are almost never anything but a secret (keys, tokens,
connection strings that carry a password, private keys). A finding shows only the first few
characters of what matched, so the warning itself doesn't spread the secret.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any, Iterable

MAX_SCAN_BYTES = 2_000_000

# (kind, pattern, remediation); group "s" is the secret itself when the pattern has one.
PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----"),
     "Keep private keys out of the repository; load them from a key file outside it or from Key Vault."),
    ("Storage account key", re.compile(r"AccountKey=(?P<s>[A-Za-z0-9+/]{40,}={0,2})"),
     "Use your sign-in (OneLake and Azure Storage take your token) or a Key Vault reference instead of the key."),
    ("Shared access signature", re.compile(r"[?&]sig=(?P<s>[A-Za-z0-9%+/]{20,}={0,2})"),
     "A SAS URL works for anyone who has it. Keep it in Key Vault or a variable library marked secret."),
    ("Password in a connection string", re.compile(r"(?i)\b(?:password|pwd)\s*=\s*(?P<s>[^;'\"\s]{4,})"),
     "Use Microsoft Entra sign-in for the connection, or read the password from Key Vault."),
    ("GitHub token", re.compile(r"\b(?P<s>(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}))\b"),
     "Revoke the token on GitHub; Git signs in through Git Credential Manager, so code never needs one."),
    ("Microsoft Entra client secret", re.compile(r"(?<![A-Za-z0-9_~.-])(?P<s>[A-Za-z0-9_~.-]{3}\dQ~[A-Za-z0-9_~.-]{31,34})(?![A-Za-z0-9_~.-])"),
     "Rotate the secret in Entra ID and read it from Key Vault at run time."),
    ("AWS access key", re.compile(r"\b(?P<s>AKIA[0-9A-Z]{16})\b"), "Rotate the key and keep it outside the repository."),
    ("Slack token", re.compile(r"\b(?P<s>xox[abposr]-[A-Za-z0-9-]{10,})\b"), "Revoke the token and keep it outside the repository."),
    ("Access token", re.compile(r"\b(?P<s>eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"),
     "This looks like a sign-in token (often in notebook output). Clear the output and commit again."),
    ("Teams webhook", re.compile(r"(?P<s>https://[A-Za-z0-9.-]+\.webhook\.office\.com/[^\s'\"]+)"),
     "Anyone with this address can post to the channel. Keep it in the agent's settings or Key Vault."),
    ("Secret in code", re.compile(r"(?i)\b(?:client_secret|api_key|apikey|secret_key|access_token|auth_token)\s*[=:]\s*[\"'](?P<s>[^\"'\s]{8,})[\"']"),
     "Read the value from Key Vault, an environment variable or a variable library at run time."),
]

# File names that hold secrets by their nature, whatever is in them.
SENSITIVE_NAMES = {".env", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", ".git-credentials", ".netrc", "_netrc",
                   "msal_token_cache.bin", "msal_cache.bin", "token_cache.bin", "fabric-view-signin.bin",
                   "agent.secrets.toml", "credentials.json"}
SENSITIVE_SUFFIXES = (".pem", ".pfx", ".p12", ".key", ".keystore", ".jks", ".publishsettings")


def mask(secret: str) -> str:
    """The first few characters of a secret, so a person can find it without it being shown."""
    keep = 4 if len(secret) > 12 else 1
    return secret[:keep] + "…[hidden]"


def sensitive_name(path: str) -> str | None:
    """Why a file name alone suggests it holds a secret, or None."""
    name = PurePosixPath(path).name.lower()
    if name in SENSITIVE_NAMES or (name.startswith(".env.") and name not in {".env.example", ".env.sample", ".env.template"}):
        return "This kind of file usually holds secrets or sign-in data."
    if name.endswith(SENSITIVE_SUFFIXES):
        return "Key and certificate files shouldn't be committed."
    return None


def scan_text(text: str) -> list[dict[str, Any]]:
    """Findings in plain text: kind, 1-based line, masked match, what to do."""
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if len(line) > 20000:  # minified data, not code someone typed a secret into
            line = line[:20000]
        for kind, pattern, fix in PATTERNS:
            for m in pattern.finditer(line):
                secret = m.groupdict().get("s") or m.group(0)
                if _placeholder(secret):
                    continue
                found.append({"kind": kind, "line": lineno, "match": mask(secret), "remediation": fix})
    return found


def _placeholder(value: str) -> bool:
    if value.startswith(("{{", "${", "$(", "<", "%(", "@{")):
        return True  # a template or variable, filled in at run time
    v = value.strip("<>{}[]$%").lower()
    return (v in {"password", "pwd", "secret", "xxxx", "changeme", "none", "null", "your_password", "yourpassword"}
            or set(v) <= {"x", "*", "."})


def scan_file(path: str, data: bytes) -> list[dict[str, Any]]:
    """Findings in one file's content. Notebook findings name the cell."""
    out: list[dict[str, Any]] = []
    why = sensitive_name(path)
    if why:
        out.append({"path": path, "kind": "Sensitive file", "line": None, "match": PurePosixPath(path).name,
                    "remediation": f"{why} Unstage it and add it to .gitignore."})
    if b"\0" in data[:8000]:
        return out
    text = data[:MAX_SCAN_BYTES].decode("utf-8", "replace")
    if path.lower().endswith(".ipynb"):
        cells = _notebook_cells(text)
        if cells is not None:
            for n, kind, source in cells:
                for f in scan_text(source):
                    out.append({"path": path, **f, "cell": n, "where": f"cell {n}{' output' if kind == 'output' else ''}"})
            return out
    out.extend({"path": path, **f} for f in scan_text(text))
    return out


def _notebook_cells(text: str) -> list[tuple[int, str, str]] | None:
    try:
        nb = json.loads(text)
    except ValueError:
        return None
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        return None
    out = []
    for n, cell in enumerate(nb["cells"], 1):
        if not isinstance(cell, dict):
            continue
        out.append((n, "source", _joined(cell.get("source"))))
        for o in cell.get("outputs") or []:
            if isinstance(o, dict):
                parts = [_joined(o.get("text"))]
                for v in (o.get("data") or {}).values():
                    parts.append(_joined(v) if not isinstance(v, dict) else json.dumps(v))
                out.append((n, "output", "\n".join(parts)))
    return out


def _joined(v: Any) -> str:
    if isinstance(v, list):
        return "".join(str(x) for x in v)
    return v if isinstance(v, str) else ""


# ---- redaction of messages

_URL_USERINFO = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^/@\s:]*)(?::[^/@\s]*)?@", re.I)
_BEARER = re.compile(r"(?i)\b(authorization:\s*(?:bearer|basic)\s+|bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_EXTRAHEADER = re.compile(r"(?i)(extraheader\s*=?\s*)\S.*")


def redact(text: str | None) -> str:
    """``text`` with credentials removed: URL passwords and user names, tokens, keys."""
    if not text:
        return text or ""
    text = _URL_USERINFO.sub(lambda m: f"{m.group('scheme')}***@", text)
    text = _BEARER.sub(lambda m: m.group(1) + "***", text)
    text = _EXTRAHEADER.sub(lambda m: m.group(1) + "***", text)
    for _, pattern, _ in PATTERNS:
        if "s" in pattern.groupindex:
            text = pattern.sub(lambda m: m.group(0).replace(m.group("s"), mask(m.group("s"))), text)
    return text


def redact_all(values: Iterable[str]) -> list[str]:
    return [redact(v) for v in values]
