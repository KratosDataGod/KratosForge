"""The live test harness's own failure messages: each opt-in check run directly against fakes and local
disk, with the store or Fabric answering wrongly at one step. Nothing here reaches a tenant."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from fabric_desktop import leases, livetest
from fabric_desktop.onelake import TableRef
from tests.test_livetest import WS, ItemFabric, extra_harness, local_writer, write_local


def _harness(tmp_path, **kw):
    test = extra_harness(tmp_path, **kw)
    test.lakehouse_id = "lh-1"
    return test


# ---------------------------------------------------------------- small pieces


def test_the_mailbox_agent_always_has_a_free_worker():
    assert livetest._MailboxAgent("a", queue=None).has_free_worker() is True


def test_the_default_write_path_publishes_with_the_storage_token(tmp_path, monkeypatch):
    from fabric_desktop import publish
    seen = []
    monkeypatch.setattr(publish, "publish", lambda data, ref, mode, token: seen.append((data, ref, mode, token())) or "ok")
    test = livetest.LiveTest(WS, ItemFabric(), None, SimpleNamespace(storage_token=lambda: "tok"), tmp_path / "out",
                             run_id="run1")
    ref = TableRef(WS, "lh-1", "dbo", "t")
    assert test.write_table(ref, "rows", "append") == "ok"
    assert seen == [("rows", ref, "append", "tok")]


def test_the_default_mailbox_control_is_the_test_lakehouse(tmp_path, monkeypatch):
    from fabric_desktop import mailbox
    made = []
    monkeypatch.setattr(mailbox, "OneLakeControl", lambda ws, lh, token: made.append((ws, lh, token())) or "control")
    test = livetest.LiveTest(WS, ItemFabric(), None, SimpleNamespace(storage_token=lambda: "tok"), tmp_path / "out",
                             run_id="run1")
    test.lakehouse_id = "lh-9"
    assert test.mailbox_control() == "control"
    assert made == [(WS, "lh-9", "tok")]


# ---------------------------------------------------------------- delta.concurrent


class _SerialThread:
    """Runs its target on start, so the two writers commit one after the other."""

    def __init__(self, target, args, daemon):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


class _Barrier:
    def __init__(self, parties):
        pass

    def wait(self, timeout=None):
        return 0


class _BrokenBarrier(_Barrier):
    def wait(self, timeout=None):
        raise threading.BrokenBarrierError


def _threads(monkeypatch, thread=_SerialThread, barrier=_Barrier):
    monkeypatch.setattr(livetest, "threading", SimpleNamespace(Thread=thread, Barrier=barrier,
                                                               BrokenBarrierError=threading.BrokenBarrierError))


def test_writers_that_start_without_each_other_still_both_commit(tmp_path, monkeypatch):
    _threads(monkeypatch, barrier=_BrokenBarrier)
    detail = _harness(tmp_path)._delta_concurrent()
    assert "version 2, 101 rows" in detail and "Neither writer had to retry" in detail


def test_a_writer_still_committing_after_the_wait_fails_the_check(tmp_path, monkeypatch):
    class Stuck(_SerialThread):
        def start(self):
            pass

        def is_alive(self):
            return True
    _threads(monkeypatch, thread=Stuck)
    with pytest.raises(RuntimeError, match="still committing after 5 minutes"):
        _harness(tmp_path)._delta_concurrent()


def test_appends_that_take_more_than_one_commit_each_fail_the_check(tmp_path, monkeypatch):
    _threads(monkeypatch)
    write = local_writer(tmp_path)

    def in_halves(ref, data, mode):
        if mode == "append" and data.num_rows > 1:
            half = data.num_rows // 2
            write(ref, data.slice(0, half), mode)
            write(ref, data.slice(half), mode)
        else:
            write(ref, data, mode)
    with pytest.raises(RuntimeError, match="found version 4"):
        _harness(tmp_path, writer=in_halves)._delta_concurrent()


# ---------------------------------------------------------------- lease.onelake


class _SecondHoldWins(leases.LocalBackend):
    """Refuses B's first try, then lets B's hold take the lease anyway."""

    def __init__(self, folder):
        super().__init__(folder)
        self.creates = 0

    def create(self, key, data):
        self.creates += 1
        if self.creates == 3:
            return "b-etag"
        return super().create(key, data)


class _DeleteKeepsTheFile(leases.LocalBackend):
    def delete(self, key, etag):
        return True


class _NoCreateAfterRelease(leases.LocalBackend):
    released = False

    def delete(self, key, etag):
        self.released = True
        return super().delete(key, etag)

    def create(self, key, data):
        return None if self.released else super().create(key, data)


class _SecondDeleteFails(leases.LocalBackend):
    deletes = 0

    def delete(self, key, etag):
        self.deletes += 1
        return super().delete(key, etag) if self.deletes == 1 else False


@pytest.mark.parametrize("backend, says", [
    (_SecondHoldWins, "A second writer held the lease while the first held it"),
    (_DeleteKeepsTheFile, "still there after the first writer released it"),
    (_NoCreateAfterRelease, "couldn't take the lease after the first released it"),
    (_SecondDeleteFails, "Releasing the second writer's lease failed"),
])
def test_a_lease_store_that_answers_wrongly_fails_the_lease_check(tmp_path, backend, says):
    store = backend(tmp_path / "leases")
    test = _harness(tmp_path, lease_backend=lambda: store)
    with pytest.raises(RuntimeError, match=says):
        test._lease()


# ---------------------------------------------------------------- delta.merge_profile


def _merge_harness(tmp_path):
    return _harness(tmp_path, writer=lambda ref, data, mode: write_local(tmp_path, ref, data, mode))


def test_a_merge_statement_that_cant_be_read_fails_the_check(tmp_path, monkeypatch):
    monkeypatch.setattr(livetest.delta_dml, "parse", lambda sql: None)
    with pytest.raises(RuntimeError, match="MERGE statement couldn't be read"):
        _merge_harness(tmp_path)._merge_profile()


def test_a_seeded_table_that_cant_be_opened_fails_the_check(tmp_path, monkeypatch):
    monkeypatch.setattr(livetest.delta_profile, "open_target", lambda uri, opts, profile: None)
    with pytest.raises(RuntimeError, match="seeded table couldn't be opened"):
        _merge_harness(tmp_path)._merge_profile()


def test_a_merge_that_changed_nothing_fails_the_check(tmp_path, monkeypatch):
    monkeypatch.setattr(livetest.delta_dml, "apply", lambda change, table, source, columns: {})
    with pytest.raises(RuntimeError, match="After the MERGE the table holds"):
        _merge_harness(tmp_path)._merge_profile()


# ---------------------------------------------------------------- items


def test_a_notebook_create_that_returns_the_item_is_used_as_it_is(tmp_path):
    class Returns(ItemFabric):
        def save_notebook(self, ws, name, ipynb):
            super().save_notebook(ws, name, ipynb)
            return next(i for i in self.items.values() if i["displayName"] == name)

        def list_notebooks(self, ws):
            raise AssertionError("looked up a notebook the create returned")
    test = _harness(tmp_path, fabric=Returns())
    detail = test._notebook_publish()
    assert test.notebook_id == "item-0" and "read back unchanged" in detail
    assert test.ledger.created[-1]["id"] == "item-0"


def test_a_pipeline_definition_that_changes_on_export_fails_the_check(tmp_path, monkeypatch):
    from fabric_desktop.orchestration import definition
    real = definition.loads
    calls = []

    def loads(text, name_hint=None):
        out = real(text, name_hint=name_hint)
        calls.append(out)
        if len(calls) == 2:
            out.pipeline.content = {"properties": {"activities": []}}
        return out
    monkeypatch.setattr(definition, "loads", loads)
    with pytest.raises(RuntimeError, match="Exporting the definition and reading it again changed it"):
        _harness(tmp_path)._pipeline_definition()


# ---------------------------------------------------------------- pipeline.mailbox


def test_a_request_already_in_the_control_folder_is_never_taken_over(tmp_path):
    test = _harness(tmp_path)
    control = test.mailbox_control()
    control.create("requests/fdlt_run1.json", b"{}")
    test.mailbox_control = lambda: control
    with pytest.raises(RuntimeError, match="requests/fdlt_run1.json already existed"):
        test._pipeline_mailbox()


ACCEPTED = {"state": "accepted", "agent": "fdlt_run1_a",
            "pipeline_run": {"verified": False, "detail": "Fabric couldn't find that run"}}


@pytest.mark.parametrize("statuses, says", [
    ([{"state": "refused", "agent": "fdlt_run1_a"}], "Expected accepted by agent A, the status says refused"),
    ([ACCEPTED | {"pipeline_run": {"verified": True}}], "made-up pipeline run wasn't reported as not found"),
    ([ACCEPTED, {"agent": "fdlt_run1_b"}], "Agent B changed a status agent A holds"),
    ([ACCEPTED, ACCEPTED, {"state": "accepted"}], "the status says accepted, not cancelled"),
])
def test_a_mailbox_that_answers_wrongly_fails_and_leaves_nothing_behind(tmp_path, monkeypatch, statuses, says):
    answers = iter(statuses)
    monkeypatch.setattr(livetest, "_status", lambda control, rid: next(answers))
    test = _harness(tmp_path)
    control = test.mailbox_control()
    test.mailbox_control = lambda: control
    with pytest.raises(RuntimeError, match=says):
        test._pipeline_mailbox()
    for folder in ("cancel", "requests", "status"):
        assert control.read(f"{folder}/fdlt_run1.json") is None

