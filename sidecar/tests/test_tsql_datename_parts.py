"""DATENAME's date part is read only as one of T-SQL's date part names: any other text, a bracket-quoted
identifier holding SQL included, is refused by name and never written into the translated query."""
from __future__ import annotations

import pytest

from fabric_desktop.localdata.tsql import translate

STAMP = "CAST('2024-01-02 13:04:05' AS DATETIME2)"


@pytest.mark.parametrize("part", [
    "[hour, 1) + READ_TEXT('x') + DATEPART(hour]",
    "[hour, kf_datename) + 1 + DATEPART(hour]",
    "[x]",
    "fortnight",
])
def test_a_part_that_isnt_a_date_part_is_refused_and_never_becomes_sql(part: str) -> None:
    with pytest.raises(ValueError, match="isn't a date part T-SQL knows, so nothing ran"):
        translate(f"SELECT DATENAME({part}, {STAMP})")


@pytest.mark.parametrize("part", ["hour", "hh", "millisecond", "dw", "month", "iso_week", "ns"])
def test_known_parts_still_translate(part: str) -> None:
    statements, _ = translate(f"SELECT DATENAME({part}, {STAMP})")
    assert len(statements) == 1 and "READ_TEXT" not in statements[0].upper()
