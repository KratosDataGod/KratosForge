"""Databricks SQL engine paths that the uncapped mutation run (batch 175, docs/quality/mutation-testing.md)
showed no test checked. Fakes only."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_sql as dbsql

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
TOKEN = "eyJ" + "hbGciOiJSUzI1NiJ9" + ".e30." + "QUFBQUFB" * 3


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.content = json.dumps(self._body).encode()
        self.headers = {}

    def json(self):
        return self._body


class Pages:
    """Catalog pages of ``size`` items each, every one with a new next-page token."""

    def __init__(self, size):
        self.size, self.calls = size, 0

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        self.calls += 1
        n = self.calls
        return Resp(200, {"catalogs": [{"name": f"c{n}-{i}"} for i in range(self.size)], "next_page_token": f"t{n}"})


def _client(http):
    return dbsql.Client(dba.TokenProvider(HOST, lambda: (TOKEN, None), "test"), http, sleep=lambda s: None)


def test_a_catalog_list_stops_at_1000_items():
    assert dbsql.CATALOG_LIMIT == 1000
    http = Pages(400)
    items = _client(http).list("/api/2.1/unity-catalog/catalogs", "catalogs", {})
    assert len(items) == 1000 and http.calls == 3
    assert items[-1]["name"] == "c3-199"


def test_a_whole_result_stops_after_10000_chunks():
    assert dbsql.MAX_CHUNKS == 10_000
    asked = []

    def chunk(statement, n):
        asked.append(n)
        return {"data_array": [], "next_chunk_index": n + 1}
    owner = SimpleNamespace(_cancelled=False, _client=SimpleNamespace(chunk=chunk))
    reply = {"statement_id": "s1", "result": {"data_array": [], "next_chunk_index": 1}}
    with pytest.raises(dbsql.DatabricksError, match="more than 10,000 chunks"):
        dbsql.DatabricksSqlSession._all_rows(owner, reply, 10)
    assert len(asked) == 9_999 and asked[-1] == 9_999


def test_a_404_from_an_api_without_its_own_hints_says_to_check_the_workspace_url():
    assert dbsql.refusal_hint(404, "/api/2.0/somewhere/else") == "Check the workspace URL."
    assert dbsql.refusal_hint(403, "/api/2.0/somewhere/else") == "Ask a workspace admin for access."


class _Client:
    """The few calls a session makes when it opens and closes, with nothing running."""

    def __init__(self):
        self.stopped = None

    def warehouse(self, warehouse_id):
        return {"name": "Shared", "state": "RUNNING"}


def _session():
    from fabric_desktop.engines.contract import SessionConfig
    return dbsql.DatabricksSqlSession(SessionConfig("nb", options={}), _Client(), "wh1", sleep=lambda s: None)


def test_a_session_says_whether_it_is_closed():
    s = _session()
    assert s.closed is False
    s.close()
    assert s.closed is True


def test_a_warehouse_session_has_no_variables_to_list():
    from fabric_desktop.engines.contract import Unsupported
    with pytest.raises(Unsupported, match="holds tables, not variables"):
        _session().variables()


def test_errors_that_hold_each_other_are_each_looked_at_once():
    a, b = OSError("a"), OSError("b")
    a.__context__, b.__context__ = b, a  # a cycle: the walk must still end
    assert dbsql._never_sent(a) is False
    c = RuntimeError("wrapped")
    c.__cause__ = ConnectionRefusedError()
    c.__context__ = c
    assert dbsql._never_sent(c) is True


def test_a_refusals_message_from_the_workspace_is_kept():
    text = dbsql.Client._refusal(Resp(403, {"message": "No access to wh1", "error_code": "PERMISSION_DENIED"}),
                                 "/api/2.0/sql/warehouses/wh1")
    assert text == "Databricks refused the request (HTTP 403). No access to wh1 Ask for CAN USE on the SQL warehouse."
    assert dbsql.Client._refusal(Resp(403, ["not", "an", "object"]), "/x") == \
        "Databricks refused the request (HTTP 403). Ask a workspace admin for access."


def test_a_statement_without_a_result_says_how_long_it_took():
    from fabric_desktop.engines.contract import CellRequest

    class Done(_Client):
        def submit(self, body):
            return {"statement_id": "s1", "status": {"state": "SUCCEEDED"}}
    from fabric_desktop.engines.contract import SessionConfig
    s = dbsql.DatabricksSqlSession(SessionConfig("nb", options={}), Done(), "wh1", sleep=lambda x: None)
    result = s.execute(CellRequest("CREATE TABLE t (a INT)", language="sql"))
    assert result.status == "ok" and 0 <= result.seconds < 60
    assert result.outputs[0]["text"] == f"Done in {result.seconds:.2f} s on Shared."


@pytest.mark.parametrize("status, path, hint", [
    (403, "/api/2.1/unity-catalog/metastore_summary", "Ask for access to the workspace's Unity Catalog metastore."),
    (404, "/api/2.1/unity-catalog/tables", "Check the catalog and schema names."),
    (404, "/api/2.0/preview/scim/v2/Me", "Check the workspace URL."),
    (403, "/api/2.0/preview/scim/v2/Me", "Your sign-in can't read its own user details in this workspace; ask a workspace admin."),
])
def test_each_api_names_what_to_check(status, path, hint):
    assert dbsql.refusal_hint(status, path) == hint


class _Script:
    """Answers each request with the next of ``answers`` (the last one repeats)."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        self.calls += 1
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def test_a_client_on_its_own_tries_a_throttled_read_again():
    http = _Script(Resp(429, {"message": "busy"}), Resp(200, {"id": "wh1", "name": "Shared"}))
    assert _client(http).warehouse("wh1")["name"] == "Shared" and http.calls == 2


def test_a_400_is_a_refusal():
    with pytest.raises(dbsql.DatabricksError) as exc:
        _client(_Script(Resp(400, {"message": "bad request"}))).warehouse("wh1")
    assert exc.value.status == 400 and "HTTP 400" in str(exc.value)


def test_a_statement_cancelled_from_here_says_cancelled():
    class Cancelled(_Client):
        def __init__(self):
            super().__init__()
            self.cancelled = []

        def submit(self, body):
            return {"statement_id": "s1", "status": {"state": "CANCELED"}}

        def cancel(self, statement_id):
            self.cancelled.append(statement_id)
    from fabric_desktop.engines.contract import SessionConfig
    client = Cancelled()
    s = dbsql.DatabricksSqlSession(SessionConfig("nb", options={}), client, "wh1", sleep=lambda x: None)
    s._cancelled = True
    with pytest.raises(dbsql.DatabricksError) as exc:
        s._run_one("SELECT 1")
    assert str(exc.value) == "Cancelled." and client.cancelled == ["s1"]


def test_a_catalog_list_of_exactly_1000_items_asks_no_further_page():
    http = Pages(500)
    assert len(_client(http).list("/api/2.1/unity-catalog/catalogs", "catalogs", {})) == 1000 and http.calls == 2


def test_a_missing_metastore_is_named_from_its_error_code():
    text = dbsql.Client._refusal(Resp(404, {"error_code": "METASTORE_DOES_NOT_EXIST"}), "/api/2.1/unity-catalog/catalogs")
    assert text.endswith("This workspace has no Unity Catalog metastore assigned; an account admin assigns one.")


class _Recording(_Script):
    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        self.sent = getattr(self, "sent", []) + [{"method": method, "json": json, "params": params}]
        return super().request(method, url, headers, timeout, json, params)


def test_a_read_sends_its_parameters():
    http = _Recording(Resp(200, {}))
    _client(http).get("/api/2.1/unity-catalog/schemas", {"catalog_name": "main"})
    assert http.sent[0]["params"] == {"catalog_name": "main"}


def test_workspace_writes_that_may_have_landed_are_not_sent_again():
    for write in (lambda c: c.workspace_import({"path": "/x"}), lambda c: c.workspace_delete("/x")):
        http = _Recording(Resp(503, {}), Resp(200, {}))
        with pytest.raises(dbsql.DatabricksError):
            write(_client(http))
        assert http.calls == 1
    http = _Recording(Resp(503, {}), Resp(200, {}))
    _client(http).workspace_mkdirs("/x")  # making a folder twice does no harm: tried again
    assert http.calls == 2


def test_a_workspace_delete_never_deletes_a_folders_contents():
    http = _Recording(Resp(200, {}))
    _client(http).workspace_delete("/Users/me/nb")
    assert http.sent[0]["json"] == {"path": "/Users/me/nb", "recursive": False}


def test_a_submit_answered_with_500_may_have_started():
    for status, started in ((500, True), (499, False)):
        with pytest.raises(dbsql.DatabricksError) as exc:
            _client(_Recording(Resp(status, {}))).submit({"statement": "SELECT 1"})
        assert ("may have started" in str(exc.value)) is started, status


def test_a_failed_statements_message_is_kept_to_1000_characters():
    from fabric_desktop import redact

    class Failed(_Client):
        def submit(self, body):
            return {"statement_id": "s1", "status": {"state": "FAILED", "error": {"message": "e" * 1500}}}
    from fabric_desktop.engines.contract import SessionConfig
    s = dbsql.DatabricksSqlSession(SessionConfig("nb", options={}), Failed(), "wh1", sleep=lambda x: None)
    with pytest.raises(dbsql.DatabricksError) as exc:
        s._run_one("SELECT 1")
    assert str(exc.value) == redact.one_line("e" * 1500, 1000) != redact.one_line("e" * 1500, 1001)


def test_a_whole_result_too_large_says_how_long_it_took():
    from fabric_desktop.engines.contract import CellRequest, SessionConfig

    class Many(_Client):
        def submit(self, body):
            return {"statement_id": "s1", "status": {"state": "SUCCEEDED"},
                    "manifest": {"schema": {"columns": [{"name": "n", "type_name": "INT"}]}},
                    "result": {"data_array": [["1"], ["2"], ["3"]]}}
    s = dbsql.DatabricksSqlSession(SessionConfig("nb", options={}), Many(), "wh1", sleep=lambda x: None)
    result = s.execute(CellRequest("SELECT n FROM t", language="sql", result_rows=2))
    assert result.status == "error" and result.outputs[0]["ename"] == "ResultTooLarge"
    assert 0 <= result.seconds < 60
