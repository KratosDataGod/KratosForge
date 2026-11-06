"""to_char and to_number against Apache Spark 3.5.3's own answers (tests/fixtures/spark_3_5_3_*.json,
captured with a real PySpark 3.5.3): 1,807 to_char and 3,114 to_number cases. All of them matched when this
was written (about seven minutes). CI checks a fixed sample, every 15th answered case and every 50th refused
one, so it runs in about 20 seconds; set KF_SPARK_CONTRACT=all to check every case."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fabric_desktop import sql

FIXTURES = Path(__file__).with_name("fixtures")
ALL = os.environ.get("KF_SPARK_CONTRACT") == "all"
ANSWERED_STEP, ERROR_STEP = (1, 1) if ALL else (15, 50)


def _cases(name: str, failed: str) -> list[dict]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    errors = [c for c in data if c.get(failed)]
    return [c for c in data if not c.get(failed)][::ANSWERED_STEP] + errors[::ERROR_STEP]


def _run(expr: str):
    row = sql.run(f"SELECT {expr} AS x, typeof({expr}) AS t").to_pylist()[0]
    return row["x"], row["t"]


@pytest.mark.parametrize("case", _cases("spark_3_5_3_to_char.json", "error"), ids=lambda c: c["sql"])
def test_to_char_answers_as_spark_353(case):
    if case["error"]:
        with pytest.raises(Exception):  # noqa: B017  # Spark stops; any stop here matches
            _run(case["sql"])
    else:
        assert _run(case["sql"])[0] == case["spark"]


@pytest.mark.parametrize("case", _cases("spark_3_5_3_to_number.json", "error"), ids=lambda c: c["sql"])
def test_to_number_answers_as_spark_353(case):
    if "error" in case:
        with pytest.raises(Exception):  # noqa: B017  # Spark stops; any stop here matches
            _run(case["sql"])
        return
    value, kind = _run(case["sql"])
    assert (None if value is None else str(value)) == case["spark"]
    if case["type"]:
        assert kind == case["type"]
