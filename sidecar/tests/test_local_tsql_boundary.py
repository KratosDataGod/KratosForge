"""T-SQL on this PC at its exact edges (boundary re-score of batch 3, mutation batch 155,
docs/quality/mutation-testing.md): time travel's limits, decimal results at 38 digits, LIKE classes, and
statements that end right after the word a check looks past."""

from __future__ import annotations

import datetime as dt

import pytest
import sqlglot

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata import tsql as T

NOW = dt.datetime(2026, 11, 18, 12, tzinfo=dt.timezone.utc)


@pytest.fixture
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    return local.engine(local.create("TSQL")["id"])


def one(engine, sql):
    return engine.execute(sql, dialect="tsql")["rows"][0]


def _as_of(when: dt.datetime):
    tree = sqlglot.parse_one("SELECT * FROM t", read=T.KfTSQL)
    tree.meta[T._AS_OF_META] = when.strftime("%Y-%m-%dT%H:%M:%S")
    return T._as_of(tree, NOW)


def test_one_time_travel_hint_is_taken():
    batch, when = T._take_as_of("SELECT * FROM t OPTION (FOR TIMESTAMP AS OF '2026-11-01T09:30:00')")
    assert when == "2026-11-01T09:30:00" and "OPTION" not in batch


def test_a_hint_cut_off_after_its_time_is_refused_by_name():
    with pytest.raises(ValueError, match="takes a time written out in quotes"):
        T._take_as_of("SELECT 1 OPTION (FOR TIMESTAMP AS OF '2026-11-01T09:30:00'")


def test_time_travel_to_exactly_now_is_read():
    assert _as_of(NOW) == []


def test_time_travel_exactly_the_most_days_back_is_read_with_a_note():
    [note] = _as_of(NOW - dt.timedelta(days=T.AS_OF_MOST_DAYS))
    assert f"more than {T.AS_OF_DEFAULT_DAYS} days ago" in note
    with pytest.raises(ValueError, match="further back"):
        _as_of(NOW - dt.timedelta(days=T.AS_OF_MOST_DAYS, seconds=1))


def test_time_travel_exactly_the_default_days_back_has_no_note():
    assert _as_of(NOW - dt.timedelta(days=T.AS_OF_DEFAULT_DAYS)) == []
    assert len(_as_of(NOW - dt.timedelta(days=T.AS_OF_DEFAULT_DAYS, seconds=1))) == 1


def test_a_product_whose_scales_add_up_to_exactly_38_is_exact_when_small(engine):
    assert one(engine, "SELECT CAST(0.01 AS DECIMAL(20,19)) * CAST(0.01 AS DECIMAL(20,19)) AS p") == \
        ["0.00010000000000000000000000000000000"]


def test_a_sum_that_keeps_its_scale_is_exact_at_38_digits(engine):
    # 36 whole digits: too big for the floating point check, so exact only when the scale isn't cut.
    assert one(engine, "SELECT CAST(123456789012345678901234567890123456.78 AS DECIMAL(38,2)) "
                       "+ CAST(1 AS DECIMAL(38,2)) AS s") == ["123456789012345678901234567890123457.78"]


def test_a_decimal_with_only_a_precision_has_scale_0(engine):
    assert one(engine, "SELECT CAST(7 AS DECIMAL(10)) / 2 AS q") == ["3.500000"]


def test_a_whole_number_of_exactly_2_to_the_31_is_a_decimal(engine):
    assert one(engine, "SELECT 2147483648 / 2 AS q") == ["1073741824.000000"]
    assert one(engine, "SELECT 2147483647 / 2 AS q") == [1073741823]


def test_a_time_with_no_fraction_digits_is_shown_without_a_point(engine):
    assert one(engine, "SELECT CAST(CAST('12:34:56' AS TIME(0)) AS VARCHAR(20)) AS t") == ["12:34:56"]


def test_like_classes_at_their_edges(engine):
    def like(text, pattern, escape=""):
        esc = f" ESCAPE '{escape}'" if escape else ""
        return one(engine, f"SELECT CASE WHEN '{text}' LIKE '{pattern}'{esc} THEN 1 ELSE 0 END AS m")[0]

    assert like("^", "[^]") == 1 and like("a", "[^]") == 0  # a lone ^ is the character, not "none of"
    assert like("-", "[a-]") == 1 and like("a", "[a-]") == 1  # a - at the end is the character
    assert like("a", "[a-a]") == 1
    assert like("a", "[a!", "!") == 0  # an escape at the very end of an unclosed class
    assert like("ab", "a[b]!", "!") == 0  # an escape at the very end of a pattern with a class


def test_choose_with_one_choice(engine):
    assert one(engine, "SELECT CHOOSE(1, 'a') AS c") == ["a"]


def test_timefromparts_precision_and_fractions_at_their_edges(engine):
    assert one(engine, "SELECT TIMEFROMPARTS(1, 2, 3, 10, 7) AS t") == ["01:02:03.000001"]
    assert one(engine, "SELECT TIMEFROMPARTS(1, 2, 3, 9, 1) AS t") == ["01:02:03.900000"]
    with pytest.raises(ValueError, match=r"fractions \(10\) don't fit a precision of 1"):
        one(engine, "SELECT TIMEFROMPARTS(1, 2, 3, 10, 1) AS t")


def test_str_takes_its_length_and_decimals_at_both_ends(engine):
    short, long_, none, most = one(engine, "SELECT STR(1.5, 1) AS a, STR(1.5, 8000) AS b, STR(1.25, 5, 0) AS c, "
                                           "STR(1.25, 20, 16) AS d")
    assert short == "2" and len(long_) == 8000 and long_.strip() == "2"
    assert none == "    1" and most == "  1.2500000000000000"


def test_isjson_with_one_and_with_two_arguments(engine):
    assert one(engine, "SELECT ISJSON('{}') AS a, ISJSON('[]', ARRAY) AS b") == [1, 1]


def test_replicate_a_count_that_is_0_toward_zero(engine):
    assert one(engine, "SELECT REPLICATE('ab', 0.5) AS r, REPLICATE('ab', 1.5) AS r2") == ["", "ab"]


@pytest.mark.parametrize("sql, message", [
    ("SET STATISTICS", "SET STATISTICS isn't mapped"),
    ("SET NOCOUNT2", "SET NOCOUNT2 isn't mapped"),
    ("UPDATE t SET n +", "isn't mapped"),
    ("CREATE", "CREATE"),
    ("SELECT 1 FOR JSON", "FOR JSON isn't mapped"),
    ("COPY INTO dbo.t FROM", "COPY INTO isn't mapped"),
    ("SELECT 1 copy INTO t FROM", "Could not read the query as T-SQL"),  # copy as an alias: not COPY INTO
])
def test_a_statement_that_ends_right_after_a_checked_word_is_refused_by_name(engine, sql, message):
    with pytest.raises(ValueError, match=message):
        engine.execute(sql, dialect="tsql")


def test_create_schema_with_nothing_after_its_name_runs(engine):
    engine.execute("CREATE SCHEMA [Authorization]", dialect="tsql")  # the word, as the schema's name
    engine.execute("CREATE SCHEMA s", dialect="tsql")
    engine.execute("CREATE TABLE s.t AS SELECT 1 AS n", dialect="tsql")
    assert one(engine, "SELECT n FROM s.t") == [1]


def test_delete_with_one_named_target_runs(engine):
    engine.execute("CREATE TABLE dbo.x AS SELECT 1 AS n UNION ALL SELECT 2", dialect="tsql")
    assert one(engine, "DELETE o FROM dbo.x o WHERE o.n = 1") == [1]
    assert engine.execute("SELECT n FROM dbo.x", dialect="tsql")["rows"] == [[2]]
