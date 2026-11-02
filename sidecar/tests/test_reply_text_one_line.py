"""Text from a service's reply goes into a message as one short line, with no token in it."""

import json

from fabric_desktop import onelake
from fabric_desktop.central import client
from fabric_desktop.engines import databricks_sql as dbsql

TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLXNpZ25hdHVyZQ"
FORGED = "refused\nERROR: a line of its own\x1b[0m " + "z" * 2000 + f" Bearer {TOKEN}"


class Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.content = self.text.encode()
        self.reason = ""

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not JSON")
        return self._body


class Session:
    def __init__(self, resp):
        self.resp = resp

    def get(self, *a, **k):
        return self.resp

    def post(self, *a, **k):
        return self.resp


def one_short_line(text, most):
    assert "\n" not in text and "\x1b" not in text and TOKEN not in text
    assert len(text) <= most, len(text)


def test_a_databricks_refusal_and_a_failed_statement_are_one_short_line():
    one_short_line(dbsql.Client._refusal(Resp(400, {"message": FORGED}), "/api/2.0/sql/statements"), 600)


def test_a_onelake_probe_answer_is_one_short_line_whatever_its_shape():
    for body in ({"error": {"code": "Forbidden", "message": FORGED}}, {"error": FORGED}, [FORGED], FORGED):
        store = onelake.FileStore(lambda: "t", session=Session(Resp(403, body)), sleep=lambda s: None)
        status, message = store.probe("ws", "lh", "Files")
        assert status == 403
        one_short_line(message, 300)


def test_platform_errors_and_a_gateway_page_are_one_short_line():
    for body in ({"errors": [{"message": FORGED}]}, FORGED):
        platform = client.FabricPlatform(lambda: "t", "ws", "fn", session=Session(Resp(400, body)), sleep=lambda s: None)
        try:
            platform._answer("next_keys", Resp(400, body))
        except client.core.PlatformError as exc:
            one_short_line(str(exc), 400)
        else:
            raise AssertionError("expected a PlatformError")
