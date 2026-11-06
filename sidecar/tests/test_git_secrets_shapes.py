"""The commit scan and git message redaction cover these key shapes. Token-shaped values are joined
from parts, so none sits in the repository whole."""

from __future__ import annotations

import pytest

from fabric_desktop.git import secrets

J = "".join
PEM_BODY = "MIIBVgIBADANBgkqhkiG9w0BAQEFAAS"
SHAPES = [
    ("Private key", J(["-----BEGIN ", "PRIVATE KEY-----\n"]) + PEM_BODY + "\n" + J(["-----END ", "PRIVATE KEY-----"]), PEM_BODY),
    ("Teams webhook", "post to https://outlook.office.com/webhook/" + J(["abc", "def123", "/IncomingWebhook/xyz"]),
     J(["abc", "def123", "/IncomingWebhook/xyz"])),
    ("Password in code", "{'password': '" + J(["correct horse", " battery"]) + "'}", J(["correct horse", " battery"])),
    ("Password in code", 'settings = {"pwd": "' + J(["Sup3r", "Secret"]) + '"}', J(["Sup3r", "Secret"])),
    ("Token in a URL", "GET /x?token=" + J(["abc123", "XYZ789"]) + "&y=1", J(["abc123", "XYZ789"])),
    ("Token in a URL", "https://h/cb?access_token=" + J(["abc123", "XYZ789"]), J(["abc123", "XYZ789"])),
    ("API key", "key " + J(["s", "k-", "abcdefghijklmnopqrstuv"]), J(["s", "k-", "abcdefghijklmnopqrstuv"])),
]


@pytest.mark.parametrize("kind, text, secret", SHAPES, ids=[f"{s[0]}-{i}" for i, s in enumerate(SHAPES)])
def test_the_scan_finds_it_and_a_message_hides_it(kind, text, secret):
    assert kind in [f["kind"] for f in secrets.scan_text(text)]
    assert secret not in secrets.redact(text)


def test_a_private_key_keeps_its_begin_and_end_lines_and_a_cut_off_one_hides_the_rest():
    begin, end = J(["-----BEGIN ", "RSA PRIVATE KEY-----"]), J(["-----END ", "RSA PRIVATE KEY-----"])
    assert secrets.redact(f"{begin}\n{PEM_BODY}\n{end}\nafter") == f"{begin}***{end}\nafter"
    assert secrets.redact(f"{begin}\n{PEM_BODY}") == f"{begin}***"


@pytest.mark.parametrize("text", ['password = "{{ pw }}"', "password: '********'", 'if password == "":'])
def test_a_placeholder_password_is_not_a_finding(text):
    assert secrets.scan_text(text) == []


@pytest.mark.parametrize("text", [
    "GET /x?token=short&y=1", "risk-assessment-for-the-quarterly-planning-cycle", "https://example.webhook.example.com/x",
])
def test_ordinary_text_is_left_alone(text):
    assert secrets.scan_text(text) == []
    assert secrets.redact(text) == text
