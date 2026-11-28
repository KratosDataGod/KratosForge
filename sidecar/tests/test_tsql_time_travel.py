"""WR-222: time travel. Fabric Warehouse's OPTION (FOR TIMESTAMP AS OF '<UTC time>') at the end of a SELECT reads
every table as it was then, as Microsoft's time travel docs describe it. Locally each table is read from its Delta
history at that time. A table with no history, a time in the future and a time further back than a warehouse keeps
are refused by name, so a time travel read is never answered from the tables as they are now.
"""

from __future__ import annotations

import datetime as dt
import time

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import AS_OF_DEFAULT_DAYS, AS_OF_MOST_DAYS, translate

UTC = dt.timezone.utc


def iso(when: dt.datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def ago(**delta: float) -> str:
    return iso(dt.datetime.now(UTC) - dt.timedelta(**delta))


@pytest.fixture(scope="module")
def history(tmp_path_factory):
    """dbo.sales with two versions: (1, 10) at ``before``, then (2, 20) added after it; dbo.plain, a database table
    with no history; and the view dbo.v over dbo.sales."""
    local = LocalData(tmp_path_factory.mktemp("app") / "local-data")
    e = local.engine(local.create("Time travel")["id"])
    e.execute("CREATE TABLE dbo.sales USING DELTA AS SELECT 1 AS id, 10 AS amount", dialect="spark")
    time.sleep(1.1)
    before = iso(dt.datetime.now(UTC))
    time.sleep(1.1)
    e.execute("INSERT INTO dbo.sales VALUES (2, 20)", dialect="spark")
    time.sleep(1.1)
    after = iso(dt.datetime.now(UTC))
    e.execute("CREATE TABLE dbo.plain AS SELECT 1 AS id")
    e.execute("CREATE VIEW dbo.v AS SELECT id FROM dbo.sales")
    yield e, before, after
    local.close_all()


def rows(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


def test_a_select_reads_its_tables_as_they_were(history):
    e, before, after = history
    assert rows(e, "SELECT id, amount FROM dbo.sales ORDER BY id") == [[1, 10], [2, 20]]
    assert rows(e, f"SELECT id, amount FROM dbo.sales ORDER BY id OPTION (FOR TIMESTAMP AS OF '{before}')") == [[1, 10]]
    assert rows(e, f"SELECT id, amount FROM dbo.sales ORDER BY id OPTION (FOR TIMESTAMP AS OF '{after}')") == \
        [[1, 10], [2, 20]]


@pytest.mark.parametrize("sql, expected", [
    ("SELECT id FROM sales OPTION (FOR TIMESTAMP AS OF '{t}')", [[1]]),  # dbo by default
    ("SELECT a.id FROM dbo.sales AS a JOIN dbo.sales AS b ON a.id = b.id OPTION (FOR TIMESTAMP AS OF '{t}')", [[1]]),
    ("WITH c AS (SELECT id FROM dbo.sales) SELECT id FROM c OPTION (FOR TIMESTAMP AS OF '{t}')", [[1]]),
    ("SELECT id FROM dbo.sales WHERE id IN (SELECT id FROM dbo.sales) OPTION (FOR TIMESTAMP AS OF '{t}')", [[1]]),
    ("SELECT COUNT(*) FROM dbo.sales OPTION (FOR TIMESTAMP AS OF '{t}');", [[1]]),
    ("SELECT id FROM dbo.sales UNION ALL SELECT id FROM dbo.sales OPTION (FOR TIMESTAMP AS OF '{t}')", [[1], [1]]),
    ("select id from dbo.sales option (for timestamp as of '{t}')", [[1]]),
    ("SELECT id FROM dbo.sales\nOPTION ( FOR  TIMESTAMP AS OF '{t}' )", [[1]]),
])
def test_one_time_applies_to_every_table_the_select_reads(history, sql, expected):
    e, before, _ = history
    assert rows(e, sql.format(t=before)) == expected


def test_milliseconds_are_read_as_written(history):
    e, before, _ = history
    assert rows(e, f"SELECT id FROM dbo.sales OPTION (FOR TIMESTAMP AS OF '{before}.999')") == [[1]]


@pytest.mark.parametrize("table", ["dbo.plain", "dbo.v"])
def test_a_table_or_view_with_no_delta_history_is_refused_not_read_as_it_is(history, table):
    e, before, _ = history
    with pytest.raises(Exception, match="isn't a Delta table") as err:
        e.execute(f"SELECT id FROM {table} OPTION (FOR TIMESTAMP AS OF '{before}')", dialect="tsql")
    assert "no versions" in str(err.value)


def test_a_time_before_the_table_existed_is_refused(history):
    e, *_ = history
    with pytest.raises(Exception, match="has no version as of"):
        e.execute(f"SELECT id FROM dbo.sales OPTION (FOR TIMESTAMP AS OF '{ago(days=2)}')", dialect="tsql")


def test_a_temp_table_is_read_as_it_is_as_in_a_warehouse(history):
    e, before, _ = history
    sql = f"CREATE TABLE #t (id INT);\nINSERT INTO #t VALUES (1)\nGO\nSELECT s.id FROM dbo.sales AS s JOIN #t ON " \
          f"#t.id = s.id OPTION (FOR TIMESTAMP AS OF '{before}')"
    assert rows(e, sql) == [[1]]


def test_a_time_later_than_now_is_refused():
    with pytest.raises(ValueError, match="is later than now") as err:
        translate(f"SELECT id FROM dbo.sales OPTION (FOR TIMESTAMP AS OF '{ago(days=-1)}')")
    assert "rather than read as the tables are now" in str(err.value)


def test_a_time_further_back_than_a_warehouse_keeps_is_refused():
    with pytest.raises(ValueError, match=f"more than {AS_OF_MOST_DAYS} days ago"):
        translate(f"SELECT id FROM dbo.sales OPTION (FOR TIMESTAMP AS OF '{ago(days=AS_OF_MOST_DAYS + 1)}')")


def test_a_time_past_the_default_retention_runs_with_a_note():
    _, notes = translate(f"SELECT id FROM dbo.sales OPTION (FOR TIMESTAMP AS OF '{ago(days=AS_OF_DEFAULT_DAYS + 1)}')")
    assert any(f"more than {AS_OF_DEFAULT_DAYS} days ago" in n and "doesn't answer" in n for n in notes)
    _, notes = translate(f"SELECT id FROM dbo.sales OPTION (FOR TIMESTAMP AS OF '{ago(days=1)}')")
    assert not any("days ago" in n for n in notes)


def test_the_translation_reads_each_table_at_the_time_in_utc():
    texts, _ = translate("SELECT a.id FROM dbo.sales AS a JOIN dbo.orders AS b ON a.id = b.id "
                         f"OPTION (FOR TIMESTAMP AS OF '{ago(hours=1)}')")
    assert texts[0].count("AT (TIMESTAMP => '") == 2 and "+00:00'" in texts[0]
    assert "OPTION" not in texts[0].upper()


@pytest.mark.parametrize("written", [
    "2026-11-01 09:30:00", "2026-11-01T09:30:00.1234", "2026-11-01", "2026-11-01T09:30:00Z",
    "2026-11-01T09:30:00+01:00", "yesterday", "2026-02-30T00:00:00", "2026-11-01T24:00:00",
])
def test_a_time_not_written_as_tsql_wants_is_refused_in_its_words(written):
    with pytest.raises(ValueError, match=r"An error occurred during timestamp conversion\. Please provide a timestamp "
                                         r"in the format yyyy-MM-ddTHH:mm:ss\[\.fff\]\."):
        translate(f"SELECT id FROM dbo.sales OPTION (FOR TIMESTAMP AS OF '{written}')")


@pytest.mark.parametrize("sql, words", [
    ("SELECT id FROM dbo.sales OPTION (FOR TIMESTAMP AS OF @t)", "A warehouse doesn't take a variable"),
    ("SELECT id FROM dbo.sales OPTION (MAXDOP 1, FOR TIMESTAMP AS OF '{t}')", "nothing else in the OPTION"),
    ("SELECT id FROM dbo.sales OPTION (FOR TIMESTAMP AS OF '{t}', MAXDOP 1)", "nothing else in the OPTION"),
    ("INSERT INTO dbo.plain SELECT id FROM dbo.sales OPTION (FOR TIMESTAMP AS OF '{t}')", "INSERT ... SELECT"),
    ("SELECT id INTO dbo.copy FROM dbo.sales OPTION (FOR TIMESTAMP AS OF '{t}')", "SELECT ... INTO"),
    ("CREATE TABLE dbo.c AS SELECT id FROM dbo.sales OPTION (FOR TIMESTAMP AS OF '{t}')", "CREATE TABLE AS"),
    ("SELECT 1 AS a;\nSELECT id FROM dbo.sales OPTION (FOR TIMESTAMP AS OF '{t}')", "Put GO before and after"),
    (("SELECT id FROM dbo.sales OPTION (FOR TIMESTAMP AS OF '{t}')\nSELECT id FROM dbo.sales OPTION (FOR TIMESTAMP "
      "AS OF '{t}')"), "more than once"),
])
def test_what_isnt_mapped_is_refused_by_name(sql, words):
    with pytest.raises(ValueError) as err:
        translate(sql.format(t=ago(hours=1)))
    assert words in str(err.value)
    assert "Could not read" not in str(err.value)


@pytest.mark.parametrize("sql", [
    "SELECT 'OPTION (FOR TIMESTAMP AS OF ''x'')' AS a",
    "-- OPTION (FOR TIMESTAMP AS OF '2026-11-01T09:30:00')\nSELECT 1 AS a",
    "SELECT [timestamp] FROM dbo.t",
])
def test_the_words_in_text_comments_or_names_are_left_alone(sql):
    texts, _ = translate(sql)
    assert "AT (TIMESTAMP" not in texts[0]


def test_another_option_is_still_refused_by_name():
    with pytest.raises(ValueError, match="local engine"):
        translate("SELECT 1 OPTION (MAXDOP 1)")
