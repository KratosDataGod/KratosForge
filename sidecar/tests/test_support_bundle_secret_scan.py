"""PB-062: a support bundle carries no token, key, password or personal detail.

Every place the bundle reads from (the engine's logs, the run before's log, the app's and the agent's
crash logs, the agent's log, the user's settings and the policy file) is seeded with one planted
secret of every kind the app handles. The bundle is then built the way Settings and the CLI build it,
and every file in the zip is scanned two ways:

1. for each planted value itself, so a rule that misses one kind fails here by name;
2. with detectors written here, apart from redact.py, so a value redact.py rewrites into something
   still secret-shaped (half a token, a key's body without its header) fails too.

Token-shaped values are joined at run time, never written whole, so GitHub push protection
doesn't block the file and no real-looking credential sits in the repository.
"""

from __future__ import annotations

import json
import re
import secrets as rnd
import string
import uuid
import zipfile
from pathlib import Path

import pytest

from fabric_desktop import support_bundle
from fabric_desktop.service import Service


def _alnum(n: int) -> str:
    return "".join(rnd.choice(string.ascii_letters + string.digits) for _ in range(n))


def _hex(n: int) -> str:
    return rnd.token_hex(n // 2)


def _b64url(n: int) -> str:
    return "".join(rnd.choice(string.ascii_letters + string.digits + "-_") for _ in range(n))


def _planted() -> list[tuple[str, str, str]]:
    """(kind, the line a log could hold, the secret in it). Fresh values each run."""
    jwt = "eyJ" + _b64url(30) + "." + "eyJ" + _b64url(60) + "." + _b64url(43)
    pem_body = "\n".join(_alnum(64) for _ in range(4))
    pem = "-----BEGIN " + "PRIVATE KEY-----\n" + pem_body + "\n-----END " + "PRIVATE KEY-----"
    pw = "Pw" + _alnum(14)
    entra = _alnum(3) + "8Q~" + _b64url(34).replace("-", "_")
    rows = [
        ("Entra access token (JWT)", f"GET https://api.fabric.microsoft.com failed; Authorization: Bearer {jwt}", jwt),
        ("bearer opaque token", f"retrying with Bearer {_alnum(40)}", None),
        ("refresh token", f"msal: refresh_token={_b64url(80)}&grant_type=refresh_token", None),
        ("MSAL token cache entry", '{"RefreshToken": {"k": {"secret": "' + _b64url(90) + '", "credential_type": "RefreshToken"}}}', None),
        ("storage SAS signature", f"PUT https://acct.blob.core.windows.net/c/f?sv=2024&se=1&sig={_b64url(40)}%3D", None),
        ("storage account key", f"DefaultEndpointsProtocol=https;AccountName=acct;AccountKey={_alnum(86)}==;EndpointSuffix=core.windows.net", None),
        ("service bus shared access key", f"Endpoint=sb://ns.servicebus.windows.net/;SharedAccessKeyName=r;SharedAccessKey={_alnum(43)}=", None),
        ("SQL connection string password", f"Driver={{ODBC Driver 18}};Server=tcp:x.database.windows.net;Uid=etl;Pwd={pw};Encrypt=yes", pw),
        ("Entra client secret", f"client_secret={entra}", entra),
        ("Entra client secret, bare", f"config dump: {entra} (from env)", entra),
        ("Databricks PAT", "workspace token dapi" + _hex(32), None),
        ("Databricks OAuth secret", "sp secret dose" + _hex(32), None),
        ("GitHub token", "remote https://" + "ghp_" + _alnum(36) + "@github.com/o/r.git", None),
        ("GitHub fine-grained token", "github_pat_" + _alnum(22) + "_" + _alnum(59), None),
        ("Azure DevOps PAT in a remote", f"git push https://ann:{_alnum(52)}@dev.azure.com/org/p/_git/r", None),
        ("AWS access key", "AKIA" + "".join(rnd.choice(string.ascii_uppercase + string.digits) for _ in range(16)), None),
        ("AI model key", "model call with sk-" + "ant-api03-" + _b64url(80), None),
        ("Google AI key", "AIza" + _b64url(35), None),
        ("GitLab token", "glpat-" + _b64url(20), None),
        ("npm token", "npm_" + _alnum(36), None),
        ("Slack token", "xox" + "b-" + _alnum(12) + "-" + _alnum(24), None),
        ("Slack webhook", "posted to https://hooks.slack.com/services/T" + _alnum(8) + "/B" + _alnum(8) + "/" + _alnum(24), None),
        ("Teams webhook", "posted to https://contoso.webhook.office.com/webhookb2/" + _alnum(20) + "/IncomingWebhook/" + _alnum(32), None),
        ("local API token", "integration token fdt_" + _b64url(32), None),
        ("VS Code pairing token", "pairing kfp_" + _b64url(32), None),
        ("PEM private key (Snowflake key pair)", "snowflake key loaded:\n" + pem, pem_body),
        ("password argument", f"ran: tool --password {pw} --user ann", pw),
        ("password in JSON", '{"user": "ann", "password": "' + pw + ' with spaces"}', pw),
        ("Snowflake passphrase", f"private_key_passphrase='{pw}'", pw),
        ("session cookie", f"Set-Cookie: session={_b64url(48)}; HttpOnly", None),
        ("OAuth authorization code", f"redirect http://localhost:8400/?code={_b64url(60)}&state=x", None),
        ("basic credentials", f"proxy said Basic {_alnum(30)}==", None),
        ("api key header", f"x-api-key: {_alnum(32)}", None),
        ("API Management subscription key", f"Ocp-Apim-Subscription-Key: {_hex(32)}", None),
        ("request cookie", f"Cookie: .AspNetCore.Session={_b64url(48)}; ai_user={_alnum(20)}", None),
        ("Power BI embed token", "embed token H4sI" + _b64url(400) + "==", None),
        ("PEM key in one-line JSON", '{"type": "service_account", "private_key": "-----BEGIN ' + 'PRIVATE KEY-----\\n'
         + "\\n".join(_alnum(64) for _ in range(3)) + '\\n-----END ' + 'PRIVATE KEY-----\\n"}', None),
    ]
    return [(kind, line, secret if secret is not None else _secret_of(line)) for kind, line, secret in rows]


def _secret_of(line: str) -> str:
    """The longest random-looking run in ``line``: what was planted there."""
    return max(re.findall(r"[A-Za-z0-9_~+/.\-]{20,}", line), key=len)


PERSONAL = [
    ("email", "signed in as ann.lee@contoso.com", "ann.lee@contoso.com"),
    ("guest email", "guest ann_fabrikam.com#EXT#@contoso.onmicrosoft.com", "ann_fabrikam.com"),
    ("tenant domain", "tenant contoso.onmicrosoft.com", "contoso.onmicrosoft"),
    ("SharePoint host", "https://contoso-my.sharepoint.com/personal/ann_contoso_com/Documents", "contoso-my"),
    ("OneDrive org folder", r"C:\Users\Ann Lee\OneDrive - Contoso Ltd\lake", "Contoso Ltd"),
    ("user name in a path", r"Traceback: File C:\Users\Ann Lee\AppData\x.py", "Ann Lee"),
    ("tenant id", "tenant 9f8e7d6c-5b4a-3210-fedc-ba9876543210", "9f8e7d6c-5b4a"),
]

# Detectors written apart from redact.py: what a secret scanner (gitleaks, GitHub secret scanning)
# would flag. None of these may match anything in a bundle.
DETECTORS = {
    "JWT": re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    "PEM key": re.compile(r"PRIVATE KEY"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}"),
    "AWS key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "sk- key": re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    "Google key": re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    "Databricks": re.compile(r"\bd(?:api|ose)[0-9a-f]{32}"),
    "Slack": re.compile(r"\bxox[a-z]-|hooks\.slack\.com/services/"),
    "Teams webhook": re.compile(r"(?i)IncomingWebhook/"),
    "app token": re.compile(r"\b(?:fdt|kfp)_[A-Za-z0-9_\-]{16,}"),
    "GitLab/npm": re.compile(r"\b(?:glpat-|npm_)[A-Za-z0-9_\-]{20,}"),
    "Entra secret": re.compile(r"[A-Za-z0-9_~.\-]{3}\dQ~[A-Za-z0-9_~.\-]{20,}"),
    "credential in URL": re.compile(r"://[^/\s:@\[]+:[^@/\s\[]+@"),
    "name=secret": re.compile(r"(?i)\b(?:password|passwd|pwd|passphrase|secret|accountkey|sharedaccesskey|"
                              r"[a-z_]*token|api[_-]?key|sig|code)\b[\"']?\s*[=:]\s*[\"']?(?!\[removed\]|\[webhook\])[A-Za-z0-9+/_~.\-]{6,}"),
    "bearer/basic": re.compile(r"(?i)\b(?:bearer|basic)\s+(?!\[removed\])[A-Za-z0-9+/=._\-]{12,}"),
    "cookie": re.compile(r"(?i)cookie:\s*(?!\[removed\])\S"),
    "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+\.[A-Za-z]{2,}"),
    "GUID": re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
}
# A long run with upper case, lower case and digits mixed is what a token or key looks like once its
# label is gone. Hex (hashes, commits) and words are fine.
LONG_RANDOM = re.compile(r"[A-Za-z0-9+/_\-]{32,}")


def _looks_random(run: str) -> bool:
    return (any(c.isupper() for c in run) and any(c.islower() for c in run) and any(c.isdigit() for c in run)
            and not re.fullmatch(r"[0-9a-fA-F]+", run))


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """A PC whose every bundle source holds every planted secret and personal detail."""
    planted = _planted()
    lines = [line for _, line, _ in planted] + [line for _, line, _ in PERSONAL]
    text = "\n".join(f"2026-11-11 01:00:00 ERROR {line}" for line in lines) + "\n"

    local = tmp_path / "la"
    data = local / "FabricDesktop"
    agent = tmp_path / "agent"
    (agent / "logs").mkdir(parents=True)
    data.mkdir(parents=True)
    for path in (data / "sidecar.log", data / "sidecar.prev.log", data / "app-crash.log",
                 agent / "logs" / "agent.log", agent / "logs" / "agent-crash.log"):
        path.write_text(text, encoding="utf-8")

    jwt = planted[0][2]
    # Settings and policy values that name the organization or hold a secret.
    (data / "app-settings.json").write_text(json.dumps({
        "client_id": "0a1b2c3d-4e5f-6789-abcd-ef0123456789",
        "sql_temp_dir": r"\\fileserver.contoso.local\scratch\ann",
        "wh_monitoring_auto": ["1a2b3c4d-1111-2222-3333-444455556666"],
    }), encoding="utf-8")
    policy = tmp_path / "pd" / "FabricDesktop"
    policy.mkdir(parents=True)
    (policy / "policy.json").write_text(json.dumps({
        "support_url": f"https://help.contoso.com/ticket?token={jwt}",
        "telemetry_connection_string": "InstrumentationKey=" + str(uuid.uuid4())
                                       + ";IngestionEndpoint=https://contoso.in.applicationinsights.azure.com/",
        "allowed_tenants": ["contoso.onmicrosoft.com", "fedcba98-7654-3210-0123-456789abcdef"],
        "notebook_package_folder": r"\\wheels.contoso.local\approved",
        "dbconnect_index_url": f"https://ann:{planted[14][2]}@pkgs.contoso.com/simple",
        "platform": {"prod": {"workspace_id": "abcdefab-1234-5678-9abc-def012345678"}},
        "contoso_custom_note": f"password={jwt}",  # an unknown key IT typed: counted, never shown
    }), encoding="utf-8")

    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("FABRIC_DESKTOP_HOME", str(agent))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return {"data": data, "agent": agent, "planted": planted, "tmp": tmp_path}


def _bundle_files(seeded) -> dict[str, str]:
    """The zip Settings > Save support bundle writes, file by file."""
    svc = Service(root=seeded["data"])
    out = seeded["tmp"] / "out"
    saved = svc.support_bundle_save(str(out))
    with zipfile.ZipFile(saved["path"]) as z:
        return {name: z.read(name).decode("utf-8") for name in z.namelist()}


def test_every_source_is_in_the_bundle(seeded):
    files = _bundle_files(seeded)
    assert set(files) == {"about.json", "sidecar.log", "sidecar.prev.log", "app-crash.log",
                          "agent.log", "agent-crash.log", "CONTENTS.txt"}
    for name in ("sidecar.log", "agent.log", "app-crash.log"):
        assert files[name].count("[removed]") >= len(seeded["planted"]) - 2, name  # the seeded lines are there


def test_no_planted_secret_reaches_any_file(seeded):
    files = _bundle_files(seeded)
    leaks = [f"{kind} in {name}" for kind, _, secret in seeded["planted"] for name, text in files.items()
             if secret in text or any(piece in text for piece in secret.split("\n") if len(piece) >= 16)]
    assert not leaks, leaks


def test_no_personal_detail_reaches_any_file(seeded):
    files = _bundle_files(seeded)
    leaks = [f"{kind} in {name}" for kind, _, value in PERSONAL for name, text in files.items() if value in text]
    for word in ("contoso", "fabrikam", "fileserver", "ann_"):
        leaks += [f"'{word}' in {name}" for name, text in files.items() if word in text.lower()]
    assert not leaks, leaks


def test_an_independent_scan_finds_nothing_secret_shaped(seeded):
    files = _bundle_files(seeded)
    hits = [f"{label} in {name}: {m.group(0)[:24]}" for name, text in files.items()
            for label, pattern in DETECTORS.items() for m in pattern.finditer(text)]
    hits += [f"random-looking run in {name}: {run[:24]}" for name, text in files.items()
             for run in LONG_RANDOM.findall(text) if _looks_random(run)]
    assert not hits, hits


def test_the_preview_shows_the_same_redacted_contents(seeded):
    svc = Service(root=seeded["data"])
    shown = svc.support_bundle_preview()
    files = _bundle_files(seeded)
    assert [f["name"] for f in shown["files"]] == list(files)
    assert shown["contents"] == files["CONTENTS.txt"]


def test_a_key_cut_in_half_by_the_log_tail_leaves_no_key_body(seeded):
    """The bundle keeps the last lines of a log: when that cut lands inside a private key, its
    BEGIN line is gone and only the body and END line are left. The body must not be kept."""
    body = [_alnum(64) for _ in range(support_bundle.LOG_LINES + 100)]  # the cut falls inside the key
    key = "-----BEGIN " + "PRIVATE KEY-----\n" + "\n".join(body) + "\n-----END " + "PRIVATE KEY-----\n"
    (seeded["data"] / "sidecar.log").write_text("start\n" + key + "after the key\n", encoding="utf-8")
    files = _bundle_files(seeded)
    assert "after the key" in files["sidecar.log"]
    assert not any(line in files["sidecar.log"] for line in body)
    assert "PRIVATE KEY" not in files["sidecar.log"]


def test_a_key_cut_by_the_byte_limit_leaves_no_key_body(seeded):
    """The other cut: a log of few but long lines is read from its last TAIL_BYTES only."""
    body = [rnd.token_urlsafe(48_000) for _ in range(support_bundle.TAIL_BYTES // 64_000 + 20)]
    assert len(body) < support_bundle.LOG_LINES  # the byte limit cuts, not the line count
    key = "-----BEGIN " + "PRIVATE KEY-----\n" + "\n".join(body) + "\n-----END " + "PRIVATE KEY-----\n"
    (seeded["data"] / "sidecar.log").write_text("start\n" + key + "after the key\n", encoding="utf-8")
    files = _bundle_files(seeded)
    assert "after the key" in files["sidecar.log"]
    assert not any(line[:64] in files["sidecar.log"] for line in body)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-be"])
def test_a_utf16_log_is_redacted_too(seeded, encoding):
    """A log saved as UTF-16 (PowerShell 5's Out-File does) has a NUL between its characters, which
    would hide a token from redaction if it were read as UTF-8."""
    planted = seeded["planted"]
    text = "".join(f"{line}\n" for _, line, _ in planted)
    raw = text.encode(encoding)
    if encoding == "utf-16-be":
        raw = b"\xfe\xff" + raw
    (seeded["data"] / "sidecar.log").write_bytes(raw)
    log = _bundle_files(seeded)["sidecar.log"]
    assert "\x00" not in log and "\ufeff" not in log
    assert log.count("[removed]") >= len(planted) - 2
    assert not [kind for kind, _, secret in planted if secret in log]
