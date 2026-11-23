"""Databricks SQL parity: the path the other tests leave out (coverage pass 20). A case this PC can't
even read is recorded as untranslated with the reason, and nothing is run."""

from __future__ import annotations

from fabric_desktop.engines import databricks_sql_parity as parity


class NoConnection:
    def execute(self, sql):   # pragma: no cover - reached only if the case were run
        raise AssertionError("an untranslated case must not run")


def test_an_empty_case_is_untranslated_with_the_reason_and_never_run():
    case = parity.Case("nothing", "Test", "", None, "untranslated")
    assert parity.run(case, NoConnection()) == ("untranslated", "ValueError: The query is empty.")


def test_a_case_sqlglot_cant_read_is_untranslated_and_names_the_error_type():
    outcome, value = parity.run(parity.Case("broken", "Test", "SELECT 1 FROM", None, "untranslated"), NoConnection())
    assert outcome == "untranslated"
    assert value.startswith("ValueError: Could not read the query: ")
