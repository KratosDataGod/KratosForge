"""Mutation batch 266: delta_cdf.py, first score (docs/quality/mutation-testing.md)."""

import pyarrow as pa
import pytest

from fabric_desktop import delta_cdf


class _Meta:
    configuration: dict = {}


class _Failing:
    def metadata(self):
        return _Meta()

    def load_cdf(self, **_):
        raise RuntimeError("the log could not be read")


def test_any_other_delta_error_is_said_with_the_range_and_its_own_words():
    with pytest.raises(ValueError) as caught:
        delta_cdf.read(_Failing(), "sales", {"startingversion": 2})
    assert str(caught.value) == ("Nothing was read from the change data feed of sales from 2 to latest: "
                                 "the log could not be read")


def test_only_the_commit_timestamp_becomes_a_utc_timestamp():
    table = pa.table({
        "seen": pa.array([0], pa.timestamp("ns")),
        "_commit_timestamp": pa.array([0], pa.timestamp("ms")),
        "_commit_version": pa.array([1], pa.int32()),
    })
    out = delta_cdf._spark_types(table).schema
    assert out.field("seen").type == pa.timestamp("ns")
    assert out.field("_commit_timestamp").type == pa.timestamp("us", tz="UTC")
    assert out.field("_commit_version").type == pa.int64()
