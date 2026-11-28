"""Mutation-testing pins for paging (docs/quality/mutation-testing.md, batch 5)."""

from __future__ import annotations

import pytest

from fabric_desktop import paging


def test_the_default_limit_is_ten_thousand_pages_exactly():
    assert paging.MAX_PAGES == 10_000
    pages = paging.Pages("items")
    for n in range(paging.MAX_PAGES - 1):  # the first page is read before any link is followed
        pages.follow(f"p{n}")
    with pytest.raises(paging.PagingError, match="more than 10,000 pages"):
        pages.follow("one more")
