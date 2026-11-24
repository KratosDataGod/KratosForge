"""SFN-031 (WR-257): a Snowflake regular expression with a non-greedy quantifier or a backreference, which
Snowflake's documentation says it doesn't support, or a lookaround, which RE2 can't read, is refused by name
with the step to take; and the pattern scan stays linear on a long run of unclosed classes."""

from __future__ import annotations

import time

import pytest

from fabric_desktop import snowflake_sql as sf
from fabric_desktop.spark_strings import java_pattern


@pytest.mark.parametrize(("pattern", "said"), [
    ("a.*?b", ("uses a non-greedy quantifier ('*?', '+?', '??'), which Snowflake's regular expressions don't support, "
              "so nothing ran. Use a narrower class, such as [^,]* in place of .*?.")),
    ("a+?", "non-greedy quantifier"),
    ("a{2}?", "non-greedy quantifier"),
    ("(a)\\\\1", ("uses a backreference ('\\1'), which Snowflake's regular expressions don't support (a replacement "
                 "can use one), so nothing ran. Rewrite the pattern without it.")),
    ("a(?=b)", ("uses a lookahead or lookbehind ('(?=', '(?!', '(?<=', '(?<!'), which RE2, the engine that runs it "
               "here, can't read, so nothing ran. Rewrite the pattern without it.")),
    ("(?<!a)b", "lookahead or lookbehind"),
])
def test_an_unsupported_pattern_is_refused_by_name(pattern, said):
    report = sf.translate(f"SELECT REGEXP_LIKE(x, '{pattern}') FROM t").statements[0]
    assert report.verdict == "not_translatable"
    assert [n.id for n in report.notes] == ["SFN-031"]
    assert said in report.notes[0].reason


@pytest.mark.parametrize("pattern", ["a*b?", "[?*]+", "a\\\\?", "[[:alpha:]]+", "a{2,3}"])
def test_a_supported_pattern_is_not_refused(pattern):
    report = sf.translate(f"SELECT REGEXP_LIKE(x, '{pattern}') FROM t").statements[0]
    assert "SFN-031" not in [n.id for n in report.notes]


def test_a_long_pattern_of_unclosed_classes_reads_in_linear_time():
    """'[' then '[:' over and over, with no ':]' to close any: each one was searched for to the end of the pattern,
    so 200 KB took seconds to translate. It is read once now. Spark's path refuses it before its own scan."""
    pattern = "[" + "[:" * 100_000
    started = time.perf_counter()
    sf.translate(f"SELECT REGEXP_LIKE(x, '{pattern}') FROM t")
    assert sf._unsupported_regex(pattern) is None
    with pytest.raises(ValueError, match="class inside a class"):
        java_pattern(pattern, "regexp_replace")
    assert time.perf_counter() - started < 2.0
