"""Mutation batch 119: git/secrets.py (docs/quality/mutation-testing.md). The survivors were the sizes the
scan stops at (2,000,000 bytes, 20,000 characters a line, a NUL in the first 8,000 bytes), how much of a
secret a finding shows, and a placeholder made of every one of x, * and . at once."""

from __future__ import annotations

from fabric_desktop.git import secrets

KEY = "-----BEGIN PRIVATE KEY-----"  # pragma: allowlist secret (only the header line, no key)


def test_a_long_secret_shows_four_characters_and_a_short_one_shows_one():
    assert secrets.mask("abcdefghijklm") == "abcd…[hidden]"  # 13 characters
    assert secrets.mask("abcdefghijkl") == "a…[hidden]"  # 12 characters
    assert secrets.mask("") == "…[hidden]"


def test_the_scan_reads_the_first_2_000_000_bytes():
    filler = ("x" * 99 + "\n") * 20_000  # 2,000,000 bytes
    assert len(filler) == 2_000_000
    cut = filler[: 2_000_001 - len(KEY)] + KEY  # its last character is byte 2,000,001
    assert secrets.scan_file("notes.txt", cut.encode()) == []
    whole = filler[: 2_000_000 - len(KEY)] + KEY
    assert [f["kind"] for f in secrets.scan_file("notes.txt", whole.encode())] == ["Private key"]


def test_a_line_is_read_up_to_20_000_characters():
    cut = "y" * (20_001 - len(KEY)) + KEY
    assert secrets.scan_text(cut) == []
    whole = "y" * (20_000 - len(KEY)) + KEY
    assert [f["kind"] for f in secrets.scan_text(whole)] == ["Private key"]


def test_a_nul_in_the_first_8000_bytes_makes_a_file_binary():
    body = KEY.encode()
    assert secrets.scan_file("a.txt", b"z" * 7999 + b"\0" + body) == []
    found = secrets.scan_file("a.txt", b"z" * 8000 + b"\0\n" + body)
    assert [f["kind"] for f in found] == ["Private key"]


def test_a_value_of_only_x_star_and_dot_is_a_placeholder():
    assert secrets.scan_text("Server=s;Password=x*.x;") == []  # pragma: allowlist secret (placeholder)
    assert secrets.scan_text("Server=s;Password=xxxx;") == []  # pragma: allowlist secret (placeholder)
