"""Differential test, first half: generated Spark SQL expressions answer here as Spark 3.5.3 answers them
(tests/spark_differential.py says how; tools/capture_spark_differential.py makes the cases)."""

from __future__ import annotations

from tests import spark_differential as diff


def test_first_half_answers_as_spark() -> None:
    diff.check(diff.part(0, 2))


def test_every_known_difference_still_happens() -> None:
    """A fixed gap must leave KNOWN, so the list says only what still differs."""
    assert not diff.stale()


def test_the_fixture_is_large_and_cites_spark_3_5() -> None:
    cases = diff.load()
    assert len(cases) >= 1500
    assert all(k.kind in ("wrong", "error", "wrong/error") and k.why and k.says for k in diff.KNOWN)
    assert len({k.name for k in diff.KNOWN}) == len(diff.KNOWN)
