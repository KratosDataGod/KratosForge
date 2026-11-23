"""delta_cdf.py: what mutation testing showed no test checked. Only _commit_timestamp becomes a UTC
microsecond timestamp; the table's own timestamp columns keep their type."""

from __future__ import annotations

from datetime import datetime

import pyarrow as pa

from fabric_desktop import delta_cdf


def test_only_the_commit_timestamp_becomes_utc_microseconds():
    when = datetime(2026, 1, 1, 12)
    table = pa.table({
        "name": pa.array(["a"], pa.string_view()),
        "local_ts": pa.array([when], pa.timestamp("us")),
        "_change_type": pa.array(["insert"], pa.string_view()),
        "_commit_version": pa.array([3], pa.int32()),
        "_commit_timestamp": pa.array([when], pa.timestamp("ms")),
    })
    out = delta_cdf._spark_types(table).schema
    assert out.field("name").type == pa.string() and out.field("_change_type").type == pa.string()
    assert out.field("local_ts").type == pa.timestamp("us")  # no zone added
    assert out.field("_commit_version").type == pa.int64()
    assert out.field("_commit_timestamp").type == pa.timestamp("us", tz="UTC")
