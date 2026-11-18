"""Running cells from VS Code (editor_service.py), pinned where mutation testing found the tests
quiet (docs/quality/mutation-testing.md, batch 91): the path checks' exact answers, the five-minute
wait for a write and the words that give it, what each app call returns, the audit records for an
unpair and an approved write, and the channel's background stop."""

from __future__ import annotations

import threading
import time

import pytest
from test_kernel_sessions import svc  # noqa: F401

from fabric_desktop import editor_service as es

_REAL_THREAD = threading.Thread


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


# ---- the path checks


@pytest.mark.parametrize("path, folder", [(None, "/repo"), (7, "/repo"), ("/repo/a.py", ""), ("/repo/a\x00.py", "/repo")])
def test_a_path_that_cant_be_checked_is_never_inside(path, folder):
    assert es._inside(path, folder) is False


def test_a_file_below_the_folder_is_inside_and_the_folder_itself_is_not(tmp_path):
    folder = str(tmp_path / "repo")
    assert es._inside(str(tmp_path / "repo" / "a.py"), folder) is True
    assert es._inside(str(tmp_path / "repo-2" / "a.py"), folder) is False


def test_a_write_ask_has_a_sixteen_digit_id():
    ask = es._WriteAsk("C:\\repo", "Daily", "LH.dbo.sales")
    assert ask.id.startswith("wq_") and len(ask.id) == len("wq_") + 16
    assert ask.view() == {"id": ask.id, "window": "C:\\repo", "notebook": "Daily", "target": "LH.dbo.sales",
                          "at": ask.at}


# ---- the app's calls


def test_nothing_keeps_it_off_when_no_policy_says_so(svc):  # noqa: F811
    assert svc._editor_allowed() == ""


def test_listening_starts_only_when_switched_on(svc):  # noqa: F811
    svc.editor_sync()  # off: no channel starts
    assert svc._editor_state()["channel"] is None
    svc.editor_set_enabled(True)
    try:
        channel = svc._editor_state()["channel"]
        svc.editor_sync()  # already listening: the same channel, not a second one
        assert svc._editor_state()["channel"] is channel
    finally:
        svc.editor_set_enabled(False)


def test_switching_it_on_and_off_says_where_it_stands(svc):  # noqa: F811
    on = svc.editor_set_enabled(True)
    try:
        assert on["enabled"] is True and on["listening"] is True and on["port"]
    finally:
        off = svc.editor_set_enabled(False)
    assert off == {"enabled": False, "blocked": "", "listening": False, "port": None, "pairings": []}


def test_answering_a_write_says_what_was_answered(svc):  # noqa: F811
    for answer in (True, False):
        ask = es._WriteAsk("w", "nb", "t")
        svc._editor_state()["asks"][ask.id] = ask
        assert svc.editor_decide_write(ask.id, answer) == {"approved": answer}
        assert ask.done.is_set() and ask.answer is answer


def test_turned_off_in_the_background_once_on_a_thread_that_never_holds_the_engine_open(svc, monkeypatch):  # noqa: F811
    made = []

    class Thread(_REAL_THREAD):
        def __init__(self, *a, **kw):
            made.append(kw)
            super().__init__(*a, **kw)

    monkeypatch.setattr(es.threading, "Thread", Thread)
    assert svc.editor_channel_open() is False  # off: refused, and the stop starts in the background
    assert svc.editor_channel_open() is False  # once, however many refused calls arrive
    deadline = time.monotonic() + 10
    while svc._editor_state().get("stopping") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert made and all(kw["daemon"] is True and kw["name"] == "editor-off" for kw in made)
    assert svc._editor_state()["stopping"] is False  # done: the next refused call can stop it again


def test_stopping_is_cleared_when_the_stop_has_run(svc):  # noqa: F811
    svc._editor_state()["stopping"] = True
    svc._editor_turned_off()
    assert svc._editor_state()["stopping"] is False


# ---- the channel's calls


def test_unpairing_from_vs_code_ends_the_pairing_and_records_it(svc, monkeypatch):  # noqa: F811
    audited = []
    monkeypatch.setattr(svc, "_editor_audit", lambda action, params, reply, seconds=0.0: audited.append((action, params, reply)))
    ended = []
    monkeypatch.setattr(svc.editor_pairings, "end", lambda pairing_id: ended.append(pairing_id) or True)
    assert svc.editor_channel_unpair({"id": "pr_1", "window": "C:\\repo"}) == {"ended": True}
    assert ended == ["pr_1"]
    assert audited == [("editor_unpair", {"window": "C:\\repo"}, {"result": True})]


def test_interrupting_from_vs_code_returns_what_the_kernel_said(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(svc, "editor_channel_notebook", lambda pairing, path: "Daily")
    monkeypatch.setattr(svc, "interrupt_cell", lambda name: {"interrupted": name})
    audited = []
    monkeypatch.setattr(svc, "_editor_audit", lambda action, params, reply, seconds=0.0: audited.append((action, reply)))
    assert svc.editor_channel_interrupt({"id": "p", "window": "C:\\repo"}, "C:\\repo\\Daily.py") == {"interrupted": "Daily"}
    assert audited == [("editor_interrupt", {"result": {"interrupted": "Daily"}})]


def test_a_run_is_audited_with_how_long_it_took(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(svc, "_editor_enabled", lambda: True)
    monkeypatch.setattr(svc, "editor_channel_notebook", lambda pairing, path: "Daily")
    monkeypatch.setattr(svc, "_editor_lakehouse", lambda name: None)
    monkeypatch.setattr(svc, "_start_cell", lambda code, ref, name, write_check=None: {"version": 1})
    audited = []
    monkeypatch.setattr(svc, "_editor_audit", lambda action, params, reply, seconds=0.0: audited.append(seconds))
    assert svc.editor_channel_run({"id": "p", "window": "C:\\repo"}, "C:\\repo\\Daily.py", "1") == {"version": 1,
                                                                                                   "notebook": "Daily"}
    [seconds] = audited
    assert 0 <= seconds < 60  # the time the run took, not the clock's reading


def test_a_notebooks_lakehouse_is_attached_only_when_it_names_both_ids(svc, monkeypatch):  # noqa: F811
    refs = {"Both": {"workspace_id": "w", "lakehouse_id": "l"}, "One": {"workspace_id": "w"}, "Text": "w/l"}
    monkeypatch.setattr(svc.notebooks, "load", lambda name: {"lakehouse": refs[name]})
    assert svc._editor_lakehouse("Both") == {"workspace_id": "w", "lakehouse_id": "l"}
    assert svc._editor_lakehouse("One") is None
    assert svc._editor_lakehouse("Text") is None


# ---- a write from a cell


def test_a_write_waits_five_minutes_for_an_answer(svc, monkeypatch):  # noqa: F811
    waited = []

    class Done:
        def wait(self, seconds):
            waited.append(seconds)
            return False  # nobody answered in time

        def is_set(self):
            return False

    class Ask(es._WriteAsk):
        def __init__(self, *a):
            super().__init__(*a)
            self.done = Done()

    monkeypatch.setattr(es, "_WriteAsk", Ask)
    monkeypatch.setattr(svc, "_editor_audit", lambda *a, **k: None)
    said = svc._editor_write_check("C:\\repo", "Daily")("LH_Gold", "dbo", "sales")
    assert waited == [300]
    assert said == ("Nobody answered in KratosForge within 5 minutes, so LH_Gold.dbo.sales wasn't written. "
                    "Writes from VS Code ask in KratosForge each time.")


def test_an_approved_write_is_recorded_as_approved(svc, monkeypatch):  # noqa: F811
    audited = []
    monkeypatch.setattr(svc, "_editor_audit", lambda action, params, reply, seconds=0.0: audited.append((action, params, reply)))
    check = svc._editor_write_check("C:\\repo", "Daily")

    def answer():
        deadline = time.monotonic() + 10
        while not svc._editor_state()["asks"] and time.monotonic() < deadline:
            time.sleep(0.01)
        [ask_id] = list(svc._editor_state()["asks"])
        svc.editor_decide_write(ask_id, True)

    t = _REAL_THREAD(target=answer)
    t.start()
    assert check("LH_Gold", "Files", "out.csv") is None
    t.join(10)
    assert audited == [("editor_write_approved", {"notebook": "Daily", "target": "LH_Gold/out.csv"}, {"result": True})]
