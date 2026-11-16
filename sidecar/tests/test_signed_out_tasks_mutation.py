"""Mutation batch 62: signed_out_tasks.py's ids, PowerShell call, log wording and result codes.

Nothing here starts PowerShell: subprocess.run is replaced by a fake that records its call.
"""

from __future__ import annotations

import logging
import os
import subprocess

import pytest

from fabric_desktop import signed_out_tasks as sot


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


class FakeRun:
    def __init__(self, code=0, stdout="", stderr=""):
        self.code, self.stdout, self.stderr = code, stdout, stderr
        self.calls: list[tuple[list[str], dict]] = []
        self.xml: str | None = None

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        path = kw["env"].get("KF_TASK_XML")
        if path:
            with open(path, encoding="utf-16") as f:
                self.xml = f.read()
        return subprocess.CompletedProcess(args, self.code, self.stdout, self.stderr)


# ---- ids


@pytest.mark.parametrize("good", ["0", "abc", "0123456789abcdef", "f" * 32])
def test_check_id_returns_the_id_it_accepts(good):
    assert sot.check_id(good) == good


@pytest.mark.parametrize("bad", ["", None, "../x", "a/b", "a\\b", "ABC", "abg", "f" * 33, " abc", "abc ",
                                 "abc\n", "ab;c", "ab'c", "ab c", "0x1f"])
def test_check_id_refuses_anything_but_1_to_32_lowercase_hex(bad):
    with pytest.raises(ValueError, match="Not a schedule id"):
        sot.check_id(bad)


# ---- the PowerShell call


def test_powershell_runs_noninteractive_by_default_with_a_two_minute_limit(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(sot.subprocess, "run", fake)
    monkeypatch.setenv("SystemRoot", "R")
    sot._powershell("x {folder} y", "Tasks", {"KF_A": "1"})
    args, kw = fake.calls[0]
    assert args == [os.path.join("R", "System32", "WindowsPowerShell", "v1.0", "powershell.exe"), "-NoProfile",
                    "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "x Tasks y"]
    assert kw["timeout"] == 120
    assert kw["capture_output"] is True and kw["text"] is True and kw["check"] is False
    assert kw["errors"] == "replace"
    assert kw["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = {k.upper(): v for k, v in kw["env"].items()}   # Windows hands environ keys back upper-case
    assert env["KF_A"] == "1" and env["SYSTEMROOT"] == "R"


def test_powershell_with_a_prompt_drops_noninteractive_and_waits_five_minutes(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(sot.subprocess, "run", fake)
    sot._powershell("s", "Tasks", {}, prompt=True)
    args, kw = fake.calls[0]
    assert "-NonInteractive" not in args
    assert sot.PROMPT_SECONDS == 300 and kw["timeout"] == 300


def test_a_nonzero_exit_is_returned_not_raised(monkeypatch):
    monkeypatch.setattr(sot.subprocess, "run", FakeRun(code=5, stderr="no"))
    assert sot._powershell("s", "Tasks", {}).returncode == 5


def test_powershell_defaults_to_c_windows_without_systemroot(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(sot.subprocess, "run", fake)
    monkeypatch.delenv("SystemRoot", raising=False)
    sot._powershell("s", "Tasks", {})
    assert fake.calls[0][0][0] == os.path.join("C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def test_the_scripts_exit_with_the_codes_the_python_side_reads():
    assert (sot.CANCELLED, sot.FAILED, sot.HELLO_ONLY) == (3, 5, 6)
    assert f"exit {sot.CANCELLED}" in sot.REGISTER and f"exit {sot.HELLO_ONLY}" in sot.REGISTER
    for script in (sot.REGISTER, sot.SET_ENABLED):
        assert f"exit {sot.FAILED}" in script


# ---- register


def test_register_writes_the_xml_as_utf16_hands_it_by_environment_and_removes_it(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(sot.subprocess, "run", fake)
    sot.register("FabricDesktop", "0a1b", "<Task>é</Task>")
    args, kw = fake.calls[0]
    assert fake.xml == "<Task>é</Task>"
    assert kw["env"]["KF_TASK_ID"] == "0a1b" and "-NonInteractive" not in args
    assert not os.path.exists(kw["env"]["KF_TASK_XML"])


def test_register_removes_the_file_when_the_prompt_times_out(monkeypatch):
    seen = {}

    def run(args, **kw):
        seen["path"] = kw["env"]["KF_TASK_XML"]
        raise subprocess.TimeoutExpired(args, kw["timeout"])

    monkeypatch.setattr(sot.subprocess, "run", run)
    with pytest.raises(sot.PasswordNotSaved, match="within 5 minutes"):
        sot.register("FabricDesktop", "abc", "<Task/>")
    assert not os.path.exists(seen["path"])


# ---- set_enabled


@pytest.mark.parametrize("enabled, flag", [(True, "1"), (False, "0")])
def test_set_enabled_passes_the_choice_and_succeeds_on_exit_0(monkeypatch, enabled, flag):
    fake = FakeRun(code=0)
    monkeypatch.setattr(sot.subprocess, "run", fake)
    sot.set_enabled("FabricDesktop", "abc", enabled)
    args, kw = fake.calls[0]
    assert kw["env"]["KF_ENABLED"] == flag and kw["env"]["KF_TASK_ID"] == "abc"
    assert "-NonInteractive" in args and kw["timeout"] == 120


@pytest.mark.parametrize("code", [1, 5])
def test_set_enabled_raises_with_task_schedulers_words_on_any_other_exit(monkeypatch, code):
    monkeypatch.setattr(sot.subprocess, "run", FakeRun(code=code, stderr="hresult=-1 Access is denied"))
    with pytest.raises(RuntimeError, match="Task Scheduler: hresult=-1 Access is denied"):
        sot.set_enabled("FabricDesktop", "abc", True)


def test_set_enabled_refuses_a_bad_id_before_running_anything(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(sot.subprocess, "run", fake)
    with pytest.raises(ValueError):
        sot.set_enabled("FabricDesktop", "../x", True)
    assert fake.calls == []


# ---- the log line


def test_for_log_replaces_the_account_name_in_any_case(monkeypatch):
    monkeypatch.setenv("USERNAME", "jo")
    assert sot._for_log("CONTOSO\\JO could not log on; Jo") == "CONTOSO\\[user] could not log on; [user]"


def test_for_log_leaves_a_one_letter_account_name_alone(monkeypatch):
    monkeypatch.setenv("USERNAME", "a")
    assert sot._for_log("a task") == "a task"


def test_for_log_folds_lines_and_cuts_at_500_characters(monkeypatch):
    monkeypatch.setenv("USERNAME", "")
    assert sot._for_log("one\ntwo") == "one two"
    cut = sot._for_log("x" * 1000)
    assert len(cut) == 500 and cut.endswith("…")
    assert sot._for_log("y" * 500) == "y" * 500


def test_a_refusal_is_logged_without_the_account_name(monkeypatch, caplog):
    monkeypatch.setenv("USERNAME", "jsmith")
    monkeypatch.setattr(sot.subprocess, "run", FakeRun(code=5, stderr="DOMAIN\\jsmith: something else"))
    with caplog.at_level(logging.WARNING, logger=sot.__name__), pytest.raises(RuntimeError):
        sot.set_enabled("FabricDesktop", "abc", False)
    assert "DOMAIN\\[user]" in caplog.text and "jsmith" not in caplog.text


# ---- result codes


@pytest.mark.parametrize("result, said", [
    (1326, "password"), (0x8007052E, "password"), (-2147023570, "password"), ("1326", "password"),
    (1385, "not_allowed"), (0x80070569, "not_allowed"), (-2147023511, "not_allowed"),
    (0, None), (1327, None), (1386, None), (267011, None), (None, None), ("x", None)])
def test_needs_password_reads_both_forms_of_each_code(result, said):
    assert sot.needs_password(result) == said
