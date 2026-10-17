"""Redaction (FD-131): the shared cases the UI is tested against too, and where it's applied."""

import json
import logging
from pathlib import Path

import pytest

from fabric_desktop import logs, redact, server

CASES = json.loads((Path(__file__).resolve().parents[2] / "app" / "tests" / "redact-cases.json").read_text())["cases"]
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
