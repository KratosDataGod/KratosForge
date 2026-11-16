"""``%pip`` in a cell: how long an install is followed, how much of pip's log a failure shows, a deadline
reached exactly, and a package with no summary. Mutation batch 58."""

import importlib.metadata
import types

import pytest

from fabric_desktop import environments, pip_magic


def no_file(path):
    raise AssertionError(f"no requirements file should be read, got {path}")


class Engine:
    """A job that runs for ``polls`` polls, then ends in ``final`` with ``log``."""

    def __init__(self, polls=1, final="done", log=""):
        self.polls, self.final, self.log = polls, final, log

    def __call__(self, method, *args):
        if method == "install_packages":
            return {"id": "j", "state": "running", "environment": "nb"}
        self.polls -= 1
        return {"id": "j", "state": self.final if self.polls <= 0 else "running", "environment": "nb",
                "log": self.log}


def test_an_install_is_followed_for_16_minutes_a_minute_past_the_engines_limit():
    assert pip_magic.GIVE_UP_SECONDS == 960
    assert pip_magic.GIVE_UP_SECONDS == environments.INSTALL_SECONDS + 60


def test_a_failure_shows_exactly_the_last_4000_characters_of_the_log():
    log = "a" + "b" * 3999
    with pytest.raises(RuntimeError) as err:
        pip_magic.run("install nothing-kf", Engine(final="failed", log="z" + log), no_file)
    assert str(err.value) == "pip couldn't install nothing-kf:\n" + log


def test_reaching_the_deadline_exactly_still_polls(monkeypatch):
    # The clock never moves, so with no time to wait the deadline equals now: not past it yet.
    monkeypatch.setattr(pip_magic.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(pip_magic.time, "sleep", lambda s: None)
    monkeypatch.setattr(pip_magic, "GIVE_UP_SECONDS", 0)
    engine = Engine(polls=1)
    pip_magic.run("install polars", engine, no_file)
    assert engine.polls == 0


def test_show_prints_an_empty_summary_when_a_package_has_none(monkeypatch, capsys):
    dist = types.SimpleNamespace(metadata={"Name": "bare-kf", "Summary": None}, version="1.0")
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: dist)
    pip_magic.run("show bare-kf", None, no_file)
    assert capsys.readouterr().out == "Name: bare-kf\nVersion: 1.0\nSummary: \n"
