"""The Databricks SQL engine: the paths the other tests leave out (coverage pass 20). The app's own
HTTP session by default, a refused answer closed before the call is sent again with a new token, a
connection failure whose reason is text, a submit that comes back with no statement ID, and a result
that can't be read when nothing listens for outputs."""

from __future__ import annotations

from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines.contract import CellRequest
from tests.test_databricks_sql_mutation import HOST, TOKEN_A, TOKEN_B, WAREHOUSE, Http, Resp, client, done, session


def test_without_an_http_object_the_client_uses_the_apps_shared_session(monkeypatch):
    from fabric_desktop import http_session

    shared = object()
    monkeypatch.setattr(http_session, "session", lambda: shared)
    c = dbsql.Client(dba.TokenProvider(HOST, lambda: (TOKEN_A, None), "test"))
    assert c._http is shared and c.host == HOST


class Closable(Resp):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.closed = False

    def close(self):
        self.closed = True


def test_a_refused_answer_is_closed_before_the_call_goes_again_with_a_new_token():
    issued = [TOKEN_A, TOKEN_B]
    provider = dba.TokenProvider(HOST, lambda: (issued[0], None), "test")
    real_drop = provider.drop

    def drop(token):
        real_drop(token)
        if issued[0] == token and len(issued) > 1:
            issued.pop(0)
    provider.drop = drop
    refused = Closable(401, {"message": "expired"})
    http = Http({"/api/2.0/sql/warehouses/wh1": [refused, WAREHOUSE]})
    assert client(http, provider).warehouse("wh1")["name"] == "Shared"
    assert refused.closed
    assert [c["token"] for c in http.calls] == [f"Bearer {TOKEN_A}", f"Bearer {TOKEN_B}"]


def test_a_failure_whose_reason_is_text_is_followed_through_its_causes():
    class Failure(Exception):
        reason = "name or service not known"   # urllib3 sometimes gives a reason as text

    plain = Failure("could not resolve")
    assert dbsql._never_sent(plain) is False
    refused = Failure("wrapped")
    refused.__cause__ = ConnectionRefusedError()
    assert dbsql._never_sent(refused) is True


def test_a_submit_answered_without_a_statement_id_is_an_error_saying_to_check_history():
    http = Http({"/api/2.0/sql/warehouses/wh1": [WAREHOUSE],
                 "/api/2.0/sql/statements": [Resp(200, {"status": {"state": "PENDING"}})]})
    s = session(http)
    result = s.execute(CellRequest("SELECT 1", "sql"))
    assert result.status == "error"
    assert result.outputs[-1]["evalue"].startswith("The warehouse took the statement but sent no statement ID")
    assert "Check Databricks' query history" in result.outputs[-1]["evalue"]
    assert [c["path"] for c in http.calls].count("/api/2.0/sql/statements") == 1   # never sent twice


def test_a_finished_statement_without_an_id_is_still_read():
    reply = done([["7"]])
    reply.pop("statement_id")
    http = Http({"/api/2.0/sql/warehouses/wh1": [WAREHOUSE], "/api/2.0/sql/statements": [Resp(200, reply)]})
    result = session(http).execute(CellRequest("SELECT 7", "sql"))
    assert result.status == "ok" and result.rows == 1


def test_an_unreadable_result_is_the_cells_error_even_with_nothing_listening():
    http = Http({"/api/2.0/sql/warehouses/wh1": [WAREHOUSE],
                 "/api/2.0/sql/statements": [Resp(200, done(external_links=[{"external_link": "https://x"}]))]})
    result = session(http).execute(CellRequest("SELECT 1", "sql"), on_output=None)
    assert result.status == "error" and len(result.outputs) == 1
    assert result.outputs[0]["ename"] == "DatabricksError"
    assert "download links" in result.outputs[0]["evalue"]


def test_a_submit_answer_whose_status_isnt_an_object_is_treated_as_no_status():
    http = Http({"/api/2.0/sql/warehouses/wh1": [WAREHOUSE],
                 "/api/2.0/sql/statements": [Resp(200, {"status": "SUCCEEDED"})]})
    result = session(http).execute(CellRequest("SELECT 1", "sql"))
    assert result.status == "error"   # no ID and no state it can trust: not followed, not read
    assert result.outputs[-1]["evalue"].startswith("The warehouse took the statement but sent no statement ID")
