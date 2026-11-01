"""Redaction (FD-131): the shared cases the UI is tested against too, and where it's applied."""

import json
import logging
from pathlib import Path

import pytest

from fabric_desktop import logs, redact, server

CASES = [c | {"in": "".join(c["in"]) if isinstance(c["in"], list) else c["in"]}
         for c in json.loads((Path(__file__).resolve().parents[2] / "app" / "tests" / "redact-cases.json").read_text())["cases"]]
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["in"][:40])
def test_shared_cases(case):
    fn = redact.secrets if case["level"] == "secrets" else redact.for_support
    assert fn(case["in"]) == case["out"]


def test_errors_sent_to_the_ui_carry_no_tokens():
    class Service:
        def list_workspaces(self):
            raise RuntimeError(f"GET failed, headers Authorization: Bearer {JWT}")
    reply = server.handle(Service(), json.dumps({"id": 1, "method": "list_workspaces"}))
    assert JWT not in reply["error"] and "[removed]" in reply["error"]
    assert reply["error"].startswith("RuntimeError: GET failed")


def test_agent_log_lines_carry_no_tokens():
    fmt = logs.JsonFormatter("w1")
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "token %s", (JWT,), None)
    record.fields = {"url": "https://a/b?sig=SECRET"}
    try:
        raise ValueError(f"client_secret=abc {JWT}")
    except ValueError:
        import sys
        record.exc_info = sys.exc_info()
    line = fmt.format(record)
    assert JWT not in line and "SECRET" not in line and "client_secret=abc" not in line
    assert json.loads(line)["message"] == "token [removed]"


def _log_line(message, **extra):
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "%s", (message,), None)
    record.fields = extra
    return logs.JsonFormatter("w1").format(record)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["in"][:40])
def test_shared_cases_in_agent_log_lines_stay_valid_json(case):
    # Redacted value by value before JSON escapes quotes, so "password": "x" still matches.
    expected = redact.secrets(case["in"])
    if case["level"] == "secrets":
        assert expected == case["out"]
    line = json.loads(_log_line(case["in"], detail=case["in"], nested={"items": [case["in"]]}))
    assert line["message"] == line["detail"] == line["nested"]["items"][0] == expected


@pytest.mark.parametrize("message, secret", [
    ('conn "password": "hunter2"', "hunter2"),
    ('client_secret=s3cr3tvalue"', "s3cr3tvalue"),
])
def test_agent_log_lines_with_quotes_leak_nothing(message, secret):
    raw = _log_line(message, detail=message)
    assert secret not in raw
    line = json.loads(raw)
    assert line["message"] == line["detail"] == redact.secrets(message)


def test_agent_log_fields_named_like_secrets_are_blanked():
    raw = _log_line("connecting", password="plain words", job_id="j1", attempt=2,
                    conn={"Connection_String": "Server=x;Pwd2=y", "API-Key": "k1", "secrets_ignored": 3})
    line = json.loads(raw)
    assert "plain words" not in raw and "Server=x" not in raw and "k1" not in raw
    assert line["password"] == line["conn"]["Connection_String"] == line["conn"]["API-Key"] == "[removed]"
    assert line["job_id"] == "j1" and line["attempt"] == 2 and line["conn"]["secrets_ignored"] == 3


def test_support_bundle_holds_versions_and_logs_with_personal_details_taken_out(tmp_path, monkeypatch):
    import zipfile

    from fabric_desktop import cli
    data = tmp_path / "la" / "FabricDesktop"
    data.mkdir(parents=True)
    (data / "sidecar.log").write_text(
        "\n".join(f"line {i}" for i in range(3000)) +
        f"\nTraceback in C:\\Users\\ann.lee\\x.py for ann@contoso.com Bearer {JWT} workspace "
        "11111111-2222-3333-4444-555555555555\n")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    cli.main(["support-bundle", "--out", str(tmp_path / "out")])
    [bundle] = list((tmp_path / "out").glob("fabric-desktop-support-*.zip"))
    with zipfile.ZipFile(bundle) as z:
        names = set(z.namelist())
        log = z.read("sidecar.log").decode()
        about = json.loads(z.read("about.json"))
        contents = z.read("CONTENTS.txt").decode()
    assert names == {"about.json", "sidecar.log", "CONTENTS.txt"}
    assert "line 999" not in log and "line 2999" in log  # only the last lines
    for leaked in ("ann.lee", "ann@contoso.com", JWT, "11111111-2222"):
        assert leaked not in log
    assert about["settings"]["client_id"]["value"] == "Microsoft's Fabric sign-in"
    assert about["settings"]["channel"] == {"value": "stable", "source": "default"}
    assert "agent.log" not in contents


def test_describe_is_log_safe():
    assert redact.describe(ValueError(f"refused Bearer {JWT}")) == "ValueError: refused Bearer [removed]"
    assert redact.describe(KeyError()) == "KeyError"


def test_describe_and_one_line_keep_text_from_a_file_or_reply_to_one_short_line():
    forged = "bad name\nERROR: forged line\r\x1b[31m\tend"
    assert redact.describe(ValueError(forged)) == "ValueError: bad name ERROR: forged line [31m end"
    assert redact.one_line(f"Bearer {JWT}\nnext") == "Bearer [removed] next"
    long = redact.describe(RuntimeError("x" * 5000))
    assert len(long) == len("RuntimeError: ") + redact.DESCRIBE_MAX and long.endswith("…")
    assert redact.one_line("y" * 400) == "y" * 299 + "…"
    assert redact.one_line({"code": "Bad"}) == "{'code': 'Bad'}"


G1, G2, G3 = ("11111111-2222-3333-4444-555555555555", "72f988bf-86f1-41af-91ab-2d7cd011db47",
              "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


@pytest.mark.parametrize("url", [
    f"https://contoso.webhook.office.com/webhookb2/{G1}@{G2}/IncomingWebhook/0123456789abcdef/{G3}",
    f"https://contoso.webhook.office.com/webhookb2/{G1}@{G2}/IncomingWebhook/0123456789abcdef/{G3}/V2xyzTOKEN",
    f"https://outlook.office.com/webhook/{G1}@{G2}/IncomingWebhook/0123456789abcdef/{G3}",
    f"https://Outlook.Office365.com/webhook/{G1}@{G2}/IncomingWebhook/0123456789abcdef/{G3}",
])
def test_office_365_connector_webhooks_are_removed(url):
    # QA-016: the address is the credential, so none of its path may survive.
    assert redact.secrets(f"POST {url} failed: 400") == "POST [removed] failed: 400"
    body = json.dumps({"channel": "alerts", "url": url, "status": 400})
    assert redact.secrets(body) == json.dumps({"channel": "alerts", "url": "[removed]", "status": 400})
    assert "IncomingWebhook" not in redact.for_support(f"Teams said no ({url}).")


@pytest.mark.parametrize("text", [
    "open https://outlook.office.com/mail/inbox or https://www.office.com/launch/word",
    "docs at https://learn.microsoft.com/office/webhooks and https://portal.office.com/",
])
def test_office_urls_without_a_webhook_path_are_kept(text):
    assert redact.secrets(text) == text


def test_a_failed_webhook_post_does_not_leak_the_path():
    # QA-016: requests names the host and the path apart, so the full-URL pattern never sees the URL.
    import requests
    path = ("/webhookb2/11111111-2222-3333-4444-555555555555@66666666-7777-8888-9999-000000000000/IncomingWebhook/"
            "abcdef0123456789abcdef0123456789/11111111-2222-3333-4444-555555555555/V2SECRETSECRET0123456789")
    with pytest.raises(requests.RequestException) as caught:
        requests.post("https://127.0.0.1:9" + path, json={}, timeout=2)  # refused: the same message shape as DNS or network failures
    text = redact.describe(caught.value)
    assert "SECRETSECRET" not in text and "IncomingWebhook" not in text
    assert "127.0.0.1" in text  # the rest of the message still says what failed


def test_the_outbox_stores_a_failed_legacy_webhook_post_without_its_secret():
    # notify.failure is what the Teams outbox saves in its error column after a retry fails.
    import requests
    from fabric_desktop.workloads import notify
    path = ("/webhook/11111111-2222-3333-4444-555555555555@66666666-7777-8888-9999-000000000000/IncomingWebhook/"
            "abcdef0123456789abcdef0123456789/11111111-2222-3333-4444-555555555555")
    with pytest.raises(requests.RequestException) as caught:
        requests.post("https://127.0.0.1:9" + path, json={}, timeout=2)
    stored = notify.failure(caught.value)
    assert "abcdef0123456789" not in stored and "IncomingWebhook" not in stored
