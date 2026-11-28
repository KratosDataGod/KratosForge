"""Spark 3.5.3's answers to hand-written queries, asked again of this PC's Spark session.

tests/fixtures/spark_3_5_3_<name>.json is made by tools/capture_spark_values.py from
tools/spark_value_cases/<name>.txt, by hand, since CI has no Spark. Each case runs here through a notebook's
spark.sql, as a Fabric notebook cell would, and its rows are compared as spark_coverage.rows_value gives
them (decimals by their digits, doubles to 12 significant digits, rows and maps as name/value pairs).

Each case gives Spark's rows, or Spark's error as an error here too, or is refused here by name: an error
that says nothing ran, matched by a Refused entry that docs/sql-compatibility.md lists. A different answer
is never allowed, and neither is DuckDB's own error. A Refused entry no case needs any more fails.

Not a test module: pytest collects test_*.py only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from fabric_desktop import spark_coverage

HERE = Path(__file__).resolve().parent
DOCS = HERE.parents[1] / "docs" / "sql-compatibility.md"


@dataclass(frozen=True)
class Refused:
    """Cases whose SQL (after "@tz <zone> " for a case run in another session time zone) matches
    ``pattern`` may be refused here, when the refusal's text matches ``says``;
    ``doc`` is the text docs/sql-compatibility.md lists it under."""
    name: str
    pattern: str
    says: str
    doc: str


def load(name: str) -> list[dict[str, Any]]:
    data = json.loads((HERE / "fixtures" / f"spark_3_5_3_{name}.json").read_text(encoding="utf-8"))
    assert data["spark"].startswith("3.5") and data["ansi"] is False and data["time_zone"] == "UTC"
    return list(data["cases"])


def refusal(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "nothing ran" in text or "this pc" in text


def outcome(spark: Any, case: dict[str, Any]) -> tuple[str, str]:
    """("same" | "wrong" | "error" | "refused" | "stopped", what this PC gave). "stopped": an error here where
    Spark errors too."""
    try:
        if "tz" in case:  # the session time zone Spark answered in (UTC when there is none)
            spark.conf.set("spark.sql.session.timeZone", case["tz"])
        got = spark_coverage.rows_value(spark.sql(case["sql"]).collect())
    except Exception as exc:  # noqa: BLE001 - silent-ok: every error is an outcome this test sorts
        first = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
        if "error" in case:
            return "stopped", first
        return ("refused" if refusal(exc) else "error"), first
    finally:
        if "tz" in case:
            spark.conf.unset("spark.sql.session.timeZone")
    if "error" in case:
        return "wrong", f"{json.dumps(got)} where Spark stops: {case['error']}"
    return ("same" if got == case["value"] else "wrong"), json.dumps(got, ensure_ascii=False)


def _sort(spark: Any, cases: list[dict[str, Any]], refused: list[Refused]) -> tuple[list[str], set[str]]:
    """(the cases that neither answer as Spark nor are refused by a listed entry, the entries used)."""
    bad, used = [], set()
    for case in cases:
        kind, got = outcome(spark, case)
        if kind in ("same", "stopped"):
            continue
        written = _written(case)
        entry = next((r for r in refused if kind == "refused" and re.search(r.pattern, written)
                      and re.search(r.says, got)), None)
        if entry is None:
            want = case.get("error") or json.dumps(case.get("value"), ensure_ascii=False)
            bad.append(f"{kind}: {case['sql']}\n    here:  {got[:300]}\n    Spark: {want[:300]}")
        else:
            used.add(entry.name)
    return bad, used


def _written(case: dict[str, Any]) -> str:
    return (f"@tz {case['tz']} " if "tz" in case else "") + case["sql"]  # a pattern can name the zone


def check(spark: Any, cases: list[dict[str, Any]], refused: list[Refused]) -> None:
    """Every case answers as Spark does or is refused by a listed entry; every entry is still needed and in the
    docs."""
    bad, used = _sort(spark, cases, refused)
    assert not bad, f"{len(bad)} of {len(cases)} cases don't answer as Spark 3.5.3:\n" + "\n".join(bad)
    _needed(refused, used)


def parts(name: str, size: int = 8) -> list[Any]:
    """The cases of ``name`` as pytest params of ``size`` cases each. One test of every case ran over the
    per-test time limit on a busy runner (84 hashing cases, some 5 s each), so each part is a test of its own."""
    cases = load(name)
    return [pytest.param(cases[i:i + size], id=f"{name}-{i + 1}-{min(i + size, len(cases))}")
            for i in range(0, len(cases), size)]


def check_part(spark: Any, cases: list[dict[str, Any]], refused: list[Refused]) -> None:
    """``check`` for one of ``parts``: every case answers as Spark does or is refused by a listed entry.
    Whether each entry is still needed is ``check_still_needed``'s, since that needs every case."""
    bad, _ = _sort(spark, cases, refused)
    assert not bad, f"{len(bad)} of {len(cases)} cases don't answer as Spark 3.5.3:\n" + "\n".join(bad)


def check_still_needed(spark: Any, name: str, refused: list[Refused]) -> None:
    """Every entry still refuses a case of ``name`` and is in the docs. Only the cases an entry's pattern
    matches are run: another case can't use an entry."""
    cases = [c for c in load(name) if any(re.search(r.pattern, _written(c)) for r in refused)]
    _, used = _sort(spark, cases, refused)
    _needed(refused, used)


def _needed(refused: list[Refused], used: set[str]) -> None:
    docs = DOCS.read_text(encoding="utf-8")
    assert not [r.name for r in refused if r.name not in used], "Refused entries no case needs any more"
    assert not [r.name for r in refused if r.doc not in docs], "Refused entries docs/sql-compatibility.md doesn't list"
