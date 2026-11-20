"""Local Delta types at their exact edges (boundary re-score of batch 1, mutation batch 159,
docs/quality/mutation-testing.md): a quoted empty name, a decimal of exactly 38 digits, and which
whole numbers a float holds."""

from __future__ import annotations

import pyarrow as pa

from fabric_desktop.localdata import delta


def test_two_quotes_are_an_empty_name():
    assert delta.unquote('""') == ""
    assert delta.unquote("``") == ""
    assert delta.unquote('"') == '"'


def test_a_decimal_of_exactly_38_digits_is_kept():
    assert delta._fabric_type(pa.decimal256(38, 2), "d") == pa.decimal128(38, 2)
    assert delta.delta_writable(pa.decimal256(38, 2))
    assert not delta.delta_writable(pa.decimal256(39, 2))


def test_a_float_holds_a_smallint_and_a_double_holds_an_int():
    assert delta._keeps_values(pa.int16(), pa.float32())
    assert delta._keeps_values(pa.int32(), pa.float64())
    assert not delta._keeps_values(pa.int32(), pa.float32())
    assert not delta._keeps_values(pa.int64(), pa.float64())
