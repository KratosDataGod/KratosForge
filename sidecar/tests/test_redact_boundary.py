"""The redactor at its boundaries (mutation batch 208, a boundary re-score of batch 1,
docs/quality/mutation-testing.md): a webhook URL's one-character path isn't blanked wherever it appears,
and a message of exactly the limit is kept whole."""

from __future__ import annotations

from fabric_desktop import redact


def test_a_webhooks_one_character_path_isnt_blanked_everywhere():
    text = "POST https://hooks.example.com/ failed for a/b"
    assert redact.without_webhook(text, "https://hooks.example.com/") == "POST [webhook] failed for a/b"


def test_a_message_of_exactly_the_limit_is_kept_whole():
    assert redact.one_line("x" * 300) == "x" * 300
    assert redact.one_line("x" * 301) == "x" * 299 + "…"
    assert redact.one_line("abcd", limit=4) == "abcd"
