"""semanticHash at its boundary (mutation batch 209, a boundary re-score of batch 10,
docs/quality/mutation-testing.md): a CRC of exactly 2**31 is the most negative signed 32-bit int."""

from __future__ import annotations

import pytest

from fabric_desktop import spark_df_extras as ex


@pytest.mark.parametrize("crc, signed", [(2**31 - 1, 2**31 - 1), (2**31, -(2**31)), (2**32 - 1, -1)])
def test_the_hash_is_a_signed_32_bit_int(monkeypatch, crc, signed):
    monkeypatch.setattr(ex, "_canonical_plan", lambda df: "plan")
    monkeypatch.setattr(ex.zlib, "crc32", lambda data: crc)
    assert ex.semanticHash(object()) == signed
