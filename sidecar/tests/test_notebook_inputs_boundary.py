"""A notebook's run values at their boundary (mutation batch 210, a boundary re-score of batch 9,
docs/quality/mutation-testing.md): a value of exactly MAX_VALUE characters is allowed."""

from __future__ import annotations

import pytest

from fabric_desktop import notebook_inputs as ni


@pytest.mark.parametrize("group", ["parameters", "widgets"])
def test_a_value_of_exactly_max_value_characters_is_allowed(group):
    value = "x" * ni.MAX_VALUE
    assert ni.check_values({group: {"n": value}})[group] == {"n": value}
    with pytest.raises(ValueError, match="longer than"):
        ni.check_values({group: {"n": value + "x"}})
