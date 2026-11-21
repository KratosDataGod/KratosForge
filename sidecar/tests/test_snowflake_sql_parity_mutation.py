"""snowflake_sql_parity.py paths that mutation testing (batch 18, docs/quality/mutation-testing.md) showed no
test checked: how a case's reference link is made, each way ``run`` answers, and the docs table."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import snowflake_sql_parity as parity


@pytest.fixture(autouse=True)
def _own_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _case(sql, expected, outcome="same"):
    return parity.Case("F", "G", sql, expected, outcome, "https://docs.snowflake.com/en/sql-reference/functions/f")


def _named(function):
    return next(c for c in parity.CASES if c.function == function)


def test_a_case_links_the_page_named_by_its_first_word_unless_one_is_given():
    assert _named("IFF").source == "https://docs.snowflake.com/en/sql-reference/functions/iff"
    assert _named("NVL2 (NULL)").source == "https://docs.snowflake.com/en/sql-reference/functions/nvl2"
    assert _named("% (modulo)").source == "https://docs.snowflake.com/en/sql-reference/functions/mod"
    made = parity._c("ABS", "Numeric", "SELECT abs(-3)", 3)
    assert made == parity.Case("ABS", "Numeric", "SELECT abs(-3)", 3, "same",
                               "https://docs.snowflake.com/en/sql-reference/functions/abs", "")


def test_run_reports_a_statement_that_runs_only_in_snowflake_with_the_reason():
    outcome, value = parity.run(_case("SELECT current_account()", "x"))
    assert outcome == "untranslated"
    assert value.startswith("Statement 1 runs only in Snowflake: CURRENT_ACCOUNT")


def test_run_counts_the_documented_error_as_the_same_answer():
    expected = parity.Raises("Division by zero")
    assert parity.run(_case("SELECT 1/0", expected), duckdb.connect()) == ("same", expected)


def test_run_reports_another_error_by_its_type_and_first_line():
    want = ("error", "InvalidInputException: Invalid Input Error: Division by zero")
    assert parity.run(_case("SELECT 1/0", parity.Raises("not this message"))) == want
    assert parity.run(_case("SELECT 1/0", 5)) == want


def test_run_gives_none_for_no_rows_and_compares_the_first_column():
    assert parity.run(_case("SELECT 1 WHERE FALSE", None)) == ("same", None)
    assert parity.run(_case("SELECT 1 WHERE FALSE", 1)) == ("differs", None)
    assert parity.run(_case("SELECT 2, 3", 2)) == ("same", 2)
    assert parity.run(_case("SELECT 2, 3", 3)) == ("differs", 2)


def test_the_docs_table_lists_each_case_that_differs_then_names_the_rest():
    text = parity.table_markdown()
    others = [c for c in parity.CASES if c.outcome != "same"]
    same = [c for c in parity.CASES if c.outcome == "same"]
    assert others and same
    head, rest = text.split("\n\n", 1)
    assert head == (f"Of {len(parity.CASES)} checks on commonly used Snowflake functions, against the values "
                    "Snowflake's SQL reference documents (no Snowflake account was used), these don't answer the same "
                    "way on this PC.")
    table, tail = rest.split("\n\n")
    lines = table.split("\n")
    assert lines[:2] == ["| Function | Group | On this PC | What to know |", "| --- | --- | --- | --- |"]
    words = {"differs": "Different result", "error": "Doesn't run", "untranslated": "Runs only in Snowflake"}
    assert lines[2:] == [f"| `{c.function}` | {c.group} | {words[c.outcome]} | {c.note} |" for c in others]
    assert tail == (f"These {len(same)} checks answer as Snowflake documents: "
                    + ", ".join(f"`{c.function}`" for c in same) + ".")


def test_the_docs_table_words_each_outcome(monkeypatch):
    cases = (_case("SELECT 1", 1), parity.Case("A", "G1", "SELECT 1", 1, "differs", "s", "n1"),
             parity.Case("B", "G2", "SELECT 1", 1, "error", "s", "n2"),
             parity.Case("C", "G3", "SELECT 1", 1, "untranslated", "s", "n3"))
    monkeypatch.setattr(parity, "CASES", cases)
    assert parity.table_markdown().split("\n")[4:7] == [
        "| `A` | G1 | Different result | n1 |", "| `B` | G2 | Doesn't run | n2 |",
        "| `C` | G3 | Runs only in Snowflake | n3 |"]
    assert parity.table_markdown().endswith("These 1 checks answer as Snowflake documents: `F`.")


@pytest.mark.parametrize("function, here", [
    ("DATASKETCHES_HLL (docs example)", 4.0), ("APPROX_PERCENTILE (docs example, 0.1)", 1.0),
    ("APPROX_PERCENTILE (docs example, 0.5)", 5.0), ("APPROX_PERCENTILE (docs example, 0.999)", 10.0)])
def test_an_estimate_that_differs_gives_the_value_its_note_names(function, here):
    case = _named(function)
    outcome, value = parity.run(case, duckdb.connect())
    assert outcome == "differs" and value == here
    assert f"gives {here}" in case.note or f"is {here}" in case.note
