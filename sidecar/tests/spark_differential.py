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

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "spark_3_5_3_differential.json"


@dataclass(frozen=True)
class Known:
    """A known difference: the cases whose SQL matches ``pattern`` may give ``kind`` ("wrong", "error", or
    "wrong/error" when the same gap shows both ways), and only when what this PC gave matches ``says`` (the
    error's text, or the value as text). So a new gap in an expression that merely contains the function
    still fails."""
    name: str
    kind: str
    pattern: str
    why: str
    says: str = ""


# Each entry names a gap found by this test. Fix it, then delete the entry: the test fails while an entry
# matches no case, so the list can't go stale.
KNOWN = [
    # Silent wrong answers.
    Known("substring-negative-length", "wrong", r"substr(ing)?\(.*, -\d+\)",
          "substring(s, pos, len) with len < 0 is '' in Spark; here it counts back from pos", says=r"."),
    Known("overlay-position-below-1", "wrong", r"overlay\(.* FROM (-\d+|0)\)",
          "overlay(... FROM pos) with pos < 1 keeps Spark's substring rules (overlay('abc' PLACING ',' FROM 0) "
          "is ',abc')", says=r"."),
    Known("levenshtein-non-ascii", "wrong", r"levenshtein\(.*'(ÄÖü|é)'",
          "levenshtein counts characters in Spark; here it counts UTF-8 bytes", says=r"^\d+$"),
    Known("locate-null-text", "wrong", r"locate\([^()]*, CAST\(NULL AS STRING\)",
          "locate(substr, NULL, pos < 1) is NULL in Spark; here it is 0", says=r"^0$"),
    Known("bit-count-negative-int", "wrong", r"bit_count\(",
          "bit_count of a negative int counts 64 bits in Spark (Long.bitCount); here 32", says=r"^(\d+|[\d-]+)$"),
    Known("bit-count-of-a-double", "error", r"bit_count\(ceil\(",
          "bit_count of a double stops here; Spark casts it to a whole number", says=r"bit_count\(DOUBLE\)"),
    Known("pmod-double-by-zero", "wrong", r"pmod\(.*0\.0D",
          "pmod(x, 0.0D) is NULL in Spark with ANSI off; here NaN", says=r"^nan$"),
    Known("div-of-a-decimal", "wrong", r"floor\([^()]*\) div",
          "a div b with a decimal is a whole number in Spark; here a double division", says=r"\."),
    Known("isnan-of-null", "wrong", r"isnan\(",
          "isnan(NULL) is false in Spark; here NULL", says=r"^None$"),
    Known("to-date-short-year", "wrong", r"to_date\((rtrim\(right|substring)\(",
          "to_date('24-02-29') is NULL in Spark (a year needs 4 digits); here the year 24", says=r"^0024-"),
    Known("date-format-past-9999", "wrong", r"add_months\(.*1000000",
          "date_format of a year past 9999 starts with + in Spark; here it doesn't", says=r"^\d{5,}"),
    # Loud: an error where Spark answers.
    Known("shift-java-rules", "wrong/error", r"shift(left|right)\(",
          "shiftleft and shiftright of a negative number, by a negative count or of a decimal stop or answer "
          "differently here; Spark shifts as Java does (the count taken mod 32 for an int)",
          says=r"shift|'<<\(|'>>\(|^-?\d+$"),
    Known("cast-long-text-to-date", "error", r"CAST\(repeat\(", "CAST of text past a date's range stops here; "
          "Spark gives NULL", says=r"date value out of range"),
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
    return next((k for k in KNOWN if kind in k.kind.split("/") and re.search(k.pattern, case_sql)
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
    for k in KNOWN:
        matching = [c for c in load() if re.search(k.pattern, c["sql"])]
        if not any(known_for(c["sql"], *outcome(c)) is k for c in matching):
            left.append(f"{k.name}: {len(matching)} matching cases, none gives '{k.kind}' now; delete the entry")
    return left
