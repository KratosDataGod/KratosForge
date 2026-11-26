"""secret_redaction's helpers for values kept elsewhere (mutation batch 501): redact_values and mask_cut try
the longest value first, so a value that holds another is replaced whole, and mask_cut acts only on a cut
that falls inside a value. They take the values as an argument, so nothing is remembered here."""

from __future__ import annotations

from fabric_desktop.secret_redaction import REDACTED, mask_cut, redact_values

LONG = "xyzabcdefgh"  # pragma: allowlist secret (made up)
SHORT = "defgQ"  # starts inside LONG, so a shorter-first pass would replace only part of LONG


def test_redact_values_replaces_a_value_holding_another_whole():
    assert redact_values("key abcdefgh end", ["abcd", "abcdefgh"]) == f"key {REDACTED} end"
    assert redact_values("key abcdefgh end", ["abcdefgh", "abcd"]) == f"key {REDACTED} end"


def test_mask_cut_hides_the_longest_value_the_cut_falls_inside():
    text = "foo " + LONG[:-1] + "..."
    assert mask_cut(text, [SHORT, LONG]) == f"foo {REDACTED}..."
    assert mask_cut(text, [LONG, SHORT]) == f"foo {REDACTED}..."


def test_mask_cut_leaves_a_whole_value_to_redact_values():
    # A whole value before the "..." isn't a cut inside it: the preview replaced it before cutting it.
    text = "foo " + LONG + "..."
    assert mask_cut(text, [LONG]) == text
    assert mask_cut(redact_values(text, [LONG]), [LONG]) == f"foo {REDACTED}..."
