"""spark_df_extras paths that mutation testing (batch 10, docs/quality/mutation-testing.md) showed no test
checked: semanticHash stays a signed 32-bit int for any plan, and installing the additions again doesn't
wrap cache a second time."""

from __future__ import annotations

import pytest
from duckdb.experimental.spark.sql.dataframe import DataFrame

from fabric_desktop import spark_df_extras as ex
from fabric_desktop.notebook import Kernel


@pytest.fixture(autouse=True)
def no_lakehouse(monkeypatch):
    from fabric_desktop import lakehouse
    monkeypatch.setattr(lakehouse, "_attached", None)


def test_semantic_hash_is_a_signed_32_bit_int_for_every_plan():
    # Several plans, so both halves of the unsigned CRC (below and above 2**31) are converted.
    out = Kernel().run(
        "hs = [spark.range(n).semanticHash() for n in range(1, 13)]\n"
        "hs += [spark.range(3).selectExpr(f'id + {n} AS x').semanticHash() for n in range(12)]\n"
        "print(all(-2**31 <= h < 2**31 for h in hs), any(h >= 0 for h in hs), any(h < 0 for h in hs))")
    assert out["status"] == "ok", out
    assert "".join(o.get("text", "") for o in out["outputs"]).strip() == "True True True"


def test_installing_again_keeps_one_cache_wrapper():
    ex.install()
    once = DataFrame.cache
    ex.install()
    assert DataFrame.cache is once
