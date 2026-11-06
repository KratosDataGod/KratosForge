"""Characterization of the five redactors (EA-028): what each one hides today, on one set of
secret-shaped inputs. No redactor is changed here; a change to what one hides has to change this
table too, and docs/audits/REDACTORS.md, which lists where they differ. Consolidating them is
tracked separately."""

from __future__ import annotations

import pytest

from fabric_desktop import livetest, redact
from fabric_desktop.git import secrets as git_secrets

J = "".join
# (case, text, the secret in it). Token-shaped values are joined from parts, so none sits in the
# repository whole (GitHub push protection blocks it), as in app/tests/redact-cases.json.
CASES = [
 ("Bearer header", "Authorization: Bearer " + J(["abcDEF123", "ghiJKL456"]), J(["abcDEF123", "ghiJKL456"])),
 ("Basic header", "Authorization: Basic " + "dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
 ("JWT", "token " + J(["eyJhbGciOiJSUzI1NiJ9", ".", "eyJzdWIiOiJ4eHh4eHgifQ", ".", "c2lnbmF0dXJlc2ln"]) + " end", J(["eyJhbGciOiJSUzI1NiJ9", ".", "eyJzdWIiOiJ4eHh4eHgifQ", ".", "c2lnbmF0dXJlc2ln"])),
 ("SAS sig", "https://a.blob.core.windows.net/c/f?sv=1&sig=" + J(["AbCdEfGhIjKlMn", "OpQrStUv%3D"]) + "&se=1", J(["AbCdEfGhIjKlMn", "OpQrStUv%3D"])),
 ("URL user:pass", "https://ann:" + J(["hunter2", "pass"]) + "@dev.azure.com/x", J(["hunter2", "pass"])),
 ("URL user only", "https://" + J(["annsecretuser"]) + "@dev.azure.com/x", "annsecretuser"),
 ("Teams webhook", "post to https://contoso.webhook.office.com/webhookb2/" + J(["abc", "def123", "/IncomingWebhook/xyz"]), J(["abc", "def123", "/IncomingWebhook/xyz"])),
 ("Legacy webhook", "post to https://outlook.office.com/webhook/" + J(["abc", "def123", "/IncomingWebhook/xyz"]), J(["abc", "def123", "/IncomingWebhook/xyz"])),
 ("GitHub token", "push with " + J(["gh", "p_", "A" * 36]), J(["gh", "p_", "A" * 36])),
 ("Entra client secret", "secret " + J(["abc", "8Q~", "x" * 34]) + " end", J(["abc", "8Q~", "x" * 34])),
 ("AWS key", "key " + J(["AK", "IA", "ABCDEFGHIJKLMNOP"]), J(["AK", "IA", "ABCDEFGHIJKLMNOP"])),
 ("Slack token", "tok " + J(["xo", "xb-", "1234567890-abcdef"]), J(["xo", "xb-", "1234567890-abcdef"])),
 ("Databricks token", "tok " + J(["da", "pi", "0123456789abcdef0123456789abcdef"]), J(["da", "pi", "0123456789abcdef0123456789abcdef"])),
 ("sk- API key", "key " + J(["s", "k-", "abcdefghijklmnopqrstuv"]), J(["s", "k-", "abcdefghijklmnopqrstuv"])),
 ("PEM private key", J(["-----BEGIN ", "PRIVATE KEY-----\n"]) + "MIIBVgIBADANBgkqhkiG9w0BAQEFAAS\n" + J(["-----END ", "PRIVATE KEY-----"]), "MIIBVgIBADANBgkqhkiG9w0BAQEFAAS"),
 ("Connection-string password", "Server=x;User Id=a;Password=" + J(["Sup3r", "Secret"]) + ";", J(["Sup3r", "Secret"])),
 ("Storage AccountKey", "AccountKey=" + J(["A" * 20, "b" * 24]) + "==;", J(["A" * 20, "b" * 24]) + "=="),
 ("git extraheader", "http.extraheader=AUTHORIZATION: basic " + J(["Zm9vOmJh", "cg=="]), J(["Zm9vOmJh", "cg=="])),
 ("Quoted password", "{'password': '" + J(["correct horse", " battery"]) + "'}", J(["correct horse", " battery"])),
 ("Bare token=", "GET /x?token=" + J(["abc123", "XYZ789"]) + "&y=1", J(["abc123", "XYZ789"])),
]

# What each redactor does with the secret, per case: removed, masked (git/secrets keeps its first
# characters, then …[hidden]), or kept (only a URL's user name with no password, which isn't a
# secret). Columns: redact.secrets, redact.for_support, redact.walk on a field whose name isn't
# secret-like (logs and integration results), git/secrets.redact, livetest.scrub.
TODAY = {
    'Bearer header': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'Basic header': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'JWT': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'SAS sig': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'URL user:pass': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'URL user only': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'Teams webhook': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'Legacy webhook': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'GitHub token': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'Entra client secret': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'AWS key': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'Slack token': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'Databricks token': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'sk- API key': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'PEM private key': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'Connection-string password': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'Storage AccountKey': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'git extraheader': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'Quoted password': ('removed', 'removed', 'removed', 'removed', 'removed'),
    'Bare token=': ('removed', 'removed', 'removed', 'removed', 'removed'),
}

REDACTORS = {
    "secrets": redact.secrets,
    "for_support": redact.for_support,
    "walk": lambda text: redact.walk({"note": text})["note"],
    "git": git_secrets.redact,
    "livetest": livetest.scrub,
}


def outcome(out: str, secret: str) -> str:
    if secret in out:
        return "kept"
    if git_secrets.mask(secret) in out:
        return "masked"
    return "removed"


def test_every_case_has_a_row():
    assert [c[0] for c in CASES] == list(TODAY)


@pytest.mark.parametrize("name, text, secret", CASES, ids=[c[0] for c in CASES])
def test_what_each_redactor_hides_today(name, text, secret):
    got = tuple(outcome(f(text), secret) for f in REDACTORS.values())
    assert got == TODAY[name], dict(zip(REDACTORS, got, strict=True))


def test_walk_blanks_fields_named_like_secrets_and_keeps_counts():
    # logs._redacted and integrations.core.redact_result both call redact.walk.
    assert redact.walk({"password": "x y", "Api-Key": "abc", "sas": "q", "token_count": 3, "n": [None, 1.5]}) == \
        {"password": "[removed]", "Api-Key": "[removed]", "sas": "[removed]", "token_count": 3, "n": [None, 1.5]}


def test_the_walkers_are_one_walker():
    from fabric_desktop import logs
    from fabric_desktop.integrations import core
    value = {"secret": "s", "note": "Authorization: Bearer " + J(["abcDEF123", "ghiJKL456"])}
    assert logs._redacted(value) == core.redact_result(value) == redact.walk(value)


def test_git_messages_use_the_shared_redactor():
    # git/secrets.redact is redact.secrets: same output, same [removed] placeholder, nothing masked.
    for _name, text, _secret in CASES:
        assert git_secrets.redact(text) == redact.secrets(text)
    assert git_secrets.redact(None) == git_secrets.redact("") == ""


def test_livetest_scrub_also_cuts_to_2000_characters():
    assert len(livetest.scrub("x" * 5000)) == 2000


def test_the_audit_page_shows_this_table():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[2] / "docs" / "audits" / "REDACTORS.md").read_text(encoding="utf-8")
    table = page.split("## What each one does with the secret", 1)[1].split("\n## ", 1)[0]
    rows = [[c.strip() for c in line.strip("|").split("|")] for line in table.splitlines() if line.startswith("| ")]
    shown = [tuple(c.split(" (")[0] for c in r[1:]) for r in rows[2:]]  # "removed (as an email)" is removed
    assert rows[0] == ["Input", *REDACTORS]
    assert shown == list(TODAY.values()), "docs/audits/REDACTORS.md and TODAY list the same outcomes, in the same order"


# One sample per pre-commit detection pattern in git/secrets.PATTERNS: the shared redactor removes
# whatever that scan would find, so moving git messages onto it lost nothing.
GIT_SAMPLES = [
    J(["-----BEGIN RSA ", "PRIVATE KEY-----\nMIIB\n-----END RSA ", "PRIVATE KEY-----"]),
    "AccountKey=" + "A" * 44 + "==",
    "?a=1&sig=" + "A" * 24,
    "Password=" + J(["Sup3r", "S3cret"]) + ";",
    J(["gh", "p_", "A" * 36]),
    J(["abc", "8Q~", "x" * 34]),
    J(["AK", "IA", "ABCDEFGHIJKLMNOP"]),
    J(["xo", "xb-", "1234567890-ab"]),
    J(["da", "pi", "0" * 32]),
    J(["s", "k-", "a" * 24]),
    J(["eyJ", "a" * 12, ".eyJ", "b" * 12, ".", "c" * 12]),
    "https://x.webhook.office.com/webhookb2/" + J(["abc@def", "/IncomingWebhook/x/y"]),
    "password = '" + J(["long pass", " phrase"]) + "'",
    "?x=1&refresh_token=" + "a" * 12,
    "&code=" + "a" * 12,
    "secret_key = '" + J(["abcd1234", "efgh"]) + "'",
    "auth_token='" + J(["abcd1234", "efgh"]) + "'",
]


@pytest.mark.parametrize("text", GIT_SAMPLES)
def test_the_shared_redactor_removes_whatever_the_git_scan_finds(text):
    found = [m.groupdict().get("s") or m.group(0) for _k, p, _f in git_secrets.PATTERNS for m in p.finditer(text)]
    assert found, "each sample is one the pre-commit scan detects"
    out = redact.secrets(text)
    assert not [s for s in found if s in out], out
