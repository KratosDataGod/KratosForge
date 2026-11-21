"""The app's side of running cells from VS Code: the edges the main tests don't reach. The policy
turning integrations off, a policy change noticed by the app's own calls, answering a write that
stopped waiting, every waiting write denied when it's switched off, calls while it's off, a run
that fails, a notebook that can't be read, and a write nobody answers."""

from __future__ import annotations

import json
import threading
import time

import pytest
from test_kernel_sessions import svc  # noqa: F401

from fabric_desktop import app_settings
from fabric_desktop import editor_service as es

OFF = "VS Code can't run notebooks in KratosForge: it's switched off"


def policy(tmp_path, monkeypatch, rules):
    path = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rules), encoding="utf-8")
    monkeypatch.setattr(app_settings, "policy_path", lambda: path)


def test_integrations_turned_off_by_policy_say_so(svc, tmp_path, monkeypatch):  # noqa: F811
    policy(tmp_path, monkeypatch, {"integrations": False})
    assert svc._editor_allowed() == "Your organization has turned integrations off on this PC."
    with pytest.raises(PermissionError, match="turned integrations off"):
        svc.editor_set_enabled(True)


@pytest.mark.parametrize("asked_by", ["editor_status", "editor_pending"])
def test_a_policy_change_while_listening_stops_the_channel_when_the_app_asks(svc, tmp_path, monkeypatch, asked_by):  # noqa: F811
    svc.editor_set_enabled(True)
    assert svc._editor_state()["channel"] is not None
    ask = es._WriteAsk("C:\\repo", "Daily", "LH.dbo.sales")
    svc._editor_state()["asks"][ask.id] = ask
    policy(tmp_path, monkeypatch, {"integrations_editors": False})
    out = getattr(svc, asked_by)()
    assert svc._editor_state()["channel"] is None and out["enabled"] is False
    assert ask.done.is_set() and ask.answer is False  # a write waiting then is denied
    assert not (svc.root / "editor-port").exists()


def test_answering_a_write_that_stopped_waiting_says_so(svc):  # noqa: F811
    with pytest.raises(LookupError, match="isn't waiting any more"):
        svc.editor_decide_write("wq_gone", True)
    ask = es._WriteAsk("w", "nb", "t")
    ask.done.set()
    svc._editor_state()["asks"][ask.id] = ask
    with pytest.raises(LookupError):
        svc.editor_decide_write(ask.id, True)


def test_switching_off_leaves_an_answered_write_as_it_was(svc):  # noqa: F811
    answered = es._WriteAsk("w", "nb", "t1")
    answered.answer = True
    answered.done.set()
    waiting = es._WriteAsk("w", "nb", "t2")
    svc._editor_state()["asks"].update({answered.id: answered, waiting.id: waiting})
    svc.editor_set_enabled(False)
    assert answered.answer is True and waiting.answer is False and waiting.done.is_set()


def test_ending_a_pairing_from_the_app(svc):  # noqa: F811
    svc.editor_set_enabled(True)
    try:
        req = svc.editor_channel_pair(str(svc.root), "test")
        svc.editor_decide_pairing(req["id"], True)
        [row] = svc.editor_pairings.list()
        assert svc.editor_end_pairing(row["id"]) == {"ended": True}
        assert svc.editor_pairings.list() == []
    finally:
        svc.editor_set_enabled(False)


def test_pairing_and_running_while_off_are_refused(svc):  # noqa: F811
    with pytest.raises(PermissionError, match=OFF):
        svc.editor_channel_pair(str(svc.root), "test")
    with pytest.raises(PermissionError, match=OFF):
        svc.editor_channel_run({"id": "p", "window": str(svc.root)}, "x.py", "print(1)")


def test_a_run_that_fails_is_audited_with_its_error_and_raised(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(svc, "_editor_enabled", lambda: True)
    monkeypatch.setattr(svc, "editor_channel_notebook", lambda pairing, path: "Daily")

    def fails(*a, **k):
        raise RuntimeError("kernel didn't start")

    monkeypatch.setattr(svc, "_start_cell", fails)
    audited = []
    monkeypatch.setattr(svc, "_editor_audit", lambda action, params, reply, seconds=0.0: audited.append((action, reply)))
    with pytest.raises(RuntimeError, match="kernel didn't start"):
        svc.editor_channel_run({"id": "p", "window": "C:\\repo"}, "C:\\repo\\Daily.py", "print(1)")
    assert audited == [("editor_run", {"error": "kernel didn't start"})]


def test_a_notebook_that_cant_be_read_attaches_no_lakehouse(svc):  # noqa: F811
    assert svc._editor_lakehouse("no such notebook") is None


def test_a_write_nobody_answers_is_not_written_and_says_why(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(es, "WRITE_WAIT_SECONDS", 0)
    monkeypatch.setattr(svc, "_editor_audit", lambda *a, **k: None)
    check = svc._editor_write_check("C:\\repo", "Daily")
    said = check("LH_Gold", "Files", "out.csv")
    assert said.startswith("Nobody answered in KratosForge within 0 minutes, so LH_Gold/out.csv wasn't written")
    assert svc._editor_state()["asks"] == {}


def test_a_write_answered_yes_is_asked_once_per_table(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(svc, "_editor_audit", lambda *a, **k: None)
    check = svc._editor_write_check("C:\\repo", "Daily")
    asked = []

    def answer():
        deadline = time.monotonic() + 10
        while not svc._editor_state()["asks"] and time.monotonic() < deadline:
            time.sleep(0.01)
        [ask_id] = list(svc._editor_state()["asks"])
        asked.append(ask_id)
        svc.editor_decide_write(ask_id, True)

    t = threading.Thread(target=answer)
    t.start()
    assert check("LH_Gold", "dbo", "sales") is None
    t.join(10)
    assert check("LH_Gold", "dbo", "sales") is None and len(asked) == 1  # not asked again
