"""The headless agent's settings: ``agent.toml``.

    [agent]
    name = "etl-01"                       # this worker's name in run logs; the host name if left out
    owner = "Sales data team"             # optional: who looks after this agent, shown in the app
    project = "/srv/fabric/sales-metadata" # the metadata project folder (a Git checkout)
    environments = ["test", "prod"]       # the environments this agent may run
    concurrency = 1                       # jobs at once
    status_seconds = 60                   # how often to publish agents/<name>.json to each LH_Meta
    queue_retention_days = 30             # finished jobs older than this leave the queue (at start, then daily)
    workspaces = ["<workspace id>"]       # optional allowlist: refuse to write anywhere else

    [auth]
    type = "service_principal"            # or "managed_identity", or "user" (your cached sign-in)
    tenant_id = "<tenant id>"
    client_id = "<app id>"
    certificate_path = "/etc/fabric-desktop/agent.pem"

    [retry]
    max_attempts = 3
    backoff_seconds = 60

    [alerts]
    teams_webhook_env = "FABRIC_DESKTOP_TEAMS_WEBHOOK"

    [incidents]                           # optional (Preview): one incident per problem, shared with the app
    enabled = true                        # evaluate the run log of each environment; off when left out
    seconds = 300                         # how often
    digest_over = 5                       # more changes than this in one evaluation: one Teams digest
    quiet_hours = "22:00-06:00"           # optional: hold messages until the hours end (this machine's clock)
    quiet_except = ["critical"]           # criticalities sent even in quiet hours
    escalate_after_minutes = 60           # optional: post open, unacknowledged incidents to escalate_to
    escalate_to = "on-call"               # a route name

    [[incidents.route]]                   # optional: a team's channel; anything no route matches goes to
    name = "sales"                        # alerts.teams_webhook_env
    webhook_env = "SALES_TEAMS_WEBHOOK"
    teams = ["Sales data"]                # match on project team, owner, criticality, kinds, envs

    [pipeline]                            # optional: take run requests from Fabric pipelines
    enabled = true
    environments = ["prod"]               # whose LH_Meta mailbox to read; agent.environments if left out
    entities = ["silver.*", "gold.*"]     # optional allowlist; a request for anything else is rejected
    allow_backfill = false                # full rebuilds and cascades from a pipeline
    poll_seconds = 30
    claim = "always"                      # or "when_free": take a request only with a worker free (Preview)
    take_over = false                     # true: take over requests from an agent that stopped reporting (Preview)
    require_run = false                   # true: only take requests whose pipeline_run checks out in Fabric (Preview)

    [shortcut_health]                     # optional: check the environments' shortcuts, post each break to Teams
    enabled = true                        # off when left out
    minutes = 60                          # how often (at least 15)
    environments = ["prod"]               # agent.environments if left out
    webhook_env = "SHORTCUTS_TEAMS_WEBHOOK"  # alerts.teams_webhook_env if left out

    [[schedule]]
    id = "prod-nightly"
    env = "prod"
    daily = ["02:00"]                     # or every_minutes = 15, or weekly = { days = [...], times = [...] }
    shared = false                        # true: shared with other agents through the mailbox (Preview)

Secrets never go in this file: a certificate is a path, a secret or webhook is the name of an
environment variable that holds it.
"""

from __future__ import annotations

import fnmatch
import os
import re
import socket
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .fabric_run import schedule_configuration
from .local_schedules import local_configuration
from .meta.project import ENVIRONMENTS
from .redact import one_line

# Keys whose value would be a secret itself. Give the name of an environment variable instead.
_SECRET_KEYS = {"client_secret", "password", "secret", "certificate_password", "teams_webhook", "token", "api_key"}
_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")
# Every-N-minutes schedules count from here (local time), so 60 runs on the hour and 15 at :00, :15, :30 and :45.
GRID_START = "2000-01-01T00:00:00"


class ConfigError(ValueError):
    def __init__(self, path: Path | str, errors: list[str]):
        self.errors = errors
        super().__init__(f"{path} has {len(errors)} problem{'s' if len(errors) != 1 else ''}:\n- " + "\n- ".join(errors))


def default_home() -> Path:
    if os.environ.get("FABRIC_DESKTOP_HOME"):
        return Path(os.environ["FABRIC_DESKTOP_HOME"])
    if sys.platform == "win32":
        return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "FabricDesktop" / "agent"
    return Path("/var/lib/fabric-desktop")


def default_config_path() -> Path:
    if os.environ.get("FABRIC_DESKTOP_AGENT_CONFIG"):
        return Path(os.environ["FABRIC_DESKTOP_AGENT_CONFIG"])
    if sys.platform == "win32":
        return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "FabricDesktop" / "agent.toml"
    return Path("/etc/fabric-desktop/agent.toml")


@dataclass(frozen=True)
class Schedule:
    id: str
    env: str
    entities: list[str]
    config: dict[str, Any]  # the same shape local_schedules.next_run reads
    backfill: str = ""
    when: dict[str, Any] = field(default_factory=dict)  # as written: {"kind": "cron"|"daily"|"weekly", ...}
    shared: bool = False  # each due time goes to the mailbox as one request any agent may claim


@dataclass(frozen=True)
class PipelineConfig:
    """``[pipeline]``: run requests that Fabric pipelines write to an environment's LH_Meta (see mailbox.py)."""
    enabled: bool = False
    environments: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    allow_backfill: bool = False
    poll_seconds: float = 30.0
    retention_days: float = 14.0
    claim: str = "always"  # or "when_free": only claim a request while a worker is free
    take_over: bool = False  # take over requests held by an agent that stopped reporting (mailbox.py)
    require_run: bool = False  # reject requests whose pipeline_run doesn't check out in Fabric (mailbox.py)


@dataclass(frozen=True)
class IncidentsConfig:
    """``[incidents]``: evaluate each environment's run log and keep its incident list (workloads/incidents.py)."""
    enabled: bool = False
    seconds: float = 300.0
    digest_over: int = 5
    quiet_hours: str = ""
    quiet_except: tuple[str, ...] = ("critical",)
    escalate_after_minutes: float = 0.0
    escalate_to: str = ""
    routes: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ShortcutHealthConfig:
    """``[shortcut_health]``: check the shortcuts in each environment's workspaces (shortcut_watch.py)."""
    enabled: bool = False
    minutes: float = 60.0
    environments: list[str] = field(default_factory=list)
    webhook_env: str = ""


@dataclass(frozen=True)
class AgentConfig:
    path: Path
    name: str
    home: Path
    project: Path
    environments: list[str]
    auth: dict[str, Any]
    owner: str = ""
    concurrency: int = 1
    poll_seconds: float = 5.0
    job_lease_seconds: float = 300.0
    table_lease_seconds: float = 120.0
    status_seconds: float = 60.0
    queue_retention_days: float = 30.0
    max_attempts: int = 3
    backoff_seconds: float = 60.0
    max_backoff_seconds: float = 1800.0
    workspaces: list[str] = field(default_factory=list)
    teams_webhook_env: str = ""
    log_level: str = "INFO"
    schedules: list[Schedule] = field(default_factory=list)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    incidents: IncidentsConfig = field(default_factory=IncidentsConfig)
    shortcut_health: ShortcutHealthConfig = field(default_factory=ShortcutHealthConfig)

    @property
    def teams_webhook(self) -> str:
        return os.environ.get(self.teams_webhook_env, "") if self.teams_webhook_env else ""

    @property
    def shortcut_webhook(self) -> str:
        """Where shortcut breaks go: shortcut_health.webhook_env, else alerts.teams_webhook_env."""
        name = self.shortcut_health.webhook_env or self.teams_webhook_env
        return os.environ.get(name, "") if name else ""


def _secret_keys(table: Any, where: str) -> list[str]:
    found = []
    if isinstance(table, dict):
        for k, v in table.items():
            if k.lower() in _SECRET_KEYS:
                found.append(f"{where}{k}: secrets don't go in agent.toml; put it in an environment variable "
                             f"and give its name as {k}_env")
            found += _secret_keys(v, f"{where}{k}.")
    elif isinstance(table, list):
        for i, v in enumerate(table):
            found += _secret_keys(v, f"{where}[{i}].")
    return found


_QUIET = re.compile(r"([01]?\d|2[0-3]):[0-5]\d-([01]?\d|2[0-3]):[0-5]\d")
_ROUTE_FILTERS = ("teams", "owners", "criticality", "kinds", "envs")


def _routes(raw: Any, errors: list[str]) -> tuple[dict[str, Any], ...]:
    """``[[incidents.route]]``: a name, the variable holding its webhook, and what it matches."""
    out = []
    for i, r in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(r, dict):
            errors.append(f"incidents.route {i + 1}: write it as [[incidents.route]] with a name and webhook_env")
            continue
        where = f"incidents.route {one_line(r.get('name') or i + 1, 80)}"
        name = str(r.get("name") or "")
        if not _ID.fullmatch(name) or name == "default":
            errors.append(f"{where}: name is letters, digits, . _ or - (and not \"default\")")
            continue
        if any(k in r for k in ("url", "webhook")):
            errors.append(f"{where}: give the webhook as webhook_env, the name of an environment variable")
            continue
        if not r.get("webhook_env"):
            errors.append(f"{where}: webhook_env is required")
            continue
        unknown = set(r) - {"name", "webhook_env", *_ROUTE_FILTERS}
        if unknown:
            errors.append(f"{where}: doesn't know {', '.join(sorted(unknown))}")
        out.append({"name": name, "webhook_env": str(r["webhook_env"]),
                    **{k: tuple(str(x) for x in r.get(k) or []) for k in _ROUTE_FILTERS}})
    names = [r["name"] for r in out]
    errors += [f"incidents.route {n}: the name is used twice" for n in sorted({n for n in names if names.count(n) > 1})]
    return tuple(out)


def _schedule(raw: dict[str, Any], i: int, errors: list[str]) -> Schedule | None:
    where = f"schedule {one_line(raw.get('id') or i + 1, 80)}"
    sid, env = str(raw.get("id", "")), raw.get("env")
    if not _ID.fullmatch(sid):
        errors.append(f"{where}: id is letters, digits, . _ or -")
        return None
    if env not in ENVIRONMENTS:
        errors.append(f"{where}: env is one of {', '.join(ENVIRONMENTS)}")
        return None
    kinds = [k for k in ("every_minutes", "daily", "weekly") if k in raw]
    if len(kinds) != 1:
        errors.append(f"{where}: give exactly one of every_minutes, daily or weekly")
        return None
    try:
        if kinds[0] == "every_minutes":
            fabric_cfg = schedule_configuration("cron", every_minutes=raw["every_minutes"])
            schedule = {"kind": "cron", "every_minutes": raw["every_minutes"]}
        elif kinds[0] == "daily":
            fabric_cfg = schedule_configuration("daily", times=list(raw["daily"]))
            schedule = {"kind": "daily", "times": list(raw["daily"])}
        else:
            weekly = raw["weekly"] or {}
            fabric_cfg = schedule_configuration("weekly", times=list(weekly.get("times", [])),
                                                weekdays=list(weekly.get("days", [])))
            schedule = {"kind": "weekly", "times": list(weekly.get("times", [])), "weekdays": list(weekly.get("days", []))}
    except (ValueError, TypeError) as exc:
        errors.append(f"{where}: {exc}")
        return None
    del fabric_cfg  # validated; the agent keeps local time, as Task Scheduler does
    entities = [str(e) for e in raw.get("entities") or []]
    shared = raw.get("shared", False)
    if not isinstance(shared, bool):
        errors.append(f"{where}: shared is true or false")
        return None
    if shared and len(sid) > 40:
        errors.append(f"{where}: a shared schedule's id is at most 40 characters")
        return None
    minutes = raw.get("every_minutes")
    if shared and minutes is not None and (isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1):
        errors.append(f"{where}: a shared schedule's every_minutes is a whole number of at least 1")  # slots are named to the minute
        return None
    config = local_configuration(schedule)
    config["startDateTime"] = GRID_START  # not the parse time: every N minutes keeps its times across restarts
    return Schedule(sid, env, entities, config, str(raw.get("backfill", "")), schedule, shared)


def _shortcut_health(raw: dict[str, Any], envs: list[str], alerts_env: str, number: Callable[..., float],
                     errors: list[str]) -> ShortcutHealthConfig:
    """``[shortcut_health]``. Turning it on needs somewhere to post: a check nobody hears about is no alert."""
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        errors.append("shortcut_health.enabled: true or false")
    if any(k in raw for k in ("url", "webhook")):
        errors.append("shortcut_health: give the webhook as webhook_env, the name of an environment variable")
    unknown = set(raw) - {"enabled", "minutes", "environments", "webhook_env", "url", "webhook"}
    if unknown:
        errors.append(f"shortcut_health: doesn't know {', '.join(sorted(unknown))}")
    given = raw.get("environments")
    if given is not None and not isinstance(given, list):
        errors.append('shortcut_health.environments: a list, like ["prod"]')
        given = None
    sh_envs = [str(e) for e in given or envs]
    errors += [f"shortcut_health.environments: {e} isn't in agent.environments" for e in sh_envs if e not in envs]
    webhook_env = str(raw.get("webhook_env") or "")
    if enabled is True and not (webhook_env or alerts_env):
        errors.append("shortcut_health: give webhook_env (or alerts.teams_webhook_env) so breaks go to a Teams channel")
    return ShortcutHealthConfig(enabled=enabled is True, minutes=number(raw, "minutes", 60, 15, "shortcut_health"),
                                environments=sh_envs, webhook_env=webhook_env)


def host_name() -> str:
    """The default agent name: this machine's name, with anything agent.name can't hold made a ``-``."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", socket.gethostname())[:64] or "agent"


def load(path: Path | str | None = None) -> AgentConfig:
    path = Path(path) if path else default_config_path()
    if not path.exists():
        raise FileNotFoundError(f"No agent config at {path}. Copy agent.example.toml there, or pass --config.")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(path, [f"not valid TOML: {exc}"]) from None
    return parse(raw, path)


def _is_file(path: Path) -> bool:
    """Whether ``path`` exists; a path the system refuses to look up (too long, a bad character) doesn't."""
    try:
        return path.exists()
    except (OSError, ValueError):
        return False


def parse(raw: dict[str, Any], path: Path | str = "agent.toml") -> AgentConfig:
    errors = _secret_keys(raw, "")
    agent, auth, retry, alerts, pipe, inc, sh = (raw.get(k) or {} for k in ("agent", "auth", "retry", "alerts", "pipeline",
                                                                          "incidents", "shortcut_health"))
    project = agent.get("project", "")
    if not project:
        errors.append("agent.project: the metadata project folder is required")
    elif not _is_file(Path(project) / "layers.toml"):
        errors.append(f"agent.project: {one_line(project, 200)} isn't a metadata project (no layers.toml)")
    envs = list(agent.get("environments") or [])
    if not envs:
        errors.append("agent.environments: list the environments this agent may run, e.g. [\"prod\"]")
    errors += [f"agent.environments: {e!r} isn't one of {', '.join(ENVIRONMENTS)}" for e in envs if e not in ENVIRONMENTS]
    if auth.get("type") not in ("service_principal", "managed_identity", "user"):
        errors.append('auth.type: "service_principal", "managed_identity" or "user"')
    if auth.get("type") == "service_principal":
        if not auth.get("tenant_id") or not auth.get("client_id"):
            errors.append("auth: a service principal needs tenant_id and client_id")
        if not auth.get("certificate_path") and not auth.get("client_secret_env"):
            errors.append("auth: give certificate_path (preferred) or client_secret_env")
        elif auth.get("certificate_path") and not _is_file(Path(auth["certificate_path"])):
            errors.append(f"auth.certificate_path: {one_line(auth['certificate_path'], 200)} doesn't exist")

    def number(table: dict[str, Any], key: str, default: float, low: float, where: str) -> float:
        value = table.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < low:
            errors.append(f"{where}.{key}: a number of at least {low:g}")
            return default
        return float(value)

    concurrency = int(number(agent, "concurrency", 1, 1, "agent"))
    schedules = [s for i, r in enumerate(raw.get("schedule") or []) if (s := _schedule(r, i, errors))]
    ids = [s.id for s in schedules]
    errors += [f"schedule {i}: the id is used twice" for i in sorted({i for i in ids if ids.count(i) > 1})]
    errors += [f"schedule {s.id}: {s.env} isn't in agent.environments" for s in schedules if s.env not in envs]
    pipe_envs = [str(e) for e in pipe.get("environments") or envs]
    errors += [f"pipeline.environments: {one_line(e, 80)} isn't in agent.environments" for e in pipe_envs if e not in envs]
    for key in ("enabled", "allow_backfill", "take_over", "require_run"):
        if not isinstance(pipe.get(key, False), bool):
            errors.append(f"pipeline.{key}: true or false")
    pipeline = PipelineConfig(enabled=pipe.get("enabled") is True, environments=pipe_envs,
                              entities=[str(e) for e in pipe.get("entities") or []],
                              allow_backfill=pipe.get("allow_backfill") is True,
                              poll_seconds=number(pipe, "poll_seconds", 30, 1, "pipeline"),
                              retention_days=number(pipe, "retention_days", 14, 1, "pipeline"),
                              claim=str(pipe.get("claim", "always")), take_over=pipe.get("take_over") is True,
                              require_run=pipe.get("require_run") is True)
    if pipeline.claim not in ("always", "when_free"):
        errors.append('pipeline.claim: "always" or "when_free"')
    for s in schedules:
        if not s.shared:
            continue
        where = f"schedule {s.id}"
        if not pipeline.enabled or s.env not in pipeline.environments:
            errors.append(f"{where}: a shared schedule goes through the {s.env} mailbox; enable [pipeline] for {s.env}")
        if s.backfill and not pipeline.allow_backfill:
            errors.append(f"{where}: a shared backfill needs pipeline.allow_backfill = true")
        if pipeline.require_run:
            errors.append(f"{where}: a shared schedule's requests name no pipeline run, so pipeline.require_run must be false")
        if pipeline.entities:
            if not s.entities:
                errors.append(f"{where}: list its entities; pipeline.entities only takes {', '.join(pipeline.entities)}")
            refused = [e for e in s.entities if not any(fnmatch.fnmatchcase(e, p) for p in pipeline.entities)]
            if refused:
                errors.append(f"{where}: {', '.join(refused)} isn't in pipeline.entities")
    if not isinstance(inc.get("enabled", False), bool):
        errors.append("incidents.enabled: true or false")
    routes = _routes(inc.get("route") or [], errors)
    quiet = str(inc.get("quiet_hours") or "")
    if quiet and not _QUIET.fullmatch(quiet):
        errors.append('incidents.quiet_hours: two times like "22:00-06:00"')
    escalate_to = str(inc.get("escalate_to") or "")
    escalate_after = number(inc, "escalate_after_minutes", 0, 0, "incidents")
    if escalate_after and not escalate_to:
        errors.append("incidents.escalate_to: name the route escalations go to")
    elif escalate_to and escalate_to not in {r["name"] for r in routes}:
        errors.append(f"incidents.escalate_to: there is no [[incidents.route]] named {escalate_to!r}")
    incidents = IncidentsConfig(enabled=inc.get("enabled") is True,
                                seconds=number(inc, "seconds", 300, 30, "incidents"),
                                digest_over=int(number(inc, "digest_over", 5, 1, "incidents")),
                                quiet_hours=quiet, quiet_except=tuple(str(x).lower() for x in inc.get("quiet_except", ["critical"])),
                                escalate_after_minutes=escalate_after, escalate_to=escalate_to, routes=routes)
    shortcut_health = _shortcut_health(sh, envs, str(alerts.get("teams_webhook_env", "")), number, errors)
    name = agent.get("name") or host_name()
    if not isinstance(name, str) or not _ID.fullmatch(name):  # the mailbox takes over only such names
        errors.append(f"agent.name: {name!r} isn't 1 to 64 letters, digits, . _ or - (it names agents/<name>.json)")
    cfg = AgentConfig(
        path=Path(path), name=name,
        home=Path(agent["home"]) if agent.get("home") else default_home(),
        project=Path(project), environments=envs, auth=dict(auth), owner=str(agent.get("owner") or "").strip(),
        concurrency=concurrency,
        poll_seconds=number(agent, "poll_seconds", 5, 0.1, "agent"),
        job_lease_seconds=number(agent, "job_lease_seconds", 300, 30, "agent"),
        table_lease_seconds=number(agent, "table_lease_seconds", 120, 15, "agent"),
        status_seconds=number(agent, "status_seconds", 60, 5, "agent"),
        queue_retention_days=number(agent, "queue_retention_days", 30, 1, "agent"),
        max_attempts=int(number(retry, "max_attempts", 3, 1, "retry")),
        backoff_seconds=number(retry, "backoff_seconds", 60, 0, "retry"),
        max_backoff_seconds=number(retry, "max_backoff_seconds", 1800, 0, "retry"),
        workspaces=[str(w) for w in agent.get("workspaces") or []],
        teams_webhook_env=str(alerts.get("teams_webhook_env", "")),
        log_level=str(agent.get("log_level", "INFO")).upper(), schedules=schedules, pipeline=pipeline,
        incidents=incidents, shortcut_health=shortcut_health)
    if errors:
        raise ConfigError(path, errors)
    return cfg
