"""A guard for paged reads (Fabric's ``continuationUri``, OneLake's ``x-ms-continuation``).

A service that keeps sending the same next-page link, or one with no end, would otherwise keep a
list call (and the user's token) going for ever. ``Pages`` stops it, and says the list was cut
short: a partial list is never returned as if it were whole.
"""

from __future__ import annotations

MAX_PAGES = 10_000  # far more than any tenant's workspaces, items or folder needs


class PagingError(RuntimeError):
    """A paged read was stopped before its end."""


class Pages:
    def __init__(self, what: str, max_pages: int | None = None):
        self.what, self.max_pages = what, max_pages or MAX_PAGES
        self._seen: set[str] = set()

    def follow(self, token: str) -> str:
        """``token`` (the next-page link or continuation), after checking it may be followed."""
        if token in self._seen:
            raise PagingError(f"Listing {self.what} stopped: the service sent the same next page twice, "
                              "so the list would never end. Try again later.")
        if len(self._seen) + 1 >= self.max_pages:
            raise PagingError(f"Listing {self.what} stopped after {self.max_pages:,} pages, so the list isn't complete.")
        self._seen.add(token)
        return token
