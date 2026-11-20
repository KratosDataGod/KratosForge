"""The Databricks SQL client at its exact edges (boundary re-score of batch 13, mutation batch 159,
docs/quality/mutation-testing.md): an HTTP 400, a 500 to a statement, and a list that reaches exactly
CATALOG_LIMIT. Fakes only."""

from __future__ import annotations

import pytest

from fabric_desktop.engines import databricks_sql as dbsql
from tests.test_databricks_sql_mutation import Http, Resp, client

PATH = "/api/2.1/unity-catalog/catalogs"


def test_an_http_400_is_a_refusal():
    http = Http({PATH: [Resp(400, {"message": "bad request"})]})
    with pytest.raises(dbsql.DatabricksError) as exc:
        client(http)._call("GET", PATH)
    assert exc.value.status == 400


def test_a_500_to_a_statement_may_have_started_it():
    http = Http({"/api/2.0/sql/statements": [Resp(500, {"message": "oops"})]})
    with pytest.raises(dbsql.DatabricksError, match="may have started on the warehouse"):
        client(http).submit({"statement": "SELECT 1", "warehouse_id": "wh1"})


def test_a_list_stops_once_it_has_exactly_catalog_limit_items(monkeypatch):
    monkeypatch.setattr(dbsql, "CATALOG_LIMIT", 2)
    http = Http({PATH: [Resp(200, {"catalogs": [{"name": "a"}, {"name": "b"}], "next_page_token": "t1"}),
                        Resp(200, {"catalogs": [{"name": "c"}]})]})
    assert [c["name"] for c in client(http).list(PATH, "catalogs", {})] == ["a", "b"]
    assert len(http.calls) == 1
