"""Mutation-testing follow-up for Databricks VARIANT on this PC (databricks_variant.py): where a number
stops being a BIGINT or a DECIMAL, integer cast ranges at their edges, how much of a value an error
repeats, a space in a string, nesting counted outside strings, and an empty or malformed path or
decimal type. Everything here is pure: text in, text or an error out."""

import json

import pytest

from fabric_desktop import databricks_variant as dv


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def get(text, target, safe=True, steps="[]"):
    return dv.variant_get(dv.parse_json(text, False), "JSON", steps, target, safe)


def test_an_error_repeats_at_most_100_characters_of_a_value():
    assert dv._shown("x" * 100) == "x" * 100
    assert dv._shown("x" * 101) == "x" * 100 + "…"
    with pytest.raises(ValueError) as caught:
        dv.path("y" * 150)
    assert "'" + "y" * 100 + "…'" in str(caught.value) and "y" * 101 not in str(caught.value)


def test_2_to_the_63_is_a_decimal_so_a_bigint_cast_stops_by_name():
    assert get("-9223372036854775808", "bigint") == "-9223372036854775808"  # the smallest BIGINT is a long
    assert get("9223372036854775807", "bigint") == "9223372036854775807"
    with pytest.raises(ValueError, match="can't tell how Databricks casts"):
        get("9223372036854775808", "bigint")
    with pytest.raises(ValueError, match="can't tell how Databricks casts"):
        get("-9223372036854775809", "bigint")


@pytest.mark.parametrize("target, bits", [("tinyint", 8), ("smallint", 16), ("int", 32)])
def test_an_integer_cast_holds_exactly_its_range(target, bits):
    top, bottom = 2 ** (bits - 1) - 1, -(2 ** (bits - 1))
    assert get(str(top), target) == str(top)
    assert get(str(bottom), target) == str(bottom)
    assert get(str(top + 1), target) is None
    assert get(str(bottom - 1), target) is None
    with pytest.raises(ValueError, match="INVALID_VARIANT_CAST"):
        get(str(top + 1), target, safe=False)


def test_a_decimal_has_at_most_38_digits_and_scale_38():
    scale_38 = "0." + "0" * 37 + "1"
    assert dv.parse_json(scale_38, False) == scale_38
    assert dv.parse_json("0." + "0" * 38 + "1", False) == "1.0E-39"  # scale 39: a DOUBLE
    assert dv.parse_json("1" * 38 + ".5", False) == "1.111111111111111E37"  # 39 digits: a DOUBLE


def test_a_decimal_keeps_all_38_digits():
    digits_38 = "1" * 37 + ".5"
    assert dv.parse_json(digits_38, False) == digits_38


def test_a_space_in_a_string_is_kept_and_a_control_character_escaped():
    assert dv.parse_json('" a\\u001fb"', False) == '" a\\u001Fb"'
    assert dv._string(" ") == '" "'


def test_nesting_is_counted_outside_strings():
    deep = "[" * 256 + "]" * 256
    assert dv.parse_json(deep, False) == deep
    with pytest.raises(ValueError, match="more than 256 levels"):
        dv.parse_json("[" * 257 + "]" * 257, True)
    # brackets inside a string, after an escaped quote or backslash, don't nest
    inside = json.dumps('\\"' + "[" * 300)
    assert dv._depth(inside) == 0
    assert dv.parse_json(inside, False) == inside
    assert dv._depth('["\\\\", [[]]]') == 3
    assert dv._depth('["a\\"]", [[]]]') == 3
    # siblings don't add up
    flat = "[" + ",".join(["[[1]]"] * 300) + "]"
    assert dv._depth(flat) == 3
    assert dv.parse_json(flat, False) == flat


def test_an_empty_path_is_not_a_path():
    assert dv.path("$") == []
    with pytest.raises(ValueError, match="INVALID_VARIANT_GET_PATH"):
        dv.path("")


def test_a_decimal_type_without_a_scale_stops_by_name():
    with pytest.raises(ValueError, match="can't tell how Databricks casts"):
        get("1.5", "decimal(10)")
    assert get("1.5", "decimal(10,2)") == "1.50"


def test_a_double_without_an_exponent_casts_to_a_decimal():
    assert get("1e2", "decimal(10,2)") == "100.00"  # 1e2 prints as 100.0
    with pytest.raises(ValueError, match="can't tell how Databricks casts"):
        get("1e20", "decimal(38,2)")  # 1.0E20
