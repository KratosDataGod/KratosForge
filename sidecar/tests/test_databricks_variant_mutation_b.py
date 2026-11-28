"""Mutation batch 537: databricks_variant.py's survivors on today's main. A JSON string or key holding half
of a UTF-16 pair on its own (\\ud800 to \\udfff) is refused by name, wherever it sits in the value, and
by try_parse_json too; a whole pair is one character and reads. A whole number one digit too wide for a
scale-0 decimal doesn't fit, and a value that isn't a VARIANT names variant_get or try_variant_get.
Everything here is pure: text in, text or an error out."""

import pytest

from fabric_desktop import databricks_variant as dv


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.mark.parametrize("text", ['"\\ud800"', '["x", ["\\udc00y"]]', '{"\\udbff": 1}', '{"a": {"b": "\\ud83d"}}'])
@pytest.mark.parametrize("safe", [False, True])
def test_half_a_utf16_pair_anywhere_in_the_value_is_refused_by_name(text, safe):
    with pytest.raises(ValueError, match="half of a UTF-16 pair"):
        dv.parse_json(text, safe)


def test_a_whole_utf16_pair_and_plain_text_still_read():
    assert dv.parse_json('{"k": ["\\ud83d\\ude00", "\\u00e9"]}', False) == '{"k":["😀","é"]}'
    assert dv._lone_surrogate(["a", {"b": "c"}]) is False
    assert dv._lone_surrogate([{"\ud800": 1}]) is True


@pytest.mark.parametrize(("safe", "name"), [(False, "variant_get"), (True, "try_variant_get")])
def test_a_value_that_is_not_a_variant_names_the_function_it_was_given_to(safe, name):
    with pytest.raises(ValueError, match=rf"^\[DATATYPE_MISMATCH\] {name}\(\) takes a VARIANT, not VARCHAR"):
        dv.variant_get("1", "VARCHAR", "[]", "int", safe)


def test_a_whole_number_one_digit_too_wide_for_a_scale_0_decimal_does_not_fit():
    assert dv.variant_get(dv.parse_json("12", False), "JSON", "[]", "decimal(2,0)", True) == "12"
    assert dv.variant_get(dv.parse_json("123", False), "JSON", "[]", "decimal(2,0)", True) is None
    with pytest.raises(ValueError, match="INVALID_VARIANT_CAST"):
        dv.variant_get(dv.parse_json("123", False), "JSON", "[]", "decimal(2,0)", False)
