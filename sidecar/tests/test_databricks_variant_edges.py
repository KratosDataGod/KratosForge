"""Databricks VARIANT on this PC: negative zero, false, a MAP without STRING keys, a path without $, a
NULL VARIANT, casts that give NULL under try_variant_get, and a double that can't be a decimal here."""

import pytest

from fabric_desktop import databricks_variant as dv


def get(text, target, safe=True):
    return dv.variant_get(text, "JSON", "[]", target, safe)


def test_negative_zero_and_false_print_as_spark_does():
    assert dv._java_double(-0.0) == "-0.0"
    assert dv._write(False) == "false"


def test_a_map_needs_string_keys_for_to_variant_object():
    with pytest.raises(ValueError, match="MAP with STRING keys only"):
        dv.to_variant('{"1": "a"}', "MAP(INTEGER, VARCHAR)")


def test_a_path_must_start_with_the_dollar_sign():
    with pytest.raises(ValueError, match="INVALID_VARIANT_GET_PATH"):
        dv.path("data.a")


def test_a_null_variant_gets_null():
    assert get(None, "int") is None


def test_casts_that_cant_be_made_give_null_under_try_variant_get():
    assert get('{"a": 1}', "int") is None          # an object to a scalar
    assert get("1", "array<int>") is None          # a scalar to an array
    assert get("12345.6", "decimal(4,2)") is None  # too many digits for the decimal


def test_casts_that_can_be_made_keep_spark_text():
    assert get("true", "boolean") == "true"
    assert get("1.5", "double") == "1.5"


def test_a_double_with_an_exponent_to_a_decimal_is_refused_rather_than_guessed():
    with pytest.raises(ValueError, match="can't run on this PC the way it runs in Databricks"):
        get("1.5E300", "decimal(10,2)")
