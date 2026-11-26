"""Spark's sha2 at each bit length DuckDB has no function for, checked here without reading docs/: the
case batch 232 (docs/quality/mutation-testing.md) found unchecked in a copy of sidecar/ on its own."""

from __future__ import annotations

import pytest

from fabric_desktop import spark_hash

ABC = {  # FIPS 180 test vectors for "abc"
    224: "23097d223405d8228642a477bda255b32aadbce4bda0b3f7e36c9da7",  # pragma: allowlist secret (test value)
    384: "cb00753f45a35e8bb5a03d699ac65007272c32ab0eded1631a8b605a43ff5bed8086072ba1e7cc2358baeca134c825a7",  # pragma: allowlist secret (test value)
    512: "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",  # noqa: E501  # pragma: allowlist secret (test value)
}


@pytest.mark.parametrize("bits", sorted(ABC))
def test_sha2_gives_the_digest_at_each_bit_length_duckdb_lacks(bits):
    assert spark_hash.sha2(b"abc", bits) == ABC[bits]


@pytest.mark.parametrize("bits", [223, 225, 383, 385, 511, 513])
def test_sha2_at_a_bit_length_next_to_one_spark_has_is_null(bits):
    assert spark_hash.sha2(b"abc", bits) is None
