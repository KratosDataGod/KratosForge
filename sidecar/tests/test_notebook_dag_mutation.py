"""Mutation testing batch 9 (docs/quality/mutation-testing.md): runMultiple's DAG on this PC.

Each test pins a behavior a surviving mutant changed without any other test noticing: the
activities a list or a DAG gives (retries, intervals and cell timeouts left out), a non-dict
activity, the concurrency note, and the DAG's timeoutInSeconds before and between runs and
retries. Time is a fake clock, so nothing here waits."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop import notebook_dag


class _Clock:
    """time.monotonic and time.sleep for notebook_dag: sleeping moves the clock on."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(notebook_dag, "time", SimpleNamespace(monotonic=c.monotonic, sleep=c.sleep))
    return c


# What a list or a DAG asks for


def test_a_list_runs_each_notebook_once_with_no_args_retries_or_cell_timeout():
    assert notebook_dag.activities(["a", "b"]) == [
        {"name": "a", "path": "a", "args": {}, "dependencies": [], "retry": 0, "retry_interval": 0,
         "cell_timeout": None},
        {"name": "b", "path": "b", "args": {}, "dependencies": [], "retry": 0, "retry_interval": 0,
         "cell_timeout": None},
    ]


def test_an_activity_that_leaves_out_retry_interval_and_cell_timeout_gets_none_of_them():
    got = notebook_dag.activities({"activities": [{"path": "nb1"}]})
    assert got == [{"name": "nb1", "path": "nb1", "args": {}, "dependencies": [], "retry": 0,
                    "retry_interval": 0.0, "cell_timeout": None}]


def test_an_activity_that_isnt_a_dict_is_a_type_error():
    with pytest.raises(TypeError, match="is a dict with a name and a path"):
        notebook_dag.activities({"activities": ["nb1"]})


def test_the_concurrency_note_is_printed_only_above_one(capsys):
    notebook_dag.run_multiple({"activities": [{"path": "a"}], "concurrency": 1}, lambda *a: "ok")
    assert capsys.readouterr().out == ""
    notebook_dag.run_multiple({"activities": [{"path": "a"}], "concurrency": 2}, lambda *a: "ok")
    assert "one at a time" in capsys.readouterr().out


# Timeouts


def test_without_a_dag_timeout_each_run_is_given_no_limit(clock):
    given = []
    notebook_dag.run_multiple(["a"], lambda path, timeout, args, cell: given.append(timeout))
    assert given == [0]


def test_a_dag_timeout_gives_each_run_the_time_left(clock):
    given = []

    def run(path, timeout, args, cell):
        given.append(timeout)
        clock.now += 10

    got = notebook_dag.run_multiple({"activities": [{"path": "a"}, {"path": "b"}], "timeoutInSeconds": 60}, run)
    assert given == [60, 50]
    assert got == {"a": {"exitVal": None, "exception": None}, "b": {"exitVal": None, "exception": None}}


def test_an_activity_reached_after_the_dag_timeout_is_skipped(clock):
    ran = []

    def run(path, timeout, args, cell):
        ran.append(path)
        clock.now += 60  # uses all of the DAG's time

    with pytest.raises(RuntimeError) as caught:
        notebook_dag.run_multiple({"activities": [{"path": "a"}, {"path": "b"}], "timeoutInSeconds": 60}, run)
    assert ran == ["a"]
    assert caught.value.results["b"] == {"exitVal": None,
                                         "exception": "Skipped: the DAG's timeoutInSeconds (60) ran out."}


def test_an_activity_with_a_second_left_still_runs(clock):
    ran = []

    def run(path, timeout, args, cell):
        ran.append((path, timeout))
        clock.now += 59

    notebook_dag.run_multiple({"activities": [{"path": "a"}, {"path": "b"}], "timeoutInSeconds": 60}, run)
    assert ran == [("a", 60), ("b", 1)]


def test_a_retry_after_the_dag_timeout_ran_out_is_stopped(clock):
    tries = []

    def run(path, timeout, args, cell):
        tries.append(timeout)
        clock.now += 30
        raise OSError("boom")

    dag = {"activities": [{"path": "a", "retry": 3, "retryIntervalInSeconds": 0}], "timeoutInSeconds": 60}
    with pytest.raises(RuntimeError) as caught:
        notebook_dag.run_multiple(dag, run)
    assert tries == [60, 30]
    assert caught.value.results["a"] == {"exitVal": None,
                                         "exception": "Stopped: the DAG's timeoutInSeconds (60) ran out."}


def test_a_retry_with_a_second_left_still_runs(clock):
    tries = []

    def run(path, timeout, args, cell):
        tries.append(timeout)
        clock.now += 59
        if len(tries) == 1:
            raise OSError("boom")
        return "ok"

    dag = {"activities": [{"path": "a", "retry": 1}], "timeoutInSeconds": 60}
    assert notebook_dag.run_multiple(dag, run) == {"a": {"exitVal": "ok", "exception": None}}
    assert tries == [60, 1]


# Retries


def test_retries_wait_the_interval_between_attempts_and_not_after_the_last(clock):
    tries = []

    def run(path, timeout, args, cell):
        tries.append(path)
        raise OSError("boom")

    with pytest.raises(RuntimeError) as caught:
        notebook_dag.run_multiple({"activities": [{"path": "a", "retry": 2, "retryIntervalInSeconds": 5}]}, run)
    assert tries == ["a", "a", "a"]
    assert clock.slept == [5.0, 5.0]
    assert caught.value.results["a"]["exception"] == "OSError: boom"


def test_a_notebook_that_succeeds_isnt_retried_or_waited_for(clock):
    tries = []
    got = notebook_dag.run_multiple({"activities": [{"path": "a", "retry": 2, "retryIntervalInSeconds": 5}]},
                                    lambda *a: tries.append(1) or 7)
    assert got == {"a": {"exitVal": 7, "exception": None}}
    assert tries == [1] and clock.slept == []
