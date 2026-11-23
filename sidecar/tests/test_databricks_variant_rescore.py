"""Databricks VARIANT on this PC: the case batch 233 (docs/quality/mutation-testing.md) found untested. A
38-digit integer cast to DECIMAL(38, 38) needs 76 digits to round before it is found too wide, and still
gives try_variant_get's NULL, or variant_get's cast error by name."""

import pytest

from fabric_desktop import databricks_variant as dv


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def get(text, target, safe):
    return dv.variant_get(dv.parse_json(text, False), "JSON", "[]", target, safe)


WIDEST = "9" * 38  # the widest integer a VARIANT keeps as a decimal


def test_the_widest_variant_integer_at_the_finest_scale_is_null_with_try():
    assert dv.parse_json(WIDEST, False) == WIDEST
    assert get(WIDEST, "decimal(38,38)", safe=True) is None


def test_the_widest_variant_integer_at_the_finest_scale_stops_by_name():
    with pytest.raises(ValueError, match=r"^\[INVALID_VARIANT_CAST\]"):
        get(WIDEST, "decimal(38,38)", safe=False)


def test_the_widest_variant_integer_fits_at_scale_0():
    assert get(WIDEST, "decimal(38,0)", safe=True) == WIDEST
