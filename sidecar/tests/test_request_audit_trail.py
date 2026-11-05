"""#1350: one request id finds every audit row about the request, from sent to decided.
Local platform only: nothing here reaches Fabric."""

import json

from fabric_desktop.central.client import LocalPlatform

ADMIN, DEV = "chris@example.com", "dev@example.com"


def _rows(p, request_id):
    out = []
    for a in p.call("audit", limit=100):
        detail = json.loads(a["detail"]) if a.get("detail") else {}
        if isinstance(detail, dict) and detail.get("request") == request_id:
            out.append(a["action"])
    return list(reversed(out))  # oldest first


def test_one_request_id_finds_its_whole_trail(tmp_path):
    admin = LocalPlatform(tmp_path / "p.sqlite", lambda: ADMIN)
    admin.call("whoami")
    dev = LocalPlatform(admin.path, lambda: DEV)
    req = dev.call("request", kind="source", target="erp", reason="needed")
    admin.call("record_request_progress", id=req["id"], made={"copy_job_id": "00000000-0000-4000-8000-000000000001"})
    admin.call("decide_request", id=req["id"], approve=False, note="not now")
    assert _rows(admin, req["id"]) == ["request source", "made for source request", "declined source"]


def test_asking_again_after_a_decline_is_a_separate_trail(tmp_path):
    admin = LocalPlatform(tmp_path / "p.sqlite", lambda: ADMIN)
    admin.call("whoami")
    dev = LocalPlatform(admin.path, lambda: DEV)
    first = dev.call("request", kind="source", target="erp", reason="needed")
    admin.call("decide_request", id=first["id"], approve=False)
    second = dev.call("request", kind="source", target="erp", reason="needed now")
    dev.call("withdraw_request", id=second["id"])
    assert _rows(admin, first["id"]) == ["request source", "declined source"]
    assert _rows(admin, second["id"]) == ["request source", "withdrew source"]


def test_the_sent_row_keeps_the_reason_off_the_audit(tmp_path):
    admin = LocalPlatform(tmp_path / "p.sqlite", lambda: ADMIN)
    admin.call("whoami")
    req = LocalPlatform(admin.path, lambda: DEV).call("request", kind="source", target="erp", reason="a private reason")
    sent = next(a for a in admin.call("audit", limit=10) if a["action"] == "request source")
    assert json.loads(sent["detail"]) == {"request": req["id"], "project": ""}
