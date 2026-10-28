"""PB-048: schedules on this PC that also run while you're signed out of Windows (Password logon,
Windows' own prompt asks for the password)."""

import subprocess
from pathlib import Path

import pytest

from fabric_desktop import local_schedules, signed_out_tasks
from fabric_desktop.local_schedules import LocalSchedules, task_xml

TARGET = {"kind": "notebook", "name": "nb_sales"}
DAILY = {"kind": "daily", "times": ["02:00"]}


class Scheduler:
    """Records what would reach Task Scheduler; a signed-out register can be made to refuse."""
    available = True

    def __init__(self):
        self.calls, self.refuse, self.last = [], None, {}

    def register(self, schedule, executable, workdir, launcher=None):
        if schedule.get("signed_out") and self.refuse:
            raise signed_out_tasks.PasswordNotSaved(self.refuse)
        self.calls.append(("register", schedule["id"], bool(schedule.get("signed_out")), launcher))

    def unregister(self, schedule_id):
        self.calls.append(("unregister", schedule_id))

    def set_enabled(self, schedule_id, enabled):
        self.calls.append(("set_enabled", schedule_id, enabled))

    def results(self):
        return self.last


@pytest.fixture
def app_exe(tmp_path, monkeypatch):
    exe = tmp_path / "KratosForge.exe"
    exe.write_text("")
    monkeypatch.setenv("FABRIC_DESKTOP_APP_EXE", str(exe))
    return str(exe)


def schedules(tmp_path, engine="C:/engine/a/python.exe"):
    return LocalSchedules(tmp_path / "app", Scheduler(), engine)


def test_a_signed_out_task_logs_on_with_its_password_and_runs_the_app_exe(app_exe):
    s = {"id": "abc123", "target": TARGET, "config": {"type": "Daily", "times": ["02:00"]}, "enabled": True, "signed_out": True}
    xml = task_xml(s, "C:/engine/a/pythonw.exe", "C:/app", app_exe)
    assert "<LogonType>Password</LogonType>" in xml
    assert f"<Command>{app_exe}</Command>" in xml and "<Arguments>--run-schedule abc123</Arguments>" in xml
    plain = task_xml({**s, "signed_out": False}, "C:/engine/a/pythonw.exe", "C:/app", app_exe)
    assert "<LogonType>InteractiveToken</LogonType>" in plain and "fabric_desktop.cli run-schedule abc123" in plain


def test_turning_it_on_and_off_and_pausing_never_asks_twice(tmp_path, app_exe):
    pc = schedules(tmp_path)
    item = pc.add(TARGET, DAILY, signed_out=True)
    assert pc.scheduler.calls == [("register", item["id"], True, app_exe)]
    assert pc.get(item["id"])["launcher"] == app_exe

    pc.scheduler.calls.clear()
    pc.set_enabled(item["id"], False)  # pausing doesn't save the task again, so no prompt
    assert pc.scheduler.calls == [("set_enabled", item["id"], False)]
    assert pc.get(item["id"])["enabled"] is False

    pc.scheduler.calls.clear()
    pc.set_signed_out(item["id"], False)
    assert pc.scheduler.calls == [("register", item["id"], False, None)]
    assert "signed_out" not in pc.get(item["id"]) and "launcher" not in pc.get(item["id"])


def test_a_closed_or_refused_prompt_changes_nothing(tmp_path, app_exe):
    pc = schedules(tmp_path)
    item = pc.add(TARGET, DAILY)
    pc.scheduler.refuse = "Windows didn't accept that password; nothing changed."
    with pytest.raises(signed_out_tasks.PasswordNotSaved, match="didn't accept"):
        pc.set_signed_out(item["id"], True)
    with pytest.raises(signed_out_tasks.PasswordNotSaved):
        pc.add(TARGET, DAILY, signed_out=True)
    assert [s.get("signed_out") for s in pc._load()["schedules"]] == [None]


def test_new_times_and_the_setting_are_one_save(tmp_path, app_exe):
    pc = schedules(tmp_path)
    item = pc.add(TARGET, DAILY)
    pc.scheduler.refuse = "You closed the Windows prompt; nothing changed."
    with pytest.raises(signed_out_tasks.PasswordNotSaved):
        pc.update(item["id"], {"kind": "daily", "times": ["05:00"]}, signed_out=True)
    assert pc.get(item["id"])["config"]["times"] == ["02:00"] and "signed_out" not in pc.get(item["id"])
    pc.scheduler.refuse = None
    pc.scheduler.calls.clear()
    pc.update(item["id"], {"kind": "daily", "times": ["05:00"]}, signed_out=True)
    assert pc.scheduler.calls == [("register", item["id"], True, app_exe)]
    pc.update(item["id"], {"kind": "daily", "times": ["06:00"]}, signed_out=False)
    assert "signed_out" not in pc.get(item["id"]) and pc.get(item["id"])["config"]["times"] == ["06:00"]


def test_an_update_leaves_signed_out_tasks_alone(tmp_path, app_exe):
    pc = schedules(tmp_path)
    plain = pc.add(TARGET, DAILY)
    pc.add(TARGET, DAILY, signed_out=True)
    moved = schedules(tmp_path, "C:/engine/b/python.exe")
    assert moved.sync() == 1
    assert moved.scheduler.calls == [("register", plain["id"], False, None)]


def test_rows_say_when_windows_needs_the_password_again(tmp_path, app_exe, monkeypatch):
    pc = schedules(tmp_path)
    a = pc.add(TARGET, DAILY, signed_out=True)
    b = pc.add(TARGET, DAILY, signed_out=True)
    c = pc.add(TARGET, DAILY, signed_out=True)
    plain = pc.add(TARGET, DAILY)
    pc.scheduler.last = {a["id"]: {"result": 0x8007052E}, b["id"]: {"result": -2147023511}, c["id"]: {"result": 0}}
    rows = {r["id"]: r for r in pc.listing()}
    assert rows[a["id"]]["signed_out_problem"] == "password"
    assert rows[b["id"]]["signed_out_problem"] == "not_allowed"
    assert rows[c["id"]]["signed_out_problem"] is None
    assert "signed_out_problem" not in rows[plain["id"]]
    monkeypatch.delenv("FABRIC_DESKTOP_APP_EXE")  # the app's exe isn't where the task points any more
    assert {r["id"]: r for r in schedules(tmp_path).listing()}[c["id"]]["signed_out_problem"] == "moved"


def test_plain_schedules_never_ask_task_scheduler_for_results(tmp_path):
    pc = schedules(tmp_path)
    pc.add(TARGET, DAILY)
    pc.scheduler.results = lambda: pytest.fail("no signed-out schedule, no PowerShell")
    assert len(pc.listing()) == 1


def test_the_password_script_gets_the_task_by_environment_never_on_its_command_line(monkeypatch, tmp_path):
    seen = {}

    def run(args, **kw):
        seen["args"], seen["env"] = args, kw["env"]
        seen["xml"] = Path(kw["env"]["KF_TASK_XML"]).read_text(encoding="utf-16")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(signed_out_tasks.subprocess, "run", run)
    signed_out_tasks.register("FabricDesktop", "0a1b2c", "<Task/>")
    assert seen["env"]["KF_TASK_ID"] == "0a1b2c" and seen["xml"] == "<Task/>"
    assert not any("0a1b2c" in a for a in seen["args"])
    assert seen["args"][0].lower().replace("/", "\\").endswith(r"system32\windowspowershell\v1.0\powershell.exe")
    assert "-NonInteractive" not in seen["args"]  # Windows' prompt has to be able to show
    with pytest.raises(ValueError):
        signed_out_tasks.register("FabricDesktop", "x'; Remove-Item", "<Task/>")


@pytest.mark.parametrize(("code", "stderr", "words"), [
    (3, "", "closed the Windows prompt"),
    (4, "", "your own Windows account"),
    (5, "hresult=-2147023570 The user name or password is incorrect.", "didn't accept that password"),
    (5, "hresult=-2147023511 Logon failure: the user has not been granted the requested logon type", "doesn't allow runs while"),
    (5, "hresult=-1 Access is denied.", "Task Scheduler: hresult=-1 Access is denied."),
])
def test_refusals_are_said_plainly(monkeypatch, code, stderr, words):
    monkeypatch.setattr(signed_out_tasks.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, code, "", stderr))
    with pytest.raises(signed_out_tasks.PasswordNotSaved, match=words):
        signed_out_tasks.register("FabricDesktop", "abc", "<Task/>")


def test_last_results_off_windows_and_when_powershell_fails(monkeypatch):
    monkeypatch.setattr(signed_out_tasks.sys, "platform", "linux")
    assert signed_out_tasks.last_results("FabricDesktop") == {}
    monkeypatch.setattr(signed_out_tasks.sys, "platform", "win32")
    monkeypatch.setattr(signed_out_tasks.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, 0, "not json", ""))
    assert signed_out_tasks.last_results("FabricDesktop") == {}
    monkeypatch.setattr(signed_out_tasks.subprocess, "run",
                        lambda args, **kw: subprocess.CompletedProcess(args, 0, '{"abc":{"result":267011,"logon":"Password"}}', ""))
    assert signed_out_tasks.last_results("FabricDesktop") == {"abc": {"result": 267011, "logon": "Password"}}


def test_the_app_exe_must_be_a_real_absolute_file(monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_DESKTOP_APP_EXE", "KratosForge.exe")
    assert local_schedules.app_launcher() is None
    monkeypatch.setenv("FABRIC_DESKTOP_APP_EXE", str(tmp_path / "missing.exe"))
    assert local_schedules.app_launcher() is None


def test_an_unanswered_prompt_ends_and_changes_nothing(monkeypatch):
    def run(args, **kw):
        assert kw["timeout"] == signed_out_tasks.PROMPT_SECONDS
        raise subprocess.TimeoutExpired(args, kw["timeout"])
    monkeypatch.setattr(signed_out_tasks.subprocess, "run", run)
    with pytest.raises(signed_out_tasks.PasswordNotSaved, match="wasn't answered within 15 minutes"):
        signed_out_tasks.register("FabricDesktop", "abc", "<Task/>")


def test_the_scripts_load_scheduledtasks_from_system32_only():
    for script in (signed_out_tasks.REGISTER, signed_out_tasks.SET_ENABLED, signed_out_tasks.LAST_RESULTS):
        assert 'Import-Module "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\Modules\\ScheduledTasks"' in script


def test_it_policy_turns_the_choice_off(tmp_path, app_exe):
    pc = schedules(tmp_path)
    item = pc.add(TARGET, DAILY, signed_out=True)
    pc.set_enabled(item["id"], False)
    off = LocalSchedules(tmp_path / "app", pc.scheduler, "C:/engine/a/python.exe", allow_signed_out=False)
    with pytest.raises(PermissionError, match="allow_signed_out_schedules"):
        off.add(TARGET, DAILY, signed_out=True)
    with pytest.raises(PermissionError):
        off.set_enabled(item["id"], True)  # resuming would start password runs again
    with pytest.raises(PermissionError):
        off.update(item["id"], {"kind": "daily", "times": ["05:00"]})
    assert off.listing()[0]["signed_out_problem"] == "policy"
    off.set_signed_out(item["id"], False)  # turning it back to signed-in always works
    assert "signed_out" not in off.get(item["id"])
    off.set_enabled(item["id"], True)


def test_the_policy_key_is_it_only_and_fails_closed():
    from fabric_desktop import app_settings
    assert app_settings.DEFAULTS["allow_signed_out_schedules"] is True
    assert app_settings.RESTRICTED["allow_signed_out_schedules"] is False
    assert "allow_signed_out_schedules" not in app_settings.USER_KEYS


def test_signed_out_tasks_set_up_before_the_policy_never_run_and_are_paused(tmp_path, app_exe, monkeypatch):
    from fabric_desktop import schedule_service
    from fabric_desktop.service import Service

    scheduler = Scheduler()
    allow = {"on": True}
    monkeypatch.setattr(Service, "pc_schedules", property(
        lambda self: LocalSchedules(self.root, scheduler, "C:/engine/a/python.exe", allow_signed_out=allow["on"])))
    svc = Service(root=tmp_path / "app")
    item = svc.pc_schedules.add(TARGET, DAILY, signed_out=True)
    plain = svc.pc_schedules.add(TARGET, DAILY)
    allow["on"] = False
    monkeypatch.setattr(schedule_service.ScheduleMixin, "_run_notebook_here",
                        lambda self, name: pytest.fail("a disallowed signed-out schedule must not run"))
    row = svc.run_schedule(item["id"])
    assert row["status"] == "skipped" and "turned off runs while you're signed out" in row["message"]
    assert svc.pc_schedules.runs()[0]["schedule_id"] == item["id"]

    scheduler.calls.clear()
    assert svc.sync_pc_schedules()["paused"] == [item["id"]]
    assert scheduler.calls == [("set_enabled", item["id"], False)]
    assert svc.pc_schedules.get(item["id"])["enabled"] is False and svc.pc_schedules.get(plain["id"])["enabled"] is True
    assert svc.sync_pc_schedules()["paused"] == []  # already paused: nothing more to do
