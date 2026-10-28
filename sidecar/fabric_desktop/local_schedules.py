"""Schedules that run on this PC: a notebook, a metadata environment's tables, or a pipeline.

Windows Task Scheduler starts each run, so a schedule fires with the app closed, as long as
you are signed in to Windows. The task runs this sidecar's own ``pythonw.exe -m
fabric_desktop.cli run-schedule <id>``; the app re-points every task at the current engine
when it starts (an update installs a new engine folder). A schedule set to run while you're signed
out too (signed_out_tasks) runs ``KratosForge.exe --run-schedule <id>`` instead, which finds the
current engine itself: its task keeps your password, and changing it would ask for that again. Each run is logged to
``schedule_runs.jsonl`` so Monitor can show the last result of every schedule.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from . import signed_out_tasks
from .fabric_run import WEEKDAYS, describe_schedule, schedule_configuration
from .jsonfile import read_json, read_text, write_json

TASK_FOLDER = "FabricDesktop"
RUNS_FILE = "schedule_runs.jsonl"
SCHEDULES_FILE = "schedules.json"


# What a pipeline schedule keeps besides its folder: the settings of the Run dialog that the run
# needs every time. SecureString parameter values are never kept (the definition's defaults apply).
PIPELINE_OPTIONS = ("parameters", "mocks", "notebooks", "sql_projects")


def target_label(target: dict[str, Any]) -> str:
    if target.get("kind") == "notebook":
        return target.get("name", "")
    if target.get("kind") == "pipeline":
        return f"Pipeline: {target.get('name') or Path(str(target.get('path', ''))).name}"
    names = target.get("entities") or []
    what = ", ".join(names) if names else "every table"
    return f"{str(target.get('env', '')).title()} metadata: {what}"


def check_target(target: dict[str, Any]) -> dict[str, Any]:
    kind = target.get("kind")
    if kind == "notebook":
        if not target.get("name"):
            raise ValueError("Pick the notebook to run")
        return {"kind": "notebook", "name": target["name"]}
    if kind == "meta":
        if target.get("env") not in ("dev", "test", "prod"):
            raise ValueError("Pick the environment to run")
        return {"kind": "meta", "env": target["env"], "entities": list(target.get("entities") or [])}
    if kind == "pipeline":
        if not target.get("path"):
            raise ValueError("Pick the pipeline to run")
        out: dict[str, Any] = {"kind": "pipeline", "path": str(target["path"]), "name": str(target.get("name") or "")}
        for key in PIPELINE_OPTIONS:
            value = target.get(key)
            if value is not None and not isinstance(value, dict):
                raise ValueError(f"{key} must be an object")
            out[key] = dict(value or {})
        out["read_lakehouses"] = bool(target.get("read_lakehouses"))
        out["workspace_id"] = str(target.get("workspace_id") or "")
        return out
    raise ValueError("A schedule runs a notebook, a metadata environment or a pipeline")


def local_configuration(schedule: dict[str, Any]) -> dict[str, Any]:
    """The same shape as Fabric's schedule configuration, in this PC's own time zone."""
    config = schedule_configuration(schedule.get("kind", "daily"), schedule.get("times"),
                                    schedule.get("every_minutes"), schedule.get("weekdays"), "Local")
    now = dt.datetime.now().replace(second=0, microsecond=0)
    config["startDateTime"] = now.strftime("%Y-%m-%dT%H:%M:%S")
    config.pop("endDateTime", None)
    return config


def next_run(config: dict[str, Any], now: dt.datetime | None = None) -> dt.datetime | None:
    """When the schedule fires next, in local time (what Task Scheduler does with these triggers)."""
    now = (now or dt.datetime.now()).replace(second=0, microsecond=0)
    kind = config.get("type")
    if kind == "Cron":
        start = dt.datetime.fromisoformat(config.get("startDateTime") or now.isoformat())
        step = dt.timedelta(minutes=int(config["interval"]))
        if start > now:
            return start
        return start + step * ((now - start) // step + 1)
    times = sorted(config.get("times") or [])
    days = set(range(7)) if kind == "Daily" else {WEEKDAYS.index(d) for d in config.get("weekdays") or []}
    if not times or not days:
        return None
    for offset in range(8):
        day = now.date() + dt.timedelta(days=offset)
        if day.weekday() not in days:
            continue
        for t in times:
            h, m = map(int, t.split(":"))
            at = dt.datetime.combine(day, dt.time(h, m))
            if at > now:
                return at
    return None


# ---- Task Scheduler

def task_name(schedule_id: str) -> str:
    return f"\\{TASK_FOLDER}\\{schedule_id}"


def pythonw(executable: str | None = None) -> str:
    """The windowless interpreter next to this one, so a scheduled run never flashes a console."""
    exe = Path(executable or sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def _triggers(config: dict[str, Any]) -> str:
    start = config.get("startDateTime") or dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    day = start[:10]
    if config.get("type") == "Cron":
        return (f"<TimeTrigger><StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>"
                f"<Repetition><Interval>PT{int(config['interval'])}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd>"
                "</Repetition></TimeTrigger>")
    out = []
    for t in config.get("times") or []:
        boundary = f"{day}T{t}:00"
        if config.get("type") == "Daily":
            schedule = "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
        else:
            days = "".join(f"<{d} />" for d in config.get("weekdays") or [])
            schedule = f"<ScheduleByWeek><DaysOfWeek>{days}</DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>"
        out.append(f"<CalendarTrigger><StartBoundary>{boundary}</StartBoundary><Enabled>true</Enabled>{schedule}</CalendarTrigger>")
    return "".join(out)


def app_launcher() -> str | None:
    """The app's own exe, which the app hands the engine (FABRIC_DESKTOP_APP_EXE): its path stays the
    same across updates, so a signed-out task that runs it never has to change. None in development."""
    exe = os.environ.get("FABRIC_DESKTOP_APP_EXE") or ""
    return exe if exe and os.path.isabs(exe) and os.path.isfile(exe) else None


def task_xml(schedule: dict[str, Any], executable: str, workdir: str, launcher: str | None = None) -> str:
    """Task Scheduler's definition: run as you, only while you are signed in to Windows (your
    Fabric sign-in lives in your profile), start a missed run when the PC wakes, never two at once.
    A signed-out schedule logs on with the password Windows keeps for the task, through ``launcher``
    (the app's exe) when there is one."""
    signed_out = bool(schedule.get("signed_out"))
    if signed_out and launcher:
        executable, args = launcher, f"--run-schedule {schedule['id']}"
    else:
        args = f"-m fabric_desktop.cli run-schedule {schedule['id']}"
    logon = "Password" if signed_out else "InteractiveToken"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{escape("KratosForge: " + target_label(schedule["target"]))}</Description></RegistrationInfo>
  <Triggers>{_triggers(schedule["config"])}</Triggers>
  <Principals><Principal id="Author"><LogonType>{logon}</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT12H</ExecutionTimeLimit>
    <Enabled>{"true" if schedule.get("enabled", True) else "false"}</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(executable)}</Command><Arguments>{escape(args)}</Arguments>
    <WorkingDirectory>{escape(workdir)}</WorkingDirectory></Exec></Actions>
</Task>
"""


class TaskScheduler:
    """``schtasks.exe``. On other systems schedules are saved but nothing starts them."""

    available = sys.platform == "win32"

    def _run(self, *args: str) -> None:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(["schtasks", *args], capture_output=True, text=True, creationflags=flags)
        if res.returncode != 0:
            raise RuntimeError(f"Task Scheduler: {(res.stderr or res.stdout).strip()}")

    def register(self, schedule: dict[str, Any], executable: str, workdir: str, launcher: str | None = None) -> None:
        """Saves the task. A signed-out schedule's task is saved through Windows' password prompt
        (signed_out_tasks.register), which raises PasswordNotSaved when it is closed or refused."""
        if not self.available:
            return
        if schedule.get("signed_out"):
            signed_out_tasks.register(TASK_FOLDER, schedule["id"], task_xml(schedule, executable, workdir, launcher))
            return
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as f:
            f.write(task_xml(schedule, executable, workdir))
        try:
            self._run("/Create", "/TN", task_name(schedule["id"]), "/XML", f.name, "/F")
        finally:
            os.unlink(f.name)

    def unregister(self, schedule_id: str) -> None:
        if self.available:
            try:
                self._run("/Delete", "/TN", task_name(schedule_id), "/F")
            except RuntimeError:
                pass  # already gone

    def set_enabled(self, schedule_id: str, enabled: bool) -> None:
        """Pause or resume a signed-out task without asking for the password again."""
        if self.available:
            signed_out_tasks.set_enabled(TASK_FOLDER, schedule_id, enabled)

    def results(self) -> dict[str, dict[str, Any]]:
        """Task Scheduler's last result for each task, by schedule id."""
        return signed_out_tasks.last_results(TASK_FOLDER) if self.available else {}

    def run_now(self, schedule_id: str) -> bool:
        if not self.available:
            return False
        self._run("/Run", "/TN", task_name(schedule_id))
        return True


class LocalSchedules:
    def __init__(self, root: Path, scheduler: TaskScheduler | None = None, executable: str | None = None,
                 allow_signed_out: bool = True):
        self.root = root
        self.allow_signed_out = allow_signed_out  # IT's allow_signed_out_schedules
        self.scheduler = scheduler or TaskScheduler()
        self.executable = pythonw(executable)
        self.launcher = app_launcher()

    def _register(self, s: dict[str, Any]) -> None:
        if s.get("signed_out"):
            if not self.allow_signed_out:
                raise PermissionError("Your organization doesn't allow schedules that run while you're signed out of "
                                      "Windows (allow_signed_out_schedules in its policy file).")
            self.scheduler.register(s, self.executable, str(self.root), self.launcher)
            s["launcher"] = self.launcher or ""
        else:
            self.scheduler.register(s, self.executable, str(self.root))
            s.pop("launcher", None)

    @property
    def file(self) -> Path:
        return self.root / SCHEDULES_FILE

    @property
    def runs_file(self) -> Path:
        return self.root / RUNS_FILE

    def _load(self) -> dict[str, Any]:
        # A torn file is set aside (not overwritten by the next save), so its schedules can be recovered
        return read_json(self.file, {"engine": "", "schedules": []})

    def _save(self, data: dict[str, Any]) -> None:
        write_json(self.file, data, indent=1)

    def set_aside(self) -> list[dict[str, Any]]:
        """Schedules files that couldn't be read and were moved aside (jsonfile.set_aside), newest first:
        their schedules aren't listed any more, but Task Scheduler may still have tasks for them."""
        out = []
        for path in self.root.glob(f"{SCHEDULES_FILE}.corrupt-*"):
            stamp = path.name.rsplit(".corrupt-", 1)[1]
            try:
                at = dt.datetime.strptime(stamp, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=dt.timezone.utc)
            except ValueError:  # renamed by hand: when it was last changed will do
                at = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
            out.append({"file": path.name, "path": str(path), "at": at.isoformat(timespec="seconds")})
        return sorted(out, key=lambda x: x["at"], reverse=True)

    def notebooks(self) -> set[str]:
        """The notebooks an enabled schedule on this PC runs: a notebook schedule's notebook, and the
        saved notebooks a pipeline schedule's Notebook steps run (its ``notebooks`` map). Only reads:
        a torn file raises ValueError and is left where it is."""
        try:
            data = json.loads(read_text(self.file))
        except FileNotFoundError:
            return set()
        out: set[str] = set()
        for s in data.get("schedules") or []:
            target = s.get("target") or {}
            if not s.get("enabled", True):
                continue
            if target.get("kind") == "notebook":
                out.add(str(target.get("name") or ""))
            elif target.get("kind") == "pipeline" and isinstance(target.get("notebooks"), dict):
                out |= {v for v in target["notebooks"].values() if isinstance(v, str)}
        return out - {""}

    def get(self, schedule_id: str) -> dict[str, Any]:
        found = next((s for s in self._load()["schedules"] if s["id"] == schedule_id), None)
        if found is None:
            raise LookupError(f"There is no schedule {schedule_id} on this PC")
        return found

    def add(self, target: dict[str, Any], schedule: dict[str, Any], signed_out: bool = False) -> dict[str, Any]:
        item = {"id": uuid.uuid4().hex[:12], "target": check_target(target), "config": local_configuration(schedule),
                "enabled": True, "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        if signed_out:
            item["signed_out"] = True
        self._register(item)
        data = self._load()
        data["schedules"].append(item)
        data["engine"] = self.executable
        self._save(data)
        return item

    def set_enabled(self, schedule_id: str, enabled: bool) -> dict[str, Any]:
        data = self._load()
        for s in data["schedules"]:
            if s["id"] == schedule_id:
                s["enabled"] = bool(enabled)
                if s.get("signed_out"):
                    if s["enabled"] and not self.allow_signed_out:
                        raise PermissionError("Your organization doesn't allow schedules that run while you're signed "
                                              "out of Windows. Edit it to run only while you're signed in, then resume it.")
                    self.scheduler.set_enabled(schedule_id, s["enabled"])
                else:
                    self._register(s)
                self._save(data)
                return s
        raise LookupError(f"There is no schedule {schedule_id} on this PC")

    def update(self, schedule_id: str, schedule: dict[str, Any], signed_out: bool | None = None) -> dict[str, Any]:
        """New times for an existing schedule. Its Windows task is replaced under the same id, so its run
        history stays, and a paused schedule stays paused. ``signed_out`` (when given) turns running while
        signed out on or off in the same save, so Windows asks for the password at most once and a closed
        prompt leaves both the times and the setting as they were."""
        config = local_configuration(schedule)  # checks the times before anything changes
        data = self._load()
        for s in data["schedules"]:
            if s["id"] == schedule_id:
                s["config"] = config
                if signed_out is not None:
                    s["signed_out"] = bool(signed_out)
                self._register(s)  # a signed-out schedule's task: Windows asks for the password again
                if not s.get("signed_out"):
                    s.pop("signed_out", None)
                self._save(data)
                return s
        raise LookupError(f"There is no schedule {schedule_id} on this PC")

    def set_signed_out(self, schedule_id: str, signed_out: bool) -> dict[str, Any]:
        """Run also while you're signed out of Windows (Windows asks for your password and keeps it
        with the task), or only while signed in again. Also how a new password is given: turning it
        on again saves the task with the password entered now. Nothing changes if the prompt is
        closed or Windows refuses (PasswordNotSaved)."""
        data = self._load()
        for s in data["schedules"]:
            if s["id"] == schedule_id:
                s["signed_out"] = bool(signed_out)
                self._register(s)
                if not s["signed_out"]:
                    s.pop("signed_out")
                self._save(data)
                return s
        raise LookupError(f"There is no schedule {schedule_id} on this PC")

    def rename_notebook(self, old: str, new: str) -> list[str]:
        """Points this PC's schedules that run notebook ``old`` at ``new`` (renamed or moved into a
        folder). Task Scheduler runs them by id, so their tasks stay as they are. Returns their ids."""
        data = self._load()
        moved = [s["id"] for s in data["schedules"] if s["target"].get("kind") == "notebook" and s["target"].get("name") == old]
        if moved:
            for s in data["schedules"]:
                if s["id"] in moved:
                    s["target"] = {**s["target"], "name": new}
            self._save(data)
        return moved

    def delete(self, schedule_id: str) -> None:
        data = self._load()
        data["schedules"] = [s for s in data["schedules"] if s["id"] != schedule_id]
        self.scheduler.unregister(schedule_id)
        self._save(data)

    def pause_disallowed(self) -> list[str]:
        """When IT turned off runs while signed out (allow_signed_out_schedules), pauses every signed-out
        schedule still on, so Windows stops starting them (pausing needs no password). Returns their ids."""
        if self.allow_signed_out:
            return []
        data = self._load()
        paused = []
        for s in [s for s in data["schedules"] if s.get("signed_out") and s.get("enabled", True)]:
            self.scheduler.set_enabled(s["id"], False)
            s["enabled"] = False
            self._save(data)  # each one as it's paused, so a refusal partway leaves the list true
            paused.append(s["id"])
        return paused

    def sync(self) -> int:
        """Point every task at this engine (after an update). Returns how many were re-registered."""
        data = self._load()
        if not data["schedules"] or data.get("engine") == self.executable:
            return 0
        # A signed-out task runs the app's exe, which finds the new engine itself; changing the task
        # would ask for the password, so it is left as it is.
        moved = [s for s in data["schedules"] if not s.get("signed_out")]
        for s in moved:
            self._register(s)
        data["engine"] = self.executable
        self._save(data)
        return len(moved)

    def runs(self, limit: int = 500) -> list[dict[str, Any]]:
        try:
            lines = self.runs_file.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        out = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out[::-1]

    def log_run(self, row: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.runs_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def listing(self, now: dt.datetime | None = None) -> list[dict[str, Any]]:
        """Every schedule with its next run and its latest result."""
        latest: dict[str, dict[str, Any]] = {}
        for r in self.runs():
            latest.setdefault(r["schedule_id"], r)
        schedules = self._load()["schedules"]
        results = self.scheduler.results() if any(s.get("signed_out") for s in schedules) else {}
        out = []
        for s in schedules:
            nxt = next_run(s["config"], now) if s.get("enabled", True) else None
            extra: dict[str, Any] = {}
            if s.get("signed_out") and not self.allow_signed_out:
                extra["signed_out_problem"] = "policy"
            elif s.get("signed_out"):
                problem = signed_out_tasks.needs_password((results.get(s["id"]) or {}).get("result"))
                if not problem and (s.get("launcher") or "") != (self.launcher or ""):
                    problem = "moved"  # the app's exe moved: the task must be saved again, with the password
                extra["signed_out_problem"] = problem
            out.append(s | extra | {"label": target_label(s["target"]), "text": describe_schedule({"configuration": s["config"]}).replace(" Local", ""),
                            "next_run": nxt.isoformat() if nxt else None, "last": latest.get(s["id"])})
        return out
