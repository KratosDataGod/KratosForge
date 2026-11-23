"""Running Git: the timeouts, the environment each call gets, and the plain message for an
error Git words in a way the known causes don't match. subprocess is replaced; no Git runs."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from fabric_desktop.git import cli


@pytest.fixture
def calls(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(cli, "git_path", lambda: "/usr/bin/git")

    def run(cmd, **kw):
        seen.append({"cmd": cmd, **kw})
        return SimpleNamespace(returncode=0, stdout=b"git version 2.47.1\n", stderr=b"")

    monkeypatch.setattr(cli.subprocess, "run", run)
    return seen


def test_a_call_waits_a_minute_and_a_network_call_half_an_hour():
    assert cli.TIMEOUT == 60
    assert cli.NETWORK_TIMEOUT == 1800


def test_asking_the_version_waits_ten_seconds_with_no_input(calls):
    assert cli.version() == "2.47.1"
    assert calls[0]["cmd"] == ["/usr/bin/git", "--version"]
    assert calls[0]["timeout"] == 10 and calls[0]["stdin"] is subprocess.DEVNULL


def test_a_run_gets_the_environment_and_the_default_timeout(calls, monkeypatch):
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    cli.run("/repo", ["status"])
    env = calls[0]["env"]
    assert isinstance(env, dict) and env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"
    assert "GIT_OPTIONAL_LOCKS" not in env  # a plain run may take the index lock
    assert calls[0]["timeout"] == 60 and calls[0]["stdin"] is subprocess.DEVNULL
    cli.run("/repo", ["status"], read_only=True)
    assert calls[1]["env"]["GIT_OPTIONAL_LOCKS"] == "0"


def test_input_is_sent_instead_of_an_empty_stdin(calls):
    cli.run("/repo", ["hash-object", "--stdin"], input=b"data")
    assert calls[0]["input"] == b"data" and calls[0]["stdin"] is None


def test_only_a_read_only_environment_skips_optional_locks(monkeypatch):
    assert "GIT_OPTIONAL_LOCKS" not in cli.environment()
    assert cli.environment(read_only=True)["GIT_OPTIONAL_LOCKS"] == "0"


def test_the_persons_own_ssh_command_is_kept(monkeypatch):
    monkeypatch.delenv("GIT_SSH", raising=False)
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i C:/keys/mine")
    assert cli.environment()["GIT_SSH_COMMAND"] == "ssh -i C:/keys/mine"


def test_a_persons_own_ssh_program_means_no_batch_mode_command(monkeypatch):
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.setenv("GIT_SSH", "C:/Tools/plink.exe")
    env = cli.environment()
    assert "GIT_SSH_COMMAND" not in env and env["GIT_SSH"] == "C:/Tools/plink.exe"


def test_with_neither_set_ssh_runs_in_batch_mode(monkeypatch):
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    assert cli.environment()["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"


def test_an_unknown_fatal_error_is_given_in_git_s_words_with_a_capital():
    err = cli.explain("warning: something\nfatal: the moon is in the wrong phase\n")
    assert str(err) == "The moon is in the wrong phase" and err.kind == "failed"
    assert cli.explain("error: x marks the spot").args[0] == "X marks the spot"


def test_an_unknown_error_with_nothing_after_the_colon_reads_as_could_not():
    assert str(cli.explain("fatal:   ")) == "Git couldn't do that."
    assert str(cli.explain("fatal:")) == "Git couldn't do that."
    assert str(cli.explain("something with no prefix")) == "Git couldn't do that."
