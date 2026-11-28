"""excellence/exceptions at its edge, from the mutation run (batch 472): exactly MAX_EXCEPTIONS
exceptions are allowed, and one more is refused."""

from __future__ import annotations

import pytest

from fabric_desktop.excellence import exceptions as exc
from test_excellence_exceptions import REG, TODAY, ex


def test_exactly_the_most_exceptions_are_allowed_and_one_more_is_not():
    many = [ex(subject=f"resource-{i}") for i in range(exc.MAX_EXCEPTIONS)]
    assert len(exc.validate(many, REG, today=TODAY)) == exc.MAX_EXCEPTIONS == 1000
    with pytest.raises(ValueError, match="at most 1000 exceptions"):
        exc.validate([*many, ex(subject="one-more")], REG, today=TODAY)
