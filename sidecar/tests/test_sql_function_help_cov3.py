"""sql_function_help.py's DuckDB forms: a function that takes any number of arguments, and the same form
listed twice by the catalog (shown once). DuckDB's catalog is a stand-in here, so the rows are exact."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import sql_function_help as h


class _Con:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params):
        assert params == ["kf_fake_fn"]
        return self

    def fetchall(self):
        return self.rows


@pytest.fixture
def catalog(monkeypatch):
    h._duckdb.cache_clear()
    yield lambda rows: monkeypatch.setattr(duckdb, "connect", lambda *_a, **_k: _Con(rows))
    h._duckdb.cache_clear()


def test_varargs_and_repeated_forms(catalog):
    catalog([
        (["a"], ["VARCHAR"], "VARCHAR", "ANY", "Joins  its\n arguments."),
        (["a"], ["VARCHAR"], "VARCHAR", "ANY", "Joins its arguments."),   # the same form again
        (["x", "y"], ["ANY"], None, None, ""),                            # fewer types than names
        (None, None, "BIGINT", None, ""),
    ])
    out = h.function_help("duckdb", "KF_FAKE_FN")
    assert out == {"name": "kf_fake_fn", "more": 0, "forms": [
        {"signature": "kf_fake_fn(a VARCHAR, ANY...) → VARCHAR", "summary": "Joins its arguments."},
        {"signature": "kf_fake_fn(x, y)", "summary": ""},
        {"signature": "kf_fake_fn() → BIGINT", "summary": ""},
    ]}


def test_a_real_varargs_function_lists_its_dots():
    h._duckdb.cache_clear()
    out = h.function_help("duckdb", "concat")
    assert out is not None and any("..." in f["signature"] for f in out["forms"])
