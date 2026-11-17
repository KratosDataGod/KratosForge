"""b64.py paths that mutation testing (batch 76, docs/quality/mutation-testing.md) showed no test pinned:
a character outside the alphabet that the lenient decoder would skip, and padding followed by data when
the standard library itself is lenient."""

from __future__ import annotations

import pytest

from fabric_desktop import b64


def test_characters_outside_the_alphabet_are_refused_even_when_skipping_them_would_decode():
    # base64.b64decode without validate skips the "*" and reads b"abc"
    with pytest.raises(ValueError):
        b64.decode_exact("YWJj****")


def test_padding_followed_by_data_is_refused_without_the_stdlibs_help(monkeypatch):
    monkeypatch.setattr(b64.base64, "b64decode", lambda s, validate=False: b"abc")
    with pytest.raises(ValueError, match="padding"):
        b64.decode_exact("YW=j")
    assert b64.decode_exact("YWJj") == b"abc"  # no padding at all is fine
