"""Incident desk failure paths no other test reaches: a Teams notice that was posted but whose note
wasn't written, and a history copy that fails halfway through writing."""

import types

import pytest

from fabric_desktop import incident_desk
from fabric_desktop.service import Service

ME = "chris@contoso.com"
PEOPLE = [{"upn": "olu@contoso.com"}]


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    s = Service(root=tmp_path / "app")
    s._auth = types.SimpleNamespace(
        signed_in_user=lambda: ME,
        silent_token=lambda scopes: None,
        fabric_token=lambda: None,
        storage_token=lambda: None,
    )
    s.meta_init(str(tmp_path / "proj"), "3")
    return s


def test_a_notice_posted_before_whose_note_was_never_written_gets_its_note_now(
    svc, monkeypatch
):
    inc = svc.incident_report("dev", "Orders late", workload="meta:sales:silver.orders")
    monkeypatch.setattr(
        svc,
        "incident_impact",
        lambda env, id: {
            "available": True,
            "people": PEOPLE,
            "products": [{"name": "orders"}],
        },
    )
    monkeypatch.setattr(
        svc,
        "platform_notify",
        lambda events: {"events": [{"result": "skipped", "reason": "already posted"}]},
    )
    out = svc.incident_tell_impacted("dev", inc["id"], ["olu@contoso.com"])
    assert out == {"told": False, "reason": "already posted", "people": PEOPLE}
    notes = [n["text"] for n in svc._incident("dev", inc["id"])["notes"]]
    assert notes == ["Told downstream in Teams: olu@contoso.com"]
    svc.incident_tell_impacted(
        "dev", inc["id"], ["olu@contoso.com"]
    )  # and it isn't written twice
    assert len(svc._incident("dev", inc["id"])["notes"]) == 1


def test_a_history_copy_that_fails_halfway_marks_nothing_as_sent_so_the_next_copy_sends_it_all(
    svc, monkeypatch
):
    svc.incident_report("dev", "Revenue looks doubled", priority="P2")
    monkeypatch.setattr(
        svc,
        "central_lakehouse",
        lambda: {
            "workspace_id": "w1",
            "lakehouse_id": "l1",
            "lakehouse_name": "Central",
        },
    )
    monkeypatch.setattr(
        svc, "central_family", lambda family, approve=False: ("lake", "me")
    )
    written: list[tuple[str, int]] = []
    fail = {"incident_snapshot": OSError("the connection dropped")}

    def write(lake, me, table, rows, family):
        if table in fail:
            raise fail.pop(table)
        written.append((table, len(rows)))

    monkeypatch.setattr(incident_desk.setup, "write", write)
    with pytest.raises(OSError, match="connection dropped"):
        svc.incidents_history_sync()
    assert written == [("incident_event", 1)]  # the events went; the snapshot didn't
    out = svc.incidents_history_sync()
    dev = next(e for e in out["envs"] if e["env"] == "dev")
    assert dev == {
        "env": "dev",
        "state": "copied",
        "events": 1,
        "snapshots": 1,
    }  # the table's MERGE skips the repeat
    again = next(e for e in svc.incidents_history_sync()["envs"] if e["env"] == "dev")
    assert again["events"] == 0 and again["snapshots"] == 0
