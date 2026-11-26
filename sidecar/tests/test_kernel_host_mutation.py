"""Mutation batch 372: how kernel_host.dispatch takes or refuses one protocol line."""

import json

import pytest

from fabric_desktop import kernel_host


class Host:
    def __init__(self):
        self.handled = []

    def handle(self, message):
        self.handled.append(message)


class Channel:
    def __init__(self):
        self.sent, self.answered = [], []

    def send(self, message):
        self.sent.append(message)

    def answer(self, message):
        self.answered.append(message)


@pytest.fixture
def threads(monkeypatch):
    made = []

    class Thread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            made.append({"target": target, "args": args, "daemon": daemon, "name": name})

        def start(self):
            made[-1]["started"] = True

    monkeypatch.setattr(kernel_host.threading, "Thread", Thread)
    return made


def test_a_message_is_at_most_64_mb_and_one_of_exactly_the_limit_is_read(monkeypatch):
    assert kernel_host.MAX_LINE == 64 * 1024 * 1024
    line = json.dumps({"id": "a1", "result": "x"})
    monkeypatch.setattr(kernel_host, "MAX_LINE", len(line))
    ch = Channel()
    assert kernel_host.dispatch(Host(), ch, line) is None and ch.answered == [{"id": "a1", "result": "x"}]
    monkeypatch.setattr(kernel_host, "MAX_LINE", len(line) - 1)
    assert kernel_host.dispatch(Host(), Channel(), line) == "longer than 0 MB"


def test_the_refusal_names_the_limit_in_megabytes(monkeypatch, capsys):
    monkeypatch.setattr(kernel_host, "MAX_LINE", 3 * 1024 * 1024)
    line = json.dumps({"id": "a1", "result": "x" * (3 * 1024 * 1024)})
    assert kernel_host.dispatch(Host(), Channel(), line) == "longer than 3 MB"
    assert f"({len(line)} characters)" in capsys.readouterr().err


def test_a_malformed_request_without_an_id_gets_no_answer(capsys):
    ch = Channel()
    problem = kernel_host.dispatch(Host(), ch, '{"op": "debug", "args": [1]}')
    assert problem == "a request whose op isn't text or whose args aren't an object"
    assert ch.sent == [] and ch.answered == []
    assert "op 'debug'" in capsys.readouterr().err


def test_a_request_with_an_op_that_isnt_text_names_no_op(capsys):
    kernel_host.dispatch(Host(), Channel(), '{"op": 5, "id": "p1"}')
    err = capsys.readouterr().err
    assert ", op" not in err and "characters)" in err


def test_the_notice_names_at_most_40_characters_of_the_op(capsys):
    op = "a" * 39 + "bc"
    kernel_host.dispatch(Host(), Channel(), json.dumps({"op": op, "args": [1]}))
    err = capsys.readouterr().err
    assert repr(op[:40]) in err and "abc" not in err and "ab'" in err


def test_a_request_with_no_args_or_args_none_is_taken(threads):
    assert kernel_host.dispatch(Host(), Channel(), '{"op": "ping", "id": "p1"}') is None
    assert kernel_host.dispatch(Host(), Channel(), '{"op": "ping", "id": "p2", "args": null}') is None
    assert [t["args"][0]["id"] for t in threads] == ["p1", "p2"]


def test_each_request_runs_on_a_daemon_thread_named_for_its_op(threads):
    op = "x" * 39 + "yz"
    host = Host()
    assert kernel_host.dispatch(host, Channel(), json.dumps({"op": op, "id": "p1", "args": {}})) is None
    t = threads[0]
    assert t["target"] == host.handle and t["daemon"] is True and t["started"]
    assert t["name"] == "request-" + "x" * 39 + "y"


def test_an_answer_with_a_text_id_wakes_its_caller_and_no_thread_starts(threads):
    ch = Channel()
    assert kernel_host.dispatch(Host(), ch, '{"id": "t1", "result": 1}') is None
    assert ch.answered == [{"id": "t1", "result": 1}] and threads == []
