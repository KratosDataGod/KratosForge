"""``NotSetUp``, the error for a step nobody has taken yet, so callers decide by type, never by wording.

A leaf module: it imports nothing from the package, so anything can raise it without a cycle.
"""

from __future__ import annotations


class NotSetUp(LookupError):
    """A step nobody has taken yet: no workspace assigned, no metadata project open, a lakehouse not
    promoted to the environment. Not a fault, so Monitoring and Data mesh Home show it as "Not set
    up" (``workloads.model.classify_error``). A ``LookupError`` so existing handlers still catch it.
    The message says what to do; callers show it as is."""
