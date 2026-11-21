"""Behavioural tests for the vendored Ossie helpers: _common, _dax_to_sql and _sql_to_dax."""

import json
import logging
import warnings

import pytest

from fabric_desktop._vendor.ossie_microsoft import _common as common
from fabric_desktop._vendor.ossie_microsoft import _dax_to_sql as dax_to_sql
from fabric_desktop._vendor.ossie_microsoft import _sql_to_dax as sql_to_dax

# ---------------------------------------------------------------------------
# _common
# ---------------------------------------------------------------------------


def test_text_joins_line_arrays_and_stringifies_scalars():
    assert common.text(["SUM(", "  x)", 3]) == "SUM(\n  x)\n3"
    assert common.text(42) == "42"
    assert common.text("plain") == "plain"


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        (None, False),
        ("", False),
        ("Short Date", True),
        ("long date", True),
        (" Medium Date ", True),
        ("General Date", False),
        ("Long Time", False),
        ("yyyy-mm-dd", True),
        (["dd", "/mm/yyyy"], True),
        ("yyyy-mm-dd hh:nn", False),
        ("dd/mm/yyyy AM/PM", False),
        ('"Hours" yyyy', True),  # quoted literal text is not a time token
        ("\\h yyyy", True),  # escaped character is display text
        ("#,0.00", False),  # no date token at all
        ("0 %", False),
    ],
)
def test_is_date_only_format(fmt, expected):
    assert common.is_date_only_format(fmt) is expected


def test_warn_emits_userwarning_and_log_record(caplog):
    with caplog.at_level(logging.WARNING, logger="ossie_microsoft"):
        with pytest.warns(UserWarning, match=r"\[table 'Sales'\] something was lost"):
            common.warn("table 'Sales'", "something was lost")
    assert "[table 'Sales'] something was lost" in caplog.text


def test_warn_can_be_escalated_to_error():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(UserWarning, match="lossy"):
            common.warn("scope", "lossy")


def test_warn_unsupported_reports_only_catalogued_keys_sorted():
    with pytest.warns(UserWarning) as record:
        reported = common.warn_unsupported(
            "model",
            {"roles": [], "cultures": [], "name": "x"},
            common.TMSL_UNSUPPORTED_MODEL,
            "Apache Ossie",
            "kept",
        )
    assert reported == ["cultures", "roles"]
    messages = [str(w.message) for w in record]
    assert messages == [
        "[model] translations and linguistic metadata ('cultures') has no Apache Ossie counterpart; kept",
        "[model] row-level security roles ('roles') has no Apache Ossie counterpart; kept",
    ]


def test_read_stash_none_and_no_power_bi_entry():
    assert common.read_stash(None) == {}
    assert common.read_stash({}) == {}
    assert common.read_stash({"custom_extensions": None}) == {}
    assert (
        common.read_stash(
            {"custom_extensions": ["junk", {"vendor_name": "DBT", "data": "{}"}]}
        )
        == {}
    )


def test_read_stash_strips_version_and_empty_data():
    obj = {
        "custom_extensions": [{"vendor_name": "POWER_BI", "data": '{"_v": 1, "a": 2}'}]
    }
    assert common.read_stash(obj) == {"a": 2}
    assert common.read_stash({"custom_extensions": [{"vendor_name": "POWER_BI"}]}) == {}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ("{not json", "is not valid JSON"),
        ("[1, 2]", "must be a JSON object"),
        ('{"_v": "2"}', "non-integer version '_v': '2'"),
        ('{"_v": true}', "non-integer version '_v': True"),
        ('{"_v": 99}', "newer converter \\(format version 99"),
    ],
)
def test_read_stash_rejects_bad_payloads(data, message):
    obj = {"custom_extensions": [{"vendor_name": "POWER_BI", "data": data}]}
    with pytest.raises(common.ConversionError, match=message):
        common.read_stash(obj)


def test_write_stash_noop_new_entry_and_merge():
    obj = {}
    common.write_stash(obj, {})
    assert obj == {}

    common.write_stash(obj, {"b": 1, "a": 2})
    assert obj["custom_extensions"] == [
        {
            "vendor_name": "POWER_BI",
            "data": json.dumps({"_v": 2, "a": 2, "b": 1}, sort_keys=True),
        }
    ]

    obj["custom_extensions"].insert(0, {"vendor_name": "DBT", "data": "{}"})
    common.write_stash(obj, {"c": 3})
    assert len(obj["custom_extensions"]) == 2
    assert common.read_stash(obj) == {"c": 3}
    assert obj["custom_extensions"][0] == {"vendor_name": "DBT", "data": "{}"}


def test_foreign_vendor_extensions_filters_power_bi_and_junk():
    obj = {
        "custom_extensions": [
            {"vendor_name": "POWER_BI", "data": "{}"},
            {"vendor_name": "DBT", "data": "{}"},
            "junk",
        ]
    }
    assert common.foreign_vendor_extensions(obj) == [
        {"vendor_name": "DBT", "data": "{}"}
    ]
    assert common.foreign_vendor_extensions(None) == []


def test_dialect_expressions_mapping_and_validation():
    expression = {
        "dialects": [
            "junk",
            {"dialect": "DAX", "expression": "SUM(x)"},
            {"dialect": "ANSI_SQL", "expression": ""},
            {"dialect": None, "expression": "ignored"},
            {"dialect": "SNOWFLAKE", "expression": None},
        ]
    }
    assert common.dialect_expressions(expression) == {"DAX": "SUM(x)"}
    assert common.dialect_expressions(None) == {}
    with pytest.raises(
        common.ConversionError, match="expression must be a string, got int"
    ):
        common.dialect_expressions({"dialects": [{"dialect": "DAX", "expression": 5}]})


def test_make_expression_and_prune():
    assert common.make_expression("x", "DAX") == {
        "dialects": [{"dialect": "DAX", "expression": "x"}]
    }
    assert common.prune(
        {"a": None, "b": "", "c": [], "d": {}, "e": 0, "f": False, "g": "v"}
    ) == {
        "e": 0,
        "f": False,
        "g": "v",
    }


# ---------------------------------------------------------------------------
# _dax_to_sql
# ---------------------------------------------------------------------------

_KNOWN = {("Sales", "Amount"), ("Sales", "Customer"), ("Sales Data", "Net Amount")}


def _resolve(table, column):
    return (table, column) if (table, column) in _KNOWN else None


@pytest.mark.parametrize(
    ("dax", "sql"),
    [
        ("SUM('Sales'[Amount])", "SUM(Sales.Amount)"),
        ("=  average(Sales[Amount])", "AVG(Sales.Amount)"),
        ("MIN( 'Sales' [Amount] )", "MIN(Sales.Amount)"),
        ("MAX(Sales[Amount])", "MAX(Sales.Amount)"),
        ("COUNTA(Sales[Customer])", "COUNT(Sales.Customer)"),
        ("DISTINCTCOUNTNOBLANK('Sales'[Customer])", "COUNT(DISTINCT Sales.Customer)"),
    ],
)
def test_translate_measure_success(dax, sql):
    assert dax_to_sql.translate_measure(dax, _resolve) == (sql, None)


@pytest.mark.parametrize(
    ("dax", "reason"),
    [
        (None, "measure has no expression"),
        ("  =  ", "measure has no expression"),
        ("[Total] + 1", "only a single aggregate function call is translated"),
        ("COUNT(Sales[Amount])", "DAX COUNT does not support TRUE/FALSE"),
        ("DISTINCTCOUNT(Sales[Customer])", "DAX DISTINCTCOUNT counts BLANK"),
        ("CALCULATE(SUM(Sales[Amount]))", "CALCULATE manipulates filter context"),
        ("PRODUCT(Sales[Amount])", "'PRODUCT' is not a translated aggregate"),
        ("SUM([Amount])", "only an aggregate over one fully qualified column"),
        ("SUM(Sales[Amount], 1)", "only an aggregate over one fully qualified column"),
        ("SUM(Sales[Missing])", "column 'Sales'[Missing] does not resolve"),
    ],
)
def test_translate_measure_refusals(dax, reason):
    sql, why = dax_to_sql.translate_measure(dax, _resolve)
    assert sql is None
    assert reason in why


def test_translate_measure_unescapes_quoted_names_before_resolving():
    seen = []

    def resolve(table, column):
        seen.append((table, column))
        return ("ok", "fine")

    assert dax_to_sql.translate_measure("SUM('O''Brien'[a]]b])", resolve) == (
        "SUM(ok.fine)",
        None,
    )
    assert seen == [("O'Brien", "a]b")]


def test_translate_measure_refuses_names_needing_sql_quoting():
    sql, why = dax_to_sql.translate_measure("SUM('Sales Data'[Net Amount])", _resolve)
    assert sql is None
    assert why == "a dataset or field name needing SQL quoting is a later tier"


def test_qualify_rejects_empty_and_non_identifiers():
    assert dax_to_sql._qualify("a", "b") == "a.b"
    assert dax_to_sql._qualify("", "b") is None
    assert dax_to_sql._qualify("a", "1b") is None


# ---------------------------------------------------------------------------
# _sql_to_dax
# ---------------------------------------------------------------------------

_INDEX = {
    "amount": ("Sales", "Amount"),
    "cust": ("Sales", "Customer"),
    "first": ("Customer", "First"),
    "last": ("Customer", "Last"),
    "s.sales": ("s", "sales"),
}


def _col(name):
    return _INDEX.get(name)


def _one_table():
    return "Sales"


def test_quote_helpers_escape():
    assert sql_to_dax.quote_table("O'Brien") == "'O''Brien'"
    assert sql_to_dax.quote_column("a]b") == "[a]]b]"
    assert sql_to_dax.quote_string('say "hi"') == '"say ""hi"""'


@pytest.mark.parametrize(
    ("sql", "dax"),
    [
        ("SUM(amount)", "SUM('Sales'[Amount])"),
        ("MIN(amount)", "MIN('Sales'[Amount])"),
        ("MAX(amount)", "MAX('Sales'[Amount])"),
        ("AVG(amount)", "AVERAGE('Sales'[Amount])"),
        ("MEDIAN(amount)", "MEDIAN('Sales'[Amount])"),
        ("STDDEV(amount)", "STDEV.S('Sales'[Amount])"),
        ("STDDEV_SAMP(amount)", "STDEV.S('Sales'[Amount])"),
        ("STDDEV_POP(amount)", "STDEV.P('Sales'[Amount])"),
        ("VARIANCE(amount)", "VAR.S('Sales'[Amount])"),
        ("VAR_POP(amount)", "VAR.P('Sales'[Amount])"),
        ("COUNT(cust)", "COUNTA('Sales'[Customer])"),
        ("COUNT(DISTINCT cust)", "DISTINCTCOUNTNOBLANK('Sales'[Customer])"),
        ("COUNT(*)", "COUNTROWS('Sales')"),
        ("SUM(amount) AS total", "SUM('Sales'[Amount])"),
        ("SUM(s.sales) / COUNT(*)", "DIVIDE(SUM('s'[sales]), COUNTROWS('Sales'))"),
        (
            "SUM(amount) / NULLIF(COUNT(cust), 0)",
            "DIVIDE(SUM('Sales'[Amount]), COUNTA('Sales'[Customer]))",
        ),
        ("first || ' ' || last", "'Customer'[First] & \" \" & 'Customer'[Last]"),
    ],
)
def test_translate_success(sql, dax):
    assert sql_to_dax.translate(sql, "ANSI_SQL", _col, _one_table) == (dax, None)


@pytest.mark.parametrize(
    "dialect", ["OSSIE_SQL_2026", "SNOWFLAKE", "DATABRICKS", "BIGQUERY"]
)
def test_translate_reads_every_sql_dialect(dialect):
    assert sql_to_dax.translate("SUM(amount)", dialect, _col, _one_table) == (
        "SUM('Sales'[Amount])",
        None,
    )


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("SUM(", "the expression could not be parsed as SQL"),
        (
            "SUM(amount) + 1",
            "only a single aggregate over one column translates unambiguously",
        ),
        ("amount", "only a single aggregate over one column translates unambiguously"),
        (
            "MAX(amount, cust)",
            "an aggregate over multiple arguments has no DAX equivalent",
        ),
        (
            "COUNT(DISTINCT amount, cust)",
            "'DISTINCT' over multiple columns has no DAX equivalent",
        ),
        ("COUNT(DISTINCT *)", "'COUNT(DISTINCT *)' has no DAX equivalent"),
        ("SUM(DISTINCT amount)", "'DISTINCT' inside 'SUM' has no DAX equivalent"),
        (
            "SUM(amount + cust)",
            "only an aggregate over a single column reference is translated",
        ),
        ("SUM(nope)", "column 'nope' does not resolve to exactly one dataset field"),
        (
            "SUM(amount) / NULLIF(COUNT(*), 1)",
            "only NULLIF(aggregate, 0) is translated in a denominator",
        ),
        (
            "SUM(amount) / NULLIF(COUNT(*), '0')",
            "only NULLIF(aggregate, 0) is translated in a denominator",
        ),
        (
            "SUM(amount) / NULLIF(COUNT(*), cust)",
            "only NULLIF(aggregate, 0) is translated in a denominator",
        ),
        ("SUM(nope) / COUNT(*)", "column 'nope' does not resolve"),
        (
            "SUM(amount) / cust",
            "only supported aggregate expressions can be used in a metric",
        ),
        ("first || 1", "a concatenation may contain only columns and string literals"),
        ("nope || first", "column 'nope' does not resolve"),
        ("first || nope", "column 'nope' does not resolve"),
    ],
)
def test_translate_refusals(sql, reason):
    dax, why = sql_to_dax.translate(sql, "ANSI_SQL", _col, _one_table)
    assert dax is None
    assert reason in why


def test_translate_refuses_non_sql_dialect():
    assert sql_to_dax.translate("SUM(x)", "MDX", _col, _one_table) == (
        None,
        "'MDX' is not a SQL dialect this converter can parse",
    )


def test_count_star_needs_exactly_one_table():
    assert sql_to_dax.translate("COUNT(*)", "ANSI_SQL", _col, lambda: None) == (
        None,
        "'COUNT(*)' needs exactly one dataset to count rows of",
    )


def test_database_qualified_column_never_reaches_resolver():
    calls = []

    def resolve(name):
        calls.append(name)
        return ("T", "c")

    dax, why = sql_to_dax.translate("SUM(db.t.amount)", "ANSI_SQL", resolve, _one_table)
    assert dax is None
    assert "does not resolve" in why
    assert calls == []
    # A table-qualified column is resolved by its dotted key.
    assert sql_to_dax.translate("SUM(t.amount)", "ANSI_SQL", resolve, _one_table) == (
        "SUM('T'[c])",
        None,
    )
    assert calls == ["t.amount"]


def test_translate_concatenation():
    assert sql_to_dax.translate_concatenation("first || last", "SNOWFLAKE", _col) == (
        "'Customer'[First] & 'Customer'[Last]",
        None,
    )
    assert sql_to_dax.translate_concatenation("SUM(amount)", "ANSI_SQL", _col) == (
        None,
        "only string concatenation is translated for a calculated column",
    )
    assert sql_to_dax.translate_concatenation("SUM(", "ANSI_SQL", _col) == (
        None,
        "the expression could not be parsed as SQL",
    )
    assert sql_to_dax.translate_concatenation("a || b", "TABLEAU", _col)[0] is None
