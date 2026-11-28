"""Mutation batch 527: audit_anchor.py where the earlier tests didn't look. The error codes that mean a
full Application log, and a line count of zero, which is a trail with no lines yet."""

from __future__ import annotations

import pytest

from fabric_desktop import audit_anchor as aa


@pytest.mark.parametrize("code", [112, 1500, 1502])
def test_a_full_log_is_told_apart_by_each_of_its_codes(code):
    assert aa.kind(OSError(code, "x")) == aa.FULL


@pytest.mark.parametrize("code", [113, 1499, 1503])
def test_a_code_next_to_a_full_log_code_is_unavailable(code):
    assert aa.kind(OSError(code, "x")) == aa.UNAVAILABLE


def test_an_anchor_with_no_lines_is_read_back():
    anchor = aa.Anchor("f" * 32, "abc", 0)
    assert aa.parse(aa.text(anchor)) == anchor


def test_an_anchor_with_one_line_is_read_back_and_minus_one_is_not():
    assert aa.parse(aa.text(aa.Anchor("f" * 32, "abc", 1))) == aa.Anchor("f" * 32, "abc", 1)
    assert aa.parse(aa.text(aa.Anchor("f" * 32, "abc", -1))) is None
