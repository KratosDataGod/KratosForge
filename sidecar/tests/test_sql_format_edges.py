"""Format SQL: when sqlglot itself fails part way, the query is left as it was and says why."""

import pytest
from sqlglot.errors import SqlglotError

from fabric_desktop import sql_format as sf


def test_t_sql_that_cant_be_tokenized_still_gets_the_t_sql_layout(monkeypatch):
    class Broken:
        def tokenize(self, query):
            raise SqlglotError("bad token")

    monkeypatch.setattr(sf, "TSQL", Broken)
    assert sf._layout_dialect("SELECT 1", "tsql") is sf._TSQLLayout


def test_a_statement_sqlglot_cant_write_back_leaves_the_query_alone(monkeypatch):
    class Statement:
        def sql(self, dialect, pretty):
            raise SqlglotError("Unsupported expression")

    monkeypatch.setattr(sf.sqlglot, "parse", lambda query, read: [Statement()])
    out = sf.format_sql("select 1", "duckdb")
    assert out == {"sql": "select 1", "changed": False,
                   "warning": "Format SQL left the query as it was: Unsupported expression"}


@pytest.mark.parametrize("exc, expected", [(SqlglotError("first\nsecond"), "first"), (SqlglotError(""), "SqlglotError")])
def test_an_error_without_details_reads_as_its_first_line_or_its_name(exc, expected):
    assert sf._first_line(exc) == expected


def test_formatting_that_would_not_parse_back_is_named(monkeypatch):
    real = sf.sqlglot.parse

    def parse(query, read):
        if query == "after":
            raise SqlglotError("no")
        return real(query, read=read)

    monkeypatch.setattr(sf.sqlglot, "parse", parse)
    assert sf._difference("SELECT 1", "after", "duckdb") == "produce SQL that doesn't parse"
