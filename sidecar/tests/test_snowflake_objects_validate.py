"""Snowflake tasks, streams and dynamic tables checked on this PC (SF-008, snowflake_objects_validate.py).

Good statements of each kind pass, each documented rule has an error or a warning that names it, task
graphs are checked across statements, and every message is a plain sentence with a next step. The
rules come from Snowflake's CREATE TASK, CREATE STREAM and CREATE DYNAMIC TABLE references."""

from __future__ import annotations

import pytest

from fabric_desktop import snowflake_objects_validate as sov
from fabric_desktop.snowflake_objects_validate import ERROR, LABEL, WARNING, validate


def one(sql: str) -> sov.Result:
    results = validate(sql)
    assert len(results) == 1, [r.text for r in results]
    return results[0]


def errors(result: sov.Result) -> list[str]:
    return [f.message for f in result.findings if f.severity == ERROR]


def warnings(result: sov.Result) -> list[str]:
    return [f.message for f in result.findings if f.severity == WARNING]


def record(results: list[sov.Result]) -> list[sov.Result]:
    return results


# ---------------------------------------------------------------------------- good statements

GOOD = [
    ("CREATE OR REPLACE TASK db.sch.load WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS INSERT INTO t SELECT * FROM s",
     "task", "db.sch.load"),
    (("CREATE TASK nightly USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE = 'XSMALL' "
     "SCHEDULE = 'USING CRON 0 2 * * MON-FRI America/Los_Angeles' CONFIG = $${\"env\": \"dev\"}$$ "
     "COMMENT = 'nightly load' AS CALL load_all(1)"), "task", "nightly"),
    ("CREATE TASK every_ten WAREHOUSE = wh SCHEDULE = '10 SECONDS' AS DELETE FROM t WHERE x < 0", "task", "every_ten"),
    (("CREATE TASK hourly WAREHOUSE = wh SCHEDULE = '1 HOUR' TASK_AUTO_RETRY_ATTEMPTS = 3 "
     "USER_TASK_TIMEOUT_MS = 60000 AS MERGE INTO a USING b ON a.id = b.id WHEN MATCHED THEN DELETE"), "task", "hourly"),
    (("CREATE TASK triggered WAREHOUSE = wh WHEN SYSTEM$STREAM_HAS_DATA('orders_stream') "
     "AS INSERT INTO t SELECT * FROM orders_stream"), "task", "triggered"),
    (("CREATE TASK serverless_trig TARGET_COMPLETION_INTERVAL = '5 MINUTES' "
     "WHEN SYSTEM$STREAM_HAS_DATA('s') AS INSERT INTO t SELECT * FROM s"), "task", "serverless_trig"),
    ('CREATE TASK IF NOT EXISTS "My Task" WAREHOUSE = "My WH" SCHEDULE = \'192 HOURS\' AS SELECT 1', "task", '"My Task"'),
    ("CREATE STREAM s ON TABLE t", "stream", "s"),
    (("CREATE OR REPLACE STREAM s ON TABLE db.sch.t AT(OFFSET => -300) APPEND_ONLY = TRUE "
     "SHOW_INITIAL_ROWS = FALSE COMMENT = 'x'"), "stream", "s"),
    ("CREATE STREAM s ON VIEW v BEFORE(STATEMENT => '8e5d0ca9-005e-44e6-b858-a8f5b37c5726')", "stream", "s"),
    ("CREATE STREAM s ON EXTERNAL TABLE ext INSERT_ONLY = TRUE", "stream", "s"),
    ("CREATE STREAM s ON STAGE my_stage", "stream", "s"),
    ("CREATE STREAM s ON DYNAMIC TABLE d", "stream", "s"),
    ("CREATE STREAM s COPY GRANTS ON EVENT TABLE e", "stream", "s"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' WAREHOUSE = wh AS SELECT a, b FROM t", "dynamic_table", "d"),
    (("CREATE OR REPLACE TRANSIENT DYNAMIC TABLE d (a, b COMMENT 'x') TARGET_LAG = DOWNSTREAM WAREHOUSE = wh "
     "REFRESH_MODE = INCREMENTAL INITIALIZE = ON_SCHEDULE COMMENT = 'c' AS SELECT a, b FROM t"), "dynamic_table", "d"),
    (("CREATE DYNAMIC TABLE d TARGET_LAG = '2 hours' WAREHOUSE = wh REFRESH_MODE = FULL CLUSTER BY (a) "
     "AS SELECT a FROM t"), "dynamic_table", "d"),
    (("CREATE DYNAMIC TABLE d TARGET_LAG = '1 day' WAREHOUSE = wh REQUIRE USER INITIALIZE = ON_SCHEDULE "
     "AS SELECT a FROM t"), "dynamic_table", "d"),
]


@pytest.mark.parametrize("sql, kind, name", GOOD, ids=[f"{k}-{i}" for i, (_, k, _n) in enumerate(GOOD)])
def test_good_statements_pass_with_no_errors(sql, kind, name):
    result = record([one(sql)])[0]
    assert result.kind == kind and result.name == name
    assert result.ok and not errors(result), errors(result)
    assert result.label == LABEL


def test_every_result_is_labeled_a_local_check_not_snowflakes():
    assert "not Snowflake's own" in LABEL and "nothing was sent" in LABEL
    for result in validate("CREATE STREAM s ON TABLE t; SELECT 1; CREATE TASK x AS"):
        assert result.label == LABEL and result.to_dict()["label"] == LABEL


def test_the_properties_come_back_parsed():
    task, stream, dt = validate(
        "CREATE TASK t WAREHOUSE = wh SCHEDULE = '15 MINUTES' AS SELECT 1;"
        "CREATE STREAM s ON EXTERNAL TABLE e INSERT_ONLY = TRUE;"
        "CREATE DYNAMIC TABLE d TARGET_LAG = '5 minutes' WAREHOUSE = wh REFRESH_MODE = AUTO AS SELECT a FROM t")
    assert task.properties["WAREHOUSE"] == "wh" and task.properties["schedule_seconds"] == 900
    assert task.properties["AS"] == "SELECT 1" and task.properties["body_kind"] == "statement"
    assert stream.properties["source_kind"] == "EXTERNAL TABLE" and stream.properties["INSERT_ONLY"] == "TRUE"
    assert dt.properties["parsed_by"] == "sqlglot" and dt.properties["target_lag_seconds"] == 300
    assert dt.properties["REFRESH_MODE"] == "AUTO" and dt.properties["AS"].startswith("SELECT")
    cron = one("CREATE TASK c WAREHOUSE = wh SCHEDULE = 'USING CRON */15 9-17 L JAN,MAR 1-5 UTC' AS SELECT 1")
    assert cron.ok and cron.properties["cron"] == {"fields": ["*/15", "9-17", "L", "JAN,MAR", "1-5"], "time_zone": "UTC"}
    call = one("CREATE TASK c WAREHOUSE = wh SCHEDULE = '1 MINUTES' AS CALL p()")
    assert call.properties["body_kind"] == "call"
    d = task.to_dict()
    assert d["kind"] == "task" and d["ok"] is True and d["findings"] == []


def test_a_dynamic_table_sqlglot_cant_parse_is_read_by_the_token_parser():
    dt = one("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' WAREHOUSE = wh EXECUTE AS USER u "
             "FROZEN WHERE (a < 1) AS SELECT 1 AS a")
    assert dt.properties["parsed_by"] == "tokens" and dt.ok, errors(dt)
    assert dt.properties["EXECUTE AS USER"] == "u" and dt.properties["FROZEN WHERE"] == "a < 1"


def test_names_resolve_as_snowflake_does():
    result = one('CREATE TASK db."Sch".load_1$ WAREHOUSE = wh SCHEDULE = \'5 MINUTES\' AS SELECT 1')
    assert result.ok and result.properties["name_key"] == 'DB.Sch.LOAD_1$'


def test_a_scripting_body_stays_one_statement_and_gets_a_warning():
    task, stream = record(validate(
        "CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS DECLARE x INT; BEGIN x := 1; "
        "IF (x > 0) THEN INSERT INTO a VALUES (1); END IF; END; CREATE STREAM s ON VIEW v"))
    assert task.ok and task.properties["body_kind"] == "scripting"
    assert any("Snowflake Scripting" in w for w in warnings(task))
    assert stream.kind == "stream" and stream.ok
    dollar = record([one("CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS $$ BEGIN RETURN 1; END $$")])[0]
    assert dollar.properties["body_kind"] == "scripting" and dollar.ok


# ---------------------------------------------------------------------------- errors

ERRORS = [
    # tasks
    ("CREATE TASK t WAREHOUSE = wh USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE = 'SMALL' SCHEDULE = '5 MINUTES' AS SELECT 1",
     "both a WAREHOUSE and a USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE"),
    ("CREATE TASK t USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE = 'HUGE' SCHEDULE = '5 MINUTES' AS SELECT 1",
     "HUGE isn't a warehouse size"),
    ("CREATE TASK t USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE = 'X4LARGE' SCHEDULE = '5 MINUTES' AS SELECT 1",
     "at most at XXLARGE"),
    ("CREATE TASK t WAREHOUSE = 'wh' SCHEDULE = '5 MINUTES' AS SELECT 1", "WAREHOUSE takes a warehouse name"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '0 MINUTES' AS SELECT 1", "range of 1 to 11,520 minutes"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '11521 MINUTE' AS SELECT 1", "range of 1 to 11,520 minutes"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '9 SECONDS' AS SELECT 1", "range of 10 to 691,200 seconds"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '193 HOURS' AS SELECT 1", "range of 1 to 192 hours"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '2 DAYS' AS SELECT 1", "isn't an interval Snowflake reads"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = 'five minutes' AS SELECT 1", "isn't an interval Snowflake reads"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = 5 AS SELECT 1", "takes a quoted interval"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON 0 9 * * MON' AS SELECT 1", "has no time zone"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON 0 0 9 * * MON UTC' AS SELECT 1", "has 6 fields"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON 60 9 * * * UTC' AS SELECT 1", "minute field '60' is outside 0 to 59"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON 0 24 * * * UTC' AS SELECT 1", "hour field '24' is outside 0 to 23"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON 0 9 0 * * UTC' AS SELECT 1", "day of month field '0'"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON 0 9 * 13 * UTC' AS SELECT 1", "month field '13'"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON 0 9 * * 7 UTC' AS SELECT 1", "day of week field '7' is outside 0 to 6"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON 0 9 * * FUNDAY UTC' AS SELECT 1", "isn't one Snowflake reads"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON 0 9 * * 1-2-3 UTC' AS SELECT 1", "isn't one Snowflake reads"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON */0 9 * * * UTC' AS SELECT 1", "step that isn't a positive"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON 0 L * * * UTC' AS SELECT 1", "hour field 'L'"),
    ("CREATE TASK c WAREHOUSE = wh SCHEDULE = '5 MINUTES' AFTER p AS SELECT 1", "A child task (one with AFTER) can't have a SCHEDULE"),
    ("CREATE TASK f WAREHOUSE = wh FINALIZE = r SCHEDULE = '5 MINUTES' AS SELECT 1", "A finalizer task (one with FINALIZE)"),
    ("CREATE TASK c WAREHOUSE = wh AFTER p, q, p AS SELECT 1", "AFTER names P more than once"),
    ("CREATE TASK c WAREHOUSE = wh AFTER " + ", ".join(f"p{i}" for i in range(101)) + " AS SELECT 1",
     "101 predecessors in AFTER, and Snowflake allows 100"),
    ("CREATE TASK c WAREHOUSE = wh AFTER p AFTER q AS SELECT 1", "AFTER is given twice"),
    ("CREATE TASK c WAREHOUSE = wh TASK_AUTO_RETRY_ATTEMPTS = 2 AFTER p AS SELECT 1", "set only on a root task"),
    ("CREATE TASK c WAREHOUSE = wh OVERLAP_POLICY = ALLOW_ALL AFTER p AS SELECT 1", "set only on a root task"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' TASK_AUTO_RETRY_ATTEMPTS = 31 AS SELECT 1",
     "from 0 to 30"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' USER_TASK_TIMEOUT_MS = 604800001 AS SELECT 1",
     "from 0 to 604,800,000"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' SUSPEND_TASK_AFTER_NUM_FAILURES = -1 AS SELECT 1",
     "of 0 or more"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' USER_TASK_MINIMUM_TRIGGER_INTERVAL_IN_SECONDS = 9 AS SELECT 1",
     "from 10 to 604,800"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' USER_TASK_TIMEOUT_MS = 'long' AS SELECT 1",
     "takes a whole number"),
    ("CREATE TASK t TARGET_COMPLETION_INTERVAL = '25 HOURS' WHEN SYSTEM$STREAM_HAS_DATA('s') AS SELECT 1",
     "range of 1 to 24 hours"),
    ("CREATE TASK t WHEN SYSTEM$STREAM_HAS_DATA('s') AS INSERT INTO a SELECT * FROM s",
     "serverless triggered task needs a TARGET_COMPLETION_INTERVAL"),
    ("CREATE TASK t WAREHOUSE = wh WHEN SYSTEM$STREAM_HAS_DATA('s') AND x AS SELECT 1", "WHEN refers to a column"),
    ("CREATE TASK t WAREHOUSE = wh WHEN PARSE_JSON('1') = 1 AS SELECT 1", "WHEN calls PARSE_JSON"),
    ("CREATE TASK t WAREHOUSE = wh WHEN SYSTEM$CANCEL_ALL_QUERIES(1) AS SELECT 1", "WHEN calls SYSTEM$CANCEL_ALL_QUERIES"),
    ("CREATE TASK t WAREHOUSE = wh WHEN (SELECT 1) = 1 AS SELECT 1", "WHEN holds an expression Snowflake doesn't allow"),
    ("CREATE TASK t WAREHOUSE = wh WHEN SYSTEM$STREAM_HAS_DATA('s' AS SELECT 1", "opens a parenthesis that isn't closed"),
    ("CREATE TASK t WAREHOUSE = wh WHEN = = AS SELECT 1", "WHEN isn't a condition this check can read"),
    ("CREATE TASK t WAREHOUSE = wh WHEN AS SELECT 1", "WHEN has no condition"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES'", "A task needs AS"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS", "AS has no SQL after it"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS SELEC 1 FRM", "body isn't Snowflake SQL"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' RUN_EVERY AS SELECT 1", "RUN_EVERY isn't a clause CREATE TASK takes"),
    ("CREATE TASK t WAREHOUSE = wh WAREHOUSE = wh2 AS SELECT 1", "WAREHOUSE is given twice"),
    ("CREATE TASK t WAREHOUSE = AS SELECT 1", "WAREHOUSE has no value after the equals sign"),
    ("CREATE TASK t SCHEDULE =", "no value after the equals sign"),
    ("CREATE TASK t SUSPEND_TASK_AFTER_NUM_FAILURES = -x AS SELECT 1", "isn't a number"),
    ("CREATE TASK t EXECUTE USER u AS SELECT 1", "EXECUTE needs AS USER"),
    ("CREATE TRANSIENT TASK t AS SELECT 1", "TRANSIENT applies to dynamic tables"),
    # names, for every kind
    ("CREATE OR REPLACE TASK IF NOT EXISTS t AS SELECT 1", "OR REPLACE and IF NOT EXISTS"),
    ("CREATE TASK 1x AS SELECT 1", "isn't a valid Snowflake identifier"),
    ("CREATE STREAM my-stream ON TABLE t", "isn't a valid Snowflake identifier"),
    ("CREATE TASK a.b.c.d AS SELECT 1", "Use at most database.schema.name"),
    ('CREATE TASK "" AS SELECT 1', "empty quoted name"),
    ("CREATE TASK " + "a" * 256 + " AS SELECT 1", "longer than 255 characters"),
    ("CREATE DYNAMIC TABLE 9d TARGET_LAG = '1 minute' WAREHOUSE = wh AS SELECT 1", "isn't a valid Snowflake identifier"),
    ("CREATE TASK", "isn't a valid Snowflake identifier"),
    # streams
    ("CREATE STREAM s TABLE t", "CREATE STREAM needs ON"),
    ("CREATE STREAM s ON MATERIALIZED VIEW mv", "this one says MATERIALIZED"),
    ("CREATE STREAM s ON", "this one says nothing"),
    ("CREATE STREAM s ON STAGE st APPEND_ONLY = TRUE", "APPEND_ONLY doesn't apply to a stream on a stage"),
    ("CREATE STREAM s ON STAGE st AT(OFFSET => -60)", "AT doesn't apply to a stream on a stage"),
    ("CREATE STREAM s ON DYNAMIC TABLE d SHOW_INITIAL_ROWS = TRUE", "doesn't apply to a stream on a dynamic table"),
    ("CREATE STREAM s ON EXTERNAL TABLE e INSERT_ONLY = TRUE SHOW_INITIAL_ROWS = TRUE",
     "SHOW_INITIAL_ROWS doesn't apply to a stream on an external table"),
    ("CREATE STREAM s ON TABLE t INSERT_ONLY = TRUE", "INSERT_ONLY doesn't apply to a stream on a table"),
    ("CREATE STREAM s ON EXTERNAL TABLE e", "needs INSERT_ONLY = TRUE"),
    ("CREATE STREAM s ON EXTERNAL TABLE e INSERT_ONLY = FALSE", "needs INSERT_ONLY = TRUE"),
    ("CREATE STREAM s ON TABLE t APPEND_ONLY = 'yes'", "takes TRUE or FALSE"),
    ("CREATE STREAM s ON TABLE t KEEP_ROWS = TRUE", "KEEP_ROWS isn't a CREATE STREAM option"),
    ("CREATE STREAM s ON TABLE t BEFORE(STREAM => 'other')", "BEFORE takes TIMESTAMP, OFFSET or STATEMENT"),
    ("CREATE STREAM s ON TABLE t AT(VERSION => 3)", "AT takes TIMESTAMP, OFFSET, STATEMENT or STREAM"),
    ("CREATE STREAM s ON TABLE t AT(OFFSET => -1) BEFORE(OFFSET => -2)", "takes one AT or BEFORE"),
    ("CREATE STREAM s ON TABLE t AT", "needs a list in parentheses"),
    ("CREATE STREAM s ON TABLE t REFRESH", "REFRESH isn't a clause CREATE STREAM takes"),
    # dynamic tables
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' AS SELECT a FROM t", "needs a WAREHOUSE"),
    ("CREATE DYNAMIC TABLE d WAREHOUSE = wh AS SELECT a FROM t", "needs a TARGET_LAG"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '59 seconds' WAREHOUSE = wh AS SELECT a FROM t", "minimum of 60 seconds"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 week' WAREHOUSE = wh AS SELECT a FROM t", "isn't a lag Snowflake reads"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = 5 WAREHOUSE = wh AS SELECT a FROM t", "isn't a lag Snowflake reads"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = UPSTREAM WAREHOUSE = wh AS SELECT a FROM t", "isn't a lag Snowflake reads"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' SCHEDULER = DISABLE WAREHOUSE = wh AS SELECT a FROM t",
     "can't be set with SCHEDULER = DISABLE"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' SCHEDULER = SOMETIMES WAREHOUSE = wh AS SELECT a FROM t",
     "SCHEDULER takes ENABLE or DISABLE"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' WAREHOUSE = wh REFRESH_MODE = LAZY AS SELECT a FROM t",
     "REFRESH_MODE is LAZY"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' WAREHOUSE = wh INITIALIZE = LATER AS SELECT a FROM t",
     "INITIALIZE is LATER"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' WAREHOUSE = wh REQUIRE USER AS SELECT a FROM t",
     "REQUIRE USER needs INITIALIZE = ON_SCHEDULE"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' WAREHOUSE = wh REFRESH_MODE = CUSTOM_INCREMENTAL AS SELECT a FROM t",
     "CUSTOM_INCREMENTAL needs REFRESH USING"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' WAREHOUSE = wh", "needs AS followed by the query"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' WAREHOUSE = wh AS SELECT FROM", "query isn't Snowflake SQL"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' WAREHOUSE = wh AS INSERT INTO a VALUES (1)",
     "AS holds another kind of statement"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' WAREHOUSE = wh FOO = 1 REQUIRE USER AS SELECT 1",
     "FOO isn't a CREATE DYNAMIC TABLE property"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' WAREHOUSE = wh REQUIRE USER LATER AS SELECT 1",
     "LATER isn't a clause CREATE DYNAMIC TABLE takes"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' WAREHOUSE = wh WAREHOUSE = w2 AS SELECT 1",
     "WAREHOUSE is given twice"),
    # other statements
    ("SELECT 1", "reads CREATE TASK, CREATE STREAM and CREATE DYNAMIC TABLE statements only"),
    ("CREATE TABLE t (a INT)", "reads CREATE TASK, CREATE STREAM and CREATE DYNAMIC TABLE statements only"),
    ("SELECT 'abc", "can't be read"),
]


@pytest.mark.parametrize("sql, expected", ERRORS, ids=[f"{i}-{s[:40]}" for i, (s, _) in enumerate(ERRORS)])
def test_each_rule_has_an_error_that_names_it(sql, expected):
    results = record(validate(sql))
    found = [m for r in results for m in errors(r)]
    assert any(expected in m for m in found), found
    assert not all(r.ok for r in results)


WARNINGS = [
    ("CREATE TASK t SCHEDULE = '5 MINUTES' AS SELECT 1", "names neither a WAREHOUSE nor a USER_TASK_MANAGED"),
    ("CREATE TASK t WAREHOUSE = wh AS SELECT 1", "runs only when started with EXECUTE TASK"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' FOO = 1 AS SELECT 1", "FOO isn't a CREATE TASK property"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS EXECUTE IMMEDIATE $$ SELECT 1 $$",
     "reads the body's EXECUTE only as a command"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON 0 9 ? * * UTC' AS SELECT 1", "form Snowflake's documentation doesn't list"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON 0 9 * * 5L UTC' AS SELECT 1", "form Snowflake's documentation doesn't list"),
    ("CREATE TASK t CLONE other", "A clone takes its definition"),
    (("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' WAREHOUSE = wh REFRESH_MODE = CUSTOM_INCREMENTAL "
     "REFRESH USING (INSERT INTO d SELECT * FROM t)"), "REFRESH USING defines a custom incremental refresh"),
    ("CREATE TASK c WAREHOUSE = wh AFTER elsewhere AS SELECT 1", "isn't created in these statements"),
]


@pytest.mark.parametrize("sql, expected", WARNINGS, ids=[f"{i}-{s[:40]}" for i, (s, _) in enumerate(WARNINGS)])
def test_each_caution_is_a_warning_not_an_error(sql, expected):
    result = record([one(sql)])[0]
    assert any(expected in m for m in warnings(result)), result.findings
    assert result.ok, errors(result)


def test_a_session_parameter_it_knows_gets_no_warning():
    result = one("CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' TIMEZONE = 'UTC' AS SELECT 1")
    assert result.ok and not result.findings


def test_an_unknown_time_zone_gets_a_warning_where_this_pc_knows_time_zones():
    result = record([one("CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON 0 9 * * * Mars/Base' AS SELECT 1")])[0]
    assert result.ok
    if sov._zones():
        assert any("Mars/Base isn't a time zone" in m for m in warnings(result))


# ---------------------------------------------------------------------------- task graphs


def test_after_references_resolve_within_the_set():
    results = record(validate(
        "CREATE TASK db.s.root WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS SELECT 1;"
        "CREATE TASK a WAREHOUSE = wh AFTER root AS SELECT 1;"
        "CREATE TASK b WAREHOUSE = wh AFTER root, a AS SELECT 1;"
        "CREATE TASK fin WAREHOUSE = wh FINALIZE = root AS SELECT 1"))
    assert all(r.ok and not r.findings for r in results), [(r.name, r.findings) for r in results]


def test_an_after_reference_outside_the_set_is_reported_as_external():
    _root, child = record(validate("CREATE TASK root WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS SELECT 1;"
                                   "CREATE TASK child WAREHOUSE = wh AFTER root, other.sch.upstream AS SELECT 1"))
    assert child.ok
    assert warnings(child) == [("Predecessor other.sch.upstream isn't created in these statements, so this check takes "
                               "it to exist on Snowflake already. Make sure it's in the same schema and has the same owner.")]


def test_a_cycle_is_an_error_on_each_task_in_it():
    results = record(validate("CREATE TASK a WAREHOUSE = wh AFTER c AS SELECT 1;"
                              "CREATE TASK b WAREHOUSE = wh AFTER a AS SELECT 1;"
                              "CREATE TASK c WAREHOUSE = wh AFTER b AS SELECT 1;"
                              "CREATE TASK d WAREHOUSE = wh AFTER a AS SELECT 1"))
    a, b, c, d = results
    for task in (a, b, c):
        assert any("form a cycle through AFTER" in m for m in errors(task)), task.findings
    assert d.ok


def test_a_task_after_itself_is_a_cycle():
    result = record([one("CREATE TASK a WAREHOUSE = wh AFTER a AS SELECT 1")])[0]
    assert errors(result) == ["Task a names itself in AFTER, and a task graph can't loop. Remove it from its own AFTER."]


def test_more_than_100_children_is_an_error_on_the_parent():
    sql = "CREATE TASK root WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS SELECT 1;" + "".join(
        f"CREATE TASK c{i} WAREHOUSE = wh AFTER root AS SELECT 1;" for i in range(101))
    root, *children = record(validate(sql))
    assert any("101 child tasks, and Snowflake allows 100" in m for m in errors(root))
    assert all(c.ok for c in children)


def test_a_graph_of_more_than_1000_tasks_is_an_error_on_its_root(monkeypatch):
    monkeypatch.setattr(sov, "MAX_GRAPH_TASKS", 3)
    results = record(validate("CREATE TASK root WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS SELECT 1;"
                              "CREATE TASK a WAREHOUSE = wh AFTER root AS SELECT 1;"
                              "CREATE TASK b WAREHOUSE = wh AFTER a AS SELECT 1;"
                              "CREATE TASK c WAREHOUSE = wh AFTER b AS SELECT 1"))
    assert any("has 4 tasks, and Snowflake allows 3 in one graph" in m for m in errors(results[0]))
    assert all(r.ok for r in results[1:])
    assert sov.MAX_GRAPH_TASKS == 3 and sov.MAX_PREDECESSORS == sov.MAX_CHILDREN == 100


def test_the_documented_graph_limit_is_1000():
    monkey_free = validate("CREATE TASK root WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS SELECT 1;" + "".join(
        f"CREATE TASK c{i} WAREHOUSE = wh AFTER {'root' if i < 100 else f'c{i % 100}'} AS SELECT 1;" for i in range(1000)))
    assert any("1001 tasks, and Snowflake allows 1000" in m for m in errors(monkey_free[0]))


def test_finalizer_rules_across_statements():
    results = record(validate(
        "CREATE TASK root WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS SELECT 1;"
        "CREATE TASK child WAREHOUSE = wh AFTER root AS SELECT 1;"
        "CREATE TASK f1 WAREHOUSE = wh FINALIZE = root AS SELECT 1;"
        "CREATE TASK f2 WAREHOUSE = wh FINALIZE = root AS SELECT 1;"
        "CREATE TASK f3 WAREHOUSE = wh FINALIZE = child AS SELECT 1;"
        "CREATE TASK after_fin WAREHOUSE = wh AFTER f1 AS SELECT 1;"
        "CREATE TASK f4 WAREHOUSE = wh FINALIZE = elsewhere AS SELECT 1"))
    by = {r.name: r for r in results}
    assert any("has 2 finalizer tasks, and Snowflake allows one" in m for m in errors(by["f1"]))
    assert any("has 2 finalizer tasks" in m for m in errors(by["f2"]))
    assert any("FINALIZE names child, which isn't a root task" in m for m in errors(by["f3"]))
    assert any("Finalizer task f1 can't have child tasks" in m for m in errors(by["f1"]))
    assert any("Root task elsewhere isn't created in these statements" in m for m in warnings(by["f4"]))


def test_a_task_defined_twice_is_a_warning_and_qualified_names_resolve_by_their_last_part():
    results = record(validate("CREATE TASK db.s.root WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS SELECT 1;"
                              "CREATE OR REPLACE TASK db.s.root WAREHOUSE = wh SCHEDULE = '6 MINUTES' AS SELECT 1;"
                              "CREATE TASK child WAREHOUSE = wh AFTER ROOT AS SELECT 1"))
    assert any("created more than once" in m for m in warnings(results[1]))
    assert results[2].ok and not results[2].findings


def test_a_quoted_name_resolves_only_with_its_case():
    _root, child = validate('CREATE TASK "root" WAREHOUSE = wh SCHEDULE = \'5 MINUTES\' AS SELECT 1;'
                            "CREATE TASK child WAREHOUSE = wh AFTER root AS SELECT 1")
    assert any("isn't created in these statements" in m for m in warnings(child))


def test_a_task_with_errors_still_takes_part_in_the_graph():
    _root, child = validate("CREATE TASK root WAREHOUSE = wh SCHEDULE = '0 MINUTES' AS SELECT 1;"
                            "CREATE TASK child WAREHOUSE = wh AFTER root AS SELECT 1")
    assert child.ok and not child.findings


# ---------------------------------------------------------------------------- messages


def test_nothing_raises_on_unfinished_statements():
    for sql in ("CREATE", "CREATE TASK t AFTER", "CREATE TASK t WHEN", "CREATE STREAM s ON TABLE",
                "CREATE DYNAMIC TABLE", "CREATE DYNAMIC TABLE d (a", "CREATE TASK t EXECUTE AS", "", "  ;  ;"):
        record(validate(sql))
    assert validate("") == []


GRAPHS = [
    "CREATE TASK a WAREHOUSE = wh AFTER b AS SELECT 1; CREATE TASK b WAREHOUSE = wh AFTER a AS SELECT 1",
    "CREATE TASK a WAREHOUSE = wh AFTER a AS SELECT 1",
    "CREATE TASK root WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS SELECT 1;" + "".join(
        f"CREATE TASK c{i} WAREHOUSE = wh AFTER root AS SELECT 1;" for i in range(101)),
    ("CREATE TASK r WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS SELECT 1; CREATE TASK c WAREHOUSE = wh AFTER r AS SELECT 1;"
    "CREATE TASK f1 WAREHOUSE = wh FINALIZE = r AS SELECT 1; CREATE TASK f2 WAREHOUSE = wh FINALIZE = r AS SELECT 1;"
    "CREATE TASK f3 WAREHOUSE = wh FINALIZE = c AS SELECT 1; CREATE TASK x WAREHOUSE = wh AFTER f1 AS SELECT 1;"
    "CREATE TASK f4 WAREHOUSE = wh FINALIZE = nowhere AS SELECT 1; CREATE TASK r WAREHOUSE = wh AS SELECT 1"),
]


def test_every_message_is_a_plain_sentence_with_a_next_step(monkeypatch):
    monkeypatch.setattr(sov, "MAX_GRAPH_TASKS", 3)
    sqls = [s for s, *_ in GOOD + ERRORS + WARNINGS] + GRAPHS + [
        "CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON 0 9 * * * Mars/Base' AS SELECT 1"]
    messages = {f.message for sql in sqls for r in validate(sql) for f in r.findings}
    assert len(messages) > 100
    for message in messages | {LABEL}:
        assert ":" not in message and ";" not in message, message
        assert message[0].isupper() or message[0] in "'\"", message
        assert message.endswith("."), message
        sentences = [s for s in message.replace("e.g.", "").split(". ") if s]
        assert len(sentences) >= 2, f"no next step in {message!r}"
        assert "Traceback" not in message and "Error(" not in message


# ---------------------------------------------------------------------------- input that would overflow


def nested(levels: int) -> str:
    return "(" * levels + "1" + ")" * levels


CASES = "SELECT " + "CASE WHEN 1 = 1 THEN " * 300 + "1" + " END" * 300


@pytest.mark.parametrize("levels", [100, 3000])
@pytest.mark.parametrize("template, kind", [
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' WHEN {} = 1 AS SELECT 1", "task"),
    ("CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS SELECT {}", "task"),
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 hour' WAREHOUSE = wh AS SELECT {} AS x FROM s", "dynamic_table"),
])
def test_a_statement_nested_too_deeply_is_an_error_on_it_not_a_crash(template, kind, levels):
    result = one(template.format(nested(levels)))
    assert result.kind == kind   # read as far as the nesting, so its kind and name are still known
    assert any("nested too deeply" in m for m in errors(result)), result.findings


def test_a_body_of_300_nested_cases_is_an_error_on_it_not_a_crash():
    result = one(f"CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS {CASES}")
    assert result.kind == "task"
    assert any("nested too deeply" in m for m in errors(result)), result.findings


def test_any_other_overflow_while_reading_a_statement_is_reported_on_it(monkeypatch):
    def overflow(text):
        raise RecursionError
    monkeypatch.setattr(sov, "_statement", overflow)
    results = validate("CREATE TASK a WAREHOUSE = wh AS SELECT 1; CREATE TASK b WAREHOUSE = wh AS SELECT 1")
    assert [errors(r) for r in results] == [[sov._TOO_NESTED]] * 2


def test_a_long_chain_written_child_first_is_checked_without_overflowing():
    count = 3000
    sql = "".join(f"CREATE TASK t{i} WAREHOUSE = wh AFTER t{i + 1} AS SELECT 1;" for i in range(count - 1))
    sql += f"CREATE TASK t{count - 1} WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS SELECT 1"
    results = validate(sql)
    assert len(results) == count
    assert not any("cycle" in m for r in results for m in errors(r))
    looped = validate(sql.replace(f"t{count - 1} WAREHOUSE = wh SCHEDULE = '5 MINUTES'", f"t{count - 1} WAREHOUSE = wh AFTER t0"))
    assert all(any("form a cycle through AFTER" in m for m in errors(r)) for r in looped)


@pytest.mark.parametrize("sql", [
    "CREATE TASK begin WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS SELECT 1; CREATE STREAM s ON TABLE x",
    "CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS SELECT declare FROM x; CREATE STREAM s ON TABLE x",
])
def test_begin_or_declare_as_a_name_doesnt_swallow_the_next_statement(sql):
    task, stream = validate(sql)
    assert task.kind == "task" and task.properties["AS"].upper().startswith("SELECT") and ";" not in task.properties["AS"]
    assert stream.kind == "stream"


def test_a_scripting_block_after_as_or_on_its_own_stays_one_statement():
    body = "CREATE TASK t WAREHOUSE = wh SCHEDULE = '5 MINUTES' AS DECLARE n INT; BEGIN n := 1; RETURN n; END"
    assert [r.kind for r in validate(body + "; CREATE STREAM s ON TABLE x")] == ["task", "stream"]
    assert len(validate("BEGIN SELECT 1; BEGIN SELECT 2; END; END; SELECT 3")) == 2


def test_an_unknown_dynamic_table_property_is_refused_on_both_paths():
    for sql in ("CREATE DYNAMIC TABLE d TARGET_LAG = '5 minutes' WAREHOUSE = wh FOO = 1 AS SELECT a FROM s",
                "CREATE DYNAMIC TABLE d (a) TARGET_LAG = '5 minutes' WAREHOUSE = wh FOO = 1 AS SELECT a FROM s"):
        result = one(sql)
        assert any("FOO isn't a CREATE DYNAMIC TABLE property" in m for m in errors(result)), (sql, result.findings)


@pytest.mark.parametrize("cron, finding", [
    ("*/ * * * *", "step that isn't a positive whole number"),
    ("*/00 * * * *", "step that isn't a positive whole number"),
    ("0 0 * * FRI-MON", "runs from high to low"),
])
def test_cron_steps_and_ranges_that_snowflake_may_not_read(cron, finding):
    result = one(f"CREATE TASK t WAREHOUSE = wh SCHEDULE = 'USING CRON {cron} UTC' AS SELECT 1")
    assert any(finding in m for m in errors(result) + warnings(result)), result.findings


def test_an_unreadable_target_lag_is_shown_in_its_quotes():
    result = one("CREATE DYNAMIC TABLE d TARGET_LAG = '1 week' WAREHOUSE = wh AS SELECT a FROM s")
    assert any(m.startswith("TARGET_LAG '1 week' isn't a lag") for m in errors(result)), result.findings
