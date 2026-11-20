"""Differential testing of the local Spark SQL translator against real Spark 3.5.3.

tests/fixtures/spark_3_5_3_differential.json holds generated scalar expressions and the answer Apache Spark
3.5.3 gave each one (local mode, UTC, ANSI mode off, as Fabric runs Spark SQL). It is made by
tools/capture_spark_differential.py, by hand, since CI has no Spark. Each case is asked again here through
sql.run, the local Spark SQL path, and must give the same answer.

An answer that differs is a silent wrong answer, and an error is a loud one. Each must be named in KNOWN, with
the function it comes from, until it is fixed (and its KNOWN entry removed) or refused by name. A difference
or error that matches no KNOWN entry fails, and so does a KNOWN entry that no case matches any more.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from fabric_desktop import sql
from fabric_desktop.duckdb_rows import duckdb_major

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "spark_3_5_3_differential.json"


@dataclass(frozen=True)
class Known:
    """A known difference: the cases whose SQL matches ``pattern`` may give ``kind`` ("wrong", "error", or
    "wrong/error" when the same gap shows both ways), and only when what this PC gave matches ``says`` (the
    error's text, or the value as text). So a new gap in an expression that merely contains the function
    still fails. ``duckdb`` names the one DuckDB major the gap shows on (0: every one); CI also runs the
    suite on the next major, where DuckDB itself can answer a case differently."""
    name: str
    kind: str
    pattern: str
    why: str
    says: str = ""
    duckdb: int = 0

    def applies(self) -> bool:
        return self.duckdb in (0, duckdb_major())


# Each entry names a gap found by this test. Fix it, then delete the entry: the test fails while an entry
# matches no case, so the list can't go stale.
KNOWN = [
    # Silent wrong answers: none left.
    # Loud: an error where Spark answers.
    Known("date-add-past-timestamp-range", "error", r"date_add\(.*shiftleft\(",
          "date_add by about 2^31 days goes past DuckDB's timestamp range and stops here; Spark gives a date "
          "millions of years away", says=r"Date and time not in timestamp range"),
    # Refused by name on purpose: split_part's index 0 is an error in Spark too, but Spark raises it only when
    # the call is reached, and this PC refuses the whole query up front.
    Known("split-part-index-0-refused", "error", r"split_part\(.*, 0\)",
          "refused by name before the query runs, which Spark does only when the call is reached",
          says=r"split_part's index can't be 0"),
]


def load() -> list[dict]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["spark"].startswith("3.5") and data["ansi"] is False and data["time_zone"] == "UTC"
    return [c for c in data["cases"] if "error" not in c]  # a query Spark itself refuses isn't compared


def decode(value: object) -> object:
    if isinstance(value, dict):
        (kind, text), = value.items()
        return {"decimal": Decimal, "date": dt.date.fromisoformat, "timestamp": dt.datetime.fromisoformat,
                "double": float}[kind](text)
    return value


def same(want: object, got: object) -> bool:
    if isinstance(got, dt.datetime) and got.tzinfo is not None:
        got = got.astimezone(dt.timezone.utc).replace(tzinfo=None)
    if want is None or got is None:
        return want is None and got is None
    if isinstance(want, bool) or isinstance(got, bool):
        return type(want) is type(got) and want == got
    if isinstance(want, float) or isinstance(got, float):
        a, b = float(want), float(got)  # type: ignore[arg-type]
        if math.isnan(a) or math.isnan(b):
            return math.isnan(a) and math.isnan(b)
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(want, Decimal) or isinstance(got, Decimal):
        return isinstance(got, (int, Decimal)) and Decimal(got) == Decimal(want)  # type: ignore[arg-type]
    return type(want) is type(got) and want == got


def outcome(case: dict) -> tuple[str, object]:
    """("same" | "wrong" | "error", what this PC gave)."""
    try:
        row = sql.run("SELECT " + case["sql"]).to_pylist()[0]
    except Exception as exc:  # noqa: BLE001 - silent-ok: any error is the loud outcome this test counts
        return "error", f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
    got = next(iter(row.values()))
    return ("same" if same(decode(case["spark"]), got) else "wrong"), got


def known_for(case_sql: str, kind: str, got: object) -> Known | None:
    said = got if isinstance(got, str) and kind == "error" else str(got)
    return next((k for k in KNOWN if k.applies() and kind in k.kind.split("/") and re.search(k.pattern, case_sql)
                 and re.search(k.says, said)), None)


def check(cases: list[dict]) -> set[str]:
    """Assert every case gives Spark's answer or a KNOWN difference; return the KNOWN names that matched."""
    unexplained, used = [], set()
    for case in cases:
        kind, got = outcome(case)
        if kind == "same":
            continue
        k = known_for(case["sql"], kind, got)
        if k is None:
            unexplained.append(f"{kind}: SELECT {case['sql']}  Spark: {decode(case['spark'])!r}  here: {got!r}")
        else:
            used.add(k.name)
    assert not unexplained, f"{len(unexplained)} answers differ from Spark 3.5.3:\n" + "\n".join(unexplained)
    return used


def part(index: int, parts: int) -> list[dict]:
    return load()[index::parts]


def stale() -> list[str]:
    """The KNOWN entries no case needs any more: each matching case now answers as Spark does."""
    left = []
    for k in filter(Known.applies, KNOWN):
        matching = [c for c in load() if re.search(k.pattern, c["sql"])]
        if not any(known_for(c["sql"], *outcome(c)) is k for c in matching):
            left.append(f"{k.name}: {len(matching)} matching cases, none gives '{k.kind}' now; delete the entry")
    return left
