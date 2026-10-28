"""Command line entry point: `fabric-desktop serve | preview JOB | run JOB | history | run-schedule ID | governance check | agent ... | live-test ... | support-bundle | audit | backup | restore | mcp | capabilities | integrations serve`."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

from .server import main as serve
from .service import Service


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="fabric-desktop")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the JSON-lines server for the desktop app")
    sub.add_parser("sign-in", help="sign in to Fabric and OneLake")
    for name in ("preview", "run"):
        p = sub.add_parser(name, help=f"{name} a job file")
        p.add_argument("job")
    sub.add_parser("history", help="show recent runs")
    p = sub.add_parser("run-schedule", help="run one of this PC's schedules (Windows Task Scheduler calls this)")
    p.add_argument("schedule_id")
    p.add_argument("--manual", action="store_true", help="started with Run now, not by the schedule")
    agent = sub.add_parser("agent", help="the headless agent: run, check, enqueue, status, requeue, prune")
    agent.add_argument("--config", help="agent.toml (default: FABRIC_DESKTOP_AGENT_CONFIG, then "
                                        "%%ProgramData%%\\FabricDesktop\\agent.toml or /etc/fabric-desktop/agent.toml)")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_sub.add_parser("run", help="run until stopped (the service command)")
    agent_sub.add_parser("check", help="check agent.toml, the project and the sign-in, then exit")
    agent_sub.add_parser("status", help="queue counts, schedules and the latest jobs")
    p = agent_sub.add_parser("enqueue", help="queue a run of an environment")
    p.add_argument("env")
    p.add_argument("entities", nargs="*", help="entities to run (default: all)")
    p.add_argument("--backfill", default="", choices=["", "full"], help="full: rebuild from the whole source")
    p.add_argument("--cascade", action="store_true", help="with one entity: rebuild it and everything downstream")
    p = agent_sub.add_parser("requeue", help="put a dead or failed job back in the queue, attempts reset")
    p.add_argument("job_id")
    p = agent_sub.add_parser("prune", help="remove finished jobs older than agent.queue_retention_days "
                                           "(the agent does this at start and daily)")
    p.add_argument("--days", type=float, help="older than this many days (default: agent.queue_retention_days)")
    p.add_argument("--dry-run", action="store_true", help="only list what would be removed")
    p = sub.add_parser("clean-local", help="delete local copies of lakehouse data unused for a while "
                                           "(the app does this at start)")
    p.add_argument("--days", type=int, help="older than this many days (default: the local_retention_days setting)")
    p.add_argument("--dry-run", action="store_true", help="only list what would be deleted")
    p = sub.add_parser("backup", help="save this PC's settings, notebooks, schedules and history to a zip "
                                      "(never sign-in tokens)")
    p.add_argument("--out", default=".", help="folder to save it in (default: here)")
    p.add_argument("--agent-home", help="also back up this agent's queue and alerts (its FABRIC_DESKTOP_HOME)")
    p = sub.add_parser("restore", help="check a backup and show what it would restore; --yes restores it")
    p.add_argument("backup")
    p.add_argument("--agent-home", help="also restore the agent's queue and alerts into this folder")
    p.add_argument("--yes", action="store_true", help="restore it (close the app first)")
    live = sub.add_parser("live-test", help="check sign-in, the Fabric API, OneLake and Delta against a test workspace")
    live.add_argument("--workspace", help="the test workspace's id; its description must contain fabric-desktop-live-test")
    live.add_argument("--dry-run", action="store_true", help="say what it would do and write nothing")
    live.add_argument("--yes", action="store_true", help="run without asking first")
    live.add_argument("--out", default="live-test-results", help="folder for results (default: live-test-results)")
    live.add_argument("--agent-config", help="sign in as this agent.toml's identity instead of as you")
    live.add_argument("--cleanup", metavar="LEDGER", help="delete what an earlier run's ledger.json lists, then exit")
    live.add_argument("--all", action="store_true", help="also run the opt-in checks (concurrent Delta writers, "
                      "notebook publish, pipeline definition); they create items and use capacity")
    live.add_argument("--check", action="append", default=[], metavar="ID",
                      choices=["delta.concurrent", "notebook.publish", "pipeline.definition"],
                      help="also run this opt-in check (repeat for more)")
    live.add_argument("--run-in-fabric", action="store_true",
                      help="also run the published test notebook in Fabric; spends Spark capacity")
    bundle = sub.add_parser("support-bundle", help="save a zip of versions, settings sources and recent logs, "
                                                   "with secrets and personal details taken out")
    bundle.add_argument("--out", default=".", help="folder to save it in (default: here)")
    bundle.add_argument("--agent-home", help="also include this agent's log (its FABRIC_DESKTOP_HOME)")
    trail = sub.add_parser("audit", help="show this PC's audit trail, or check that no line was changed or removed")
    trail.add_argument("--verify", action="store_true", help="check the hash chain and exit 1 if it is broken")
    trail.add_argument("--limit", type=int, default=50, help="how many of the newest lines to show (default 50)")
    trail.add_argument("--action", default="", help="only this action, such as meta_promote")
    trail.add_argument("--folder", help="the audit folder (default: this PC's, or an agent home's audit folder)")
    p = sub.add_parser("mcp", help="serve the Model Context Protocol on stdin/stdout, for an external AI tool to start")
    p.add_argument("--client", default="", help="the client id Settings > Integrations gave this tool")
    sub.add_parser("capabilities", help="print every capability external tools can use, with schemas, as JSON")
    integ = sub.add_parser("integrations", help="external AI tools: the local HTTP API")
    integ_sub = integ.add_subparsers(dest="integrations_command", required=True)
    p = integ_sub.add_parser("serve", help="run the local HTTP API on 127.0.0.1 (the app starts this when it's switched on)")
    p.add_argument("--port", type=int, default=47615)
    p.add_argument("--parent-pid", type=int, default=0, help="stop when this process ends")
    pipe = sub.add_parser("pipeline", help="read and check a Fabric pipeline definition on this PC (nothing runs)")
    pipe_sub = pipe.add_subparsers(dest="pipeline_command", required=True)
    for name, text in (("validate", "list problems with their JSON paths; exit 1 if there are errors"),
                       ("inspect", "print the activities, dependencies, expressions and diagnostics as JSON"),
                       ("export", "print the definition in another format"),
                       ("compat", "what this version can do with each activity (classification, capacity, "
                                  "properties); exit 1 if there are errors")):
        p = pipe_sub.add_parser(name, help=text)
        p.add_argument("path", help="pipeline-content.json, a getDefinition result, or a <name>.DataPipeline folder")
        if name == "export":
            p.add_argument("--format", choices=["pipeline_content", "item_definition", "git_folder"])
        if name == "compat":
            p.add_argument("--json", action="store_true", help="print the whole report as JSON")
    p = pipe_sub.add_parser("activities", help="print the activity registry, or one type's entry, as JSON")
    p.add_argument("type", nargs="?", help="an activity type, such as Copy or TridentNotebook")
    p = pipe_sub.add_parser("run", help="run the pipeline on this PC (control flow, child pipelines, notebooks and "
                                        "mocks; no Fabric job is started); exit 0 if it succeeds")
    p.add_argument("path", help="pipeline-content.json, a getDefinition result, or a <name>.DataPipeline folder")
    p.add_argument("--param", action="append", default=[], metavar="NAME=VALUE", help="a pipeline parameter")
    p.add_argument("--mocks", help="JSON file: {activity name: {status, output, error, seconds}}")
    p.add_argument("--child", action="append", default=[], metavar="KEY=PATH",
                   help="a child pipeline's definition, by referenceName or pipelineId")
    p.add_argument("--max-parallel", type=int, default=4)
    p.add_argument("--wait-scale", type=float, default=1.0, help="multiply every wait by this (0 for none)")
    p.add_argument("--timeout", type=float, help="stop the run after this many seconds")
    p.add_argument("--notebook", action="append", default=[], metavar="KEY=NOTEBOOK",
                   help="run a Notebook activity (by activity name or notebookId) with a notebook saved in "
                        "KratosForge; its writes go to the run's own folder in the lakehouse")
    p.add_argument("--allow-uncontained", action="store_true",
                   help="let notebooks call the network or read files on this PC")
    p.add_argument("--keep-outputs", action="store_true", help="keep the run's folder in the lakehouse")
    p.add_argument("--read-lakehouse", action="store_true",
                   help="let Lookup and Get Metadata read the lakehouse their dataset names (reads only)")
    p.add_argument("--workspace", metavar="ID", help="the pipeline's own workspace, for datasets that leave it out")
    p.add_argument("--sql-project", action="append", default=[], metavar="KEY=PROJECT",
                   help="run a Script activity (by activity or connection name) in a local data project")
    p.add_argument("--json", action="store_true", help="print the whole run record as JSON")
    p = pipe_sub.add_parser("schedule", help="run a <name>.DataPipeline folder on this PC on a schedule (Windows "
                                             "Task Scheduler); every step must run here or have a mock")
    p.add_argument("path", help="a <name>.DataPipeline folder; it is read again at every run")
    when = p.add_mutually_exclusive_group(required=True)
    when.add_argument("--at", action="append", metavar="HH:MM", help="a time of day (daily, or on --weekday days)")
    when.add_argument("--every-minutes", type=int, metavar="N", help="every N minutes")
    p.add_argument("--weekday", action="append", default=[], metavar="DAY", help="Monday ... Sunday (with --at)")
    p.add_argument("--param", action="append", default=[], metavar="NAME=VALUE",
                   help="a pipeline parameter (SecureString values are never kept; the default applies)")
    p.add_argument("--mocks", help="JSON file: {activity name: {status, output, error, seconds}}")
    p.add_argument("--notebook", action="append", default=[], metavar="KEY=NOTEBOOK",
                   help="run a Notebook activity with a notebook saved in KratosForge")
    p.add_argument("--read-lakehouse", action="store_true",
                   help="let Lookup, Get Metadata and Script read the lakehouse their dataset names (reads only)")
    p.add_argument("--workspace", metavar="ID", help="the pipeline's own workspace, for datasets that leave it out")
    p.add_argument("--sql-project", action="append", default=[], metavar="KEY=PROJECT",
                   help="run a Script activity (by activity or connection name) in a local data project")
    gov = sub.add_parser("governance", help="governance policies: check a project offline, or take its policy package")
    gov_sub = gov.add_subparsers(dest="governance_command", required=True)
    p = gov_sub.add_parser("check", help="check the project against its policies from a policy package, with no platform, "
                                         "Fabric or OneLake call; exit 1 if an Error finding isn't excepted")
    p.add_argument("--package", help="a policy package file (default: the latest one on this PC for the project)")
    p.add_argument("--expect-sha256", metavar="HASH",
                   help="refuse the package unless its SHA-256 is this (default: KRATOSFORGE_POLICY_PACKAGE_SHA256); "
                        "keep it where a pull request can't change it, such as a protected CI variable")
    p.add_argument("--max-age", type=float, metavar="DAYS", help="refuse a package taken more than this many days ago")
    p.add_argument("--project", help="the metadata project's folder (default: the project open in the app)")
    p.add_argument("--json", action="store_true", help="print the whole result as JSON")
    p = gov_sub.add_parser("package", help="take the project's policy package from the platform (it reads the platform) "
                                           "and keep it on this PC; --out also writes it there")
    p.add_argument("--out", help="also write the package to this file, such as one a CI job reads with --package")
    p.add_argument("--project", help="the metadata project's folder (default: the project open in the app)")
    nb = sub.add_parser("nb", help="notebooks: check an .ipynb file on this PC (nothing runs)")
    nb_sub = nb.add_subparsers(dest="nb_command", required=True)
    p = nb_sub.add_parser("validate", help="list what Validate finds, one line per finding as file:line:column: "
                                           "severity: message; exit 1 if there are errors")
    p.add_argument("path", nargs="?", help="an .ipynb file")
    p.add_argument("--path-from-env", metavar="NAME",
                   help="read the file's path from this environment variable (VS Code's task does, so no shell reads it)")
    p.add_argument("--runs-on", choices=["pc", "fabric"],
                   help="where it will run (default: the notebook's own Runs on, if it's set)")
    p.add_argument("--json", action="store_true", help="print the whole result as JSON")
    p = nb_sub.add_parser("open", help="show an .ipynb file in KratosForge (a kratosforge:// link; nothing runs)")
    p.add_argument("path", nargs="?", help="an .ipynb file in the notebooks folder or a notebooks repository")
    p.add_argument("--path-from-env", metavar="NAME", help="read the file's path from this environment variable")
    p.add_argument("--print", action="store_true", help="print the link instead of opening it")
    args = parser.parse_args(argv)

    if args.command == "mcp":
        from .integrations.mcp_server import run_stdio
        run_stdio(args.client)
        return
    if args.command == "capabilities":
        from .integrations.registry import default_registry
        print(json.dumps({"capabilities": [c.describe() for c in default_registry().all()]}, indent=2))
        return
    if args.command == "integrations":
        from .integrations.http_server import serve as serve_http
        raise SystemExit(serve_http(args.port, args.parent_pid))
    if args.command == "pipeline":
        raise SystemExit(_pipeline(args))
    if args.command == "governance":
        raise SystemExit(_governance(args))
    if args.command == "nb":
        raise SystemExit(_nb_open(args) if args.nb_command == "open" else _nb_validate(args))
    if args.command == "serve":
        serve()
        return
    if args.command == "agent":
        raise SystemExit(_agent(args))
    if args.command == "clean-local":
        from . import app_settings, retention
        from .service import data_dir
        days = args.days if args.days is not None else app_settings.value("local_retention_days")
        result = retention.sweep(data_dir(), days, dry_run=args.dry_run)
        verb = "Would delete" if args.dry_run else "Deleted"
        print(f"{verb} {len(result['removed'])} local copies ({result['bytes'] / 1e6:.1f} MB) unused for {days} days")
        for path in result["removed"]:
            print(f"  {path}")
        return
    if args.command == "live-test":
        raise SystemExit(_live_test(args))
    if args.command == "support-bundle":
        from pathlib import Path

        from .support_bundle import create
        print(create(Path(args.out), agent_home=Path(args.agent_home) if args.agent_home else None))
        return
    if args.command == "audit":
        raise SystemExit(_audit(args))
    if args.command in ("backup", "restore"):
        raise SystemExit(_backup(args))
    if args.command == "run-schedule":
        _run_schedule(args.schedule_id, "manual" if args.manual else "schedule")
        return
    service = Service()
    result = {
        "sign-in": lambda: service.sign_in(),
        "preview": lambda: service.preview_job(args.job, limit=10),
        "run": lambda: service.run_job(args.job),
        "history": lambda: service.history(),
    }[args.command]()
    print(json.dumps(result, indent=2, default=str))


def _agent(args: argparse.Namespace) -> int:
    from . import agent_config, logs
    from .agent import Agent

    try:
        config = agent_config.load(args.config)
    except (FileNotFoundError, agent_config.ConfigError) as exc:
        print(exc)
        return 2
    if args.agent_command == "run":
        log = logs.setup(config.name, config.home / "logs", config.log_level)
        Agent(config, log=log).run_forever()
        return 0
    if args.agent_command == "check":
        from .runtime import Runtime
        try:
            print(json.dumps(Runtime(config).check(), indent=2, default=str))
        except Exception as exc:
            print(f"Check failed: {type(exc).__name__}: {exc}")
            return 1
        return 0
    agent = Agent(config)
    if args.agent_command == "enqueue":
        result: object = agent.enqueue(args.env, args.entities, args.backfill, args.cascade, source="cli")
        agent.audit.record(
            "agent_enqueue", "ok", 0.0, {"env": args.env, "entities": args.entities, "backfill": args.backfill,
                                         "cascade": args.cascade, "agent": config.name})
    elif args.agent_command == "requeue":
        by = f"{agent.audit.os_user or 'unknown'} (cli)"
        try:
            job = agent.queue.requeue(args.job_id, by)
        except (LookupError, ValueError) as exc:
            print(exc)
            return 1
        agent.audit.record("agent_requeue", "ok", 0.0, {"job_id": args.job_id, "agent": config.name}, who=by)
        result = {k: job[k] for k in ("id", "state", "attempts", "requeued_by")}
    elif args.agent_command == "prune":
        if args.days is not None and args.days < 0:
            print("--days: a number of at least 0")
            return 2
        days = config.queue_retention_days if args.days is None else args.days
        gone = agent.queue.prune(days, dry_run=args.dry_run)
        if not args.dry_run:
            agent.audit.record("agent_prune", "ok", 0.0, {"jobs": len(gone), "days": days, "agent": config.name})
        result = {"dry_run": args.dry_run, "older_than_days": days, "jobs": gone}
    else:
        result = agent.status()
    print(json.dumps(result, indent=2, default=str))
    return 0


def _backup(args: argparse.Namespace) -> int:
    from pathlib import Path

    from . import backup
    from .service import data_dir

    home = Path(args.agent_home) if args.agent_home else None
    if args.command == "backup":
        result = backup.create(Path(args.out), data_dir(), home)
        print(f"Saved {result['files']} files to {result['path']}")
        for item in result["cleaned"]:
            print(f"  {item['path']}: {item['removed']} left out")
        return 0
    try:
        print(backup.describe(backup.restore(Path(args.backup), data_dir(), home, apply=args.yes)), end="")
    except backup.BackupError as exc:
        print(exc)
        return 1
    return 0


def _live_test(args: argparse.Namespace) -> int:
    """0 every check passed, 1 a check failed, 2 refused or not started."""
    from pathlib import Path

    from . import livetest
    from .fabric_api import FabricClient
    from .onelake import FileStore

    if not args.workspace and not args.cleanup:
        print("Name the test workspace with --workspace <id>.")
        return 2
    extra = set(args.check) | (set(livetest.ALL_CHECKS) if args.all else set())
    if args.dry_run:
        print("The live test will:")
        for step in livetest.LiveTest(args.workspace, None, None, None, Path(args.out), bad_token_files=object(),
                                      extra=extra, run_in_fabric=args.run_in_fabric).plan():
            print(f"  - {step}")
        print("Dry run: nothing was signed in to, created or written.")
        return 0
    if args.agent_config:
        from . import agent_config, credentials
        credential = credentials.from_config(agent_config.load(args.agent_config).auth)
    else:
        from . import app_settings
        from .auth import Authenticator
        credential = Authenticator(app_settings.value("client_id"))
    fabric = FabricClient(credential.fabric_token)
    if args.cleanup:
        result = livetest.cleanup_from_ledger(Path(args.cleanup), fabric, credential)
        print(json.dumps(result, indent=2))
        return 1 if result["left_behind"] else 0
    test = livetest.LiveTest(args.workspace, fabric, FileStore(credential.storage_token), credential, Path(args.out),
                             protected_ids=livetest.policy_workspace_ids(), extra=extra,
                             run_in_fabric=args.run_in_fabric)
    print("The live test will:")
    for step in test.plan():
        print(f"  - {step}")
    if not args.yes and input("Run it? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Not run.")
        return 2
    result = test.run()
    print(livetest.summary(result))
    print("\n".join(result["files"]))
    return {"passed": 0, "failed": 1}.get(result["outcome"], 2)


def _path_arg(args: argparse.Namespace) -> str:
    """The file an `nb` command names: its argument, or the variable --path-from-env names (VS Code's
    tasks pass ${file} that way, so cmd.exe never reads a name such as a&b.ipynb)."""
    import os

    raw = os.environ.get(args.path_from_env, "") if args.path_from_env else (args.path or "")
    if not raw:
        print("Name an .ipynb file" + (f": {args.path_from_env} isn't set." if args.path_from_env else "."), file=sys.stderr)
    return raw


def _nb_open(args: argparse.Namespace) -> int:
    """Asks Windows to open a kratosforge:// link for the file; the app shows it (deep_link.rs)."""
    import os
    from pathlib import Path
    from urllib.parse import quote

    raw = _path_arg(args)
    if not raw:
        return 2
    path = Path(raw).resolve()
    if path.suffix.lower() != ".ipynb" or not path.is_file():
        print(f"{path} isn't an .ipynb file.", file=sys.stderr)
        return 2
    link = "kratosforge://notebook?path=" + quote(path.as_posix(), safe="")
    if args.print or os.name != "nt":
        print(link)
        return 0
    os.startfile(link)  # type: ignore[attr-defined]  # Windows only
    return 0


def _nb_validate(args: argparse.Namespace) -> int:
    """Validate's code checks on one file (validate.check), printed the way VS Code's problem
    matcher in .vscode/tasks.json reads them (vscode_setup.PROBLEM_PATTERN). Findings point at the
    file's first line and name the cell in the message, since an .ipynb has no line per cell."""
    from pathlib import Path

    from . import validate
    from .notebook import from_ipynb
    from .service import data_dir

    raw = _path_arg(args)
    if not raw:
        return 2
    path = Path(raw).resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("cells"), list):
            raise ValueError("it isn't a Jupyter notebook (.ipynb) file")
        nb = from_ipynb(data)
    except (OSError, UnicodeDecodeError, ValueError, AttributeError, TypeError) as exc:
        print(f"{path}:1:1: error: KratosForge can't read this notebook: {exc}")
        return 2
    try:
        observed = json.loads((data_dir() / "fabric_environment.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        observed = {}
    runs_on = args.runs_on or {"local": "pc", "fabric": "fabric"}.get(nb["compute"])
    result = validate.check(nb["cells"], nb["lakehouse"], runs_on, observed)
    if args.json:
        print(json.dumps({"path": str(path), "runs_on": runs_on} | result, indent=2, default=str))
    else:
        for f in result["findings"]:
            title = f["title"] + (f" (line {f['line']})" if f.get("line") else "")
            if f.get("cell") and not title.startswith(f"Cell {f['cell']}"):
                title = f"Cell {f['cell']}: {title}"
            text = " ".join(t for t in (title + ".", f["text"], f["fix"]) if t)
            print(f"{path}:1:1: {f['severity']}: {' '.join(text.split())}")
        c = result["counts"]
        print(f"{c.get('error', 0)} errors, {c.get('warning', 0)} warnings, {c.get('info', 0)} notes"
              + ("" if runs_on else " (Runs on isn't set, so what stops either place is a warning)"))
    return 1 if result["counts"].get("error") else 0


def unexcepted_errors(result: dict) -> list[dict]:
    """The Error-severity findings no active exception covers: what fails a CI job."""
    return [f for p in result["policies"] if p["status"] != "excepted" for f in p["findings"]
            if f.get("severity") == "Error" and not f.get("excepted")]


def _governance(args: argparse.Namespace) -> int:
    """governance check: 0 nothing fails, 1 an Error finding isn't excepted, 2 refused (no project, or no usable
    package). It reads only the package and the project's files; it calls no platform, Fabric or OneLake.
    governance package: takes the package from the platform (the one subcommand here that reads it)."""
    from pathlib import Path

    from .central.policy_package import PackageError
    from .meta import Project

    service = Service(headless=True)
    if args.project:
        folder = Path(args.project).resolve()
        if not (folder / "layers.toml").exists():
            print(f"{folder} isn't a metadata project folder (it has no layers.toml).")
            return 2
        project = Project(str(folder))
        service._project = lambda: project  # type: ignore[method-assign]  # this command's project, not the app's
    try:
        if args.governance_command == "package":
            out = service.governance_package_export(out=args.out or "")
            print(f"Took the policy package for {out['policies']} policies from {out['platform']}."
                  f"\nKept on this PC: {out['kept']}" + (f"\nWritten to: {out['file']}" if args.out else "")
                  + "\nPin it in CI with --expect-sha256 or KRATOSFORGE_POLICY_PACKAGE_SHA256:"
                  f"\nSHA-256: {out['sha256']}")
            return 0
        if args.max_age is not None and not 0 <= args.max_age < float("inf"):  # refuses nan and inf too
            print("--max-age: a number of days, at least 0")
            return 2
        expect = args.expect_sha256 or os.environ.get("KRATOSFORGE_POLICY_PACKAGE_SHA256", "")
        result = service.governance_check_from_package(package=args.package or "", expect_sha256=expect,
                                                       max_age_days=args.max_age)
    except (PackageError, LookupError) as exc:
        print(exc)
        return 2
    errors = unexcepted_errors(result)
    if args.json:
        print(json.dumps(result | {"unexcepted_errors": len(errors)}, indent=2, default=str))
        return 1 if errors else 0
    print(result["package_note"])
    for p in result["policies"]:
        print(f"{p['status'].replace('_', ' ')}: {p['name']} ({p['id']} v{p['version']}, {p['enforcement']})"
              + (f" {p['passed']} of {p['checked']} pass" if p["status"] == "pass" else ""))
        for f in p["findings"] + p["unknown"]:
            kind = "unknown" if f.get("outcome") == "unknown" else f.get("severity", "")
            print(f"  {kind}: {f.get('object')}: {f.get('description')} ({f.get('rule_id')})"
                  + (f" [{f['excepted']}]" if f.get("excepted") else ""))
        if p.get("note"):
            print(f"  {p['note']}")
    for x in result["problems"]:
        print(f"note: {x}")
    c = result["counts"]
    print(f"{result['project']}: {c['to_look_at']} to look at, {c['unknown']} unknown, {c['excepted']} excepted; "
          f"{c['passing']} of {c['policies']} policies pass. {len(errors)} Error findings aren't excepted.")
    return 1 if errors else 0


def _pipeline(args: argparse.Namespace) -> int:
    from .pipeline_service import PipelineMixin

    svc = PipelineMixin()
    if args.pipeline_command == "export":
        result = svc.pipeline_export(path=args.path, format=args.format)
        if result.get("ok") and "text" in result:
            print(result["text"])
            return 0
    elif args.pipeline_command == "inspect":
        result = svc.pipeline_inspect(path=args.path)
    elif args.pipeline_command == "run":
        return _pipeline_run(args)
    elif args.pipeline_command == "schedule":
        return _pipeline_schedule(args)
    elif args.pipeline_command == "activities":
        print(json.dumps(svc.pipeline_activity_registry(args.type), indent=2))
        return 0
    elif args.pipeline_command == "compat":
        result = svc.pipeline_compatibility(path=args.path)
        if not args.json and "summary" in result:
            for r in result["activities"]:
                planned = f" (planned: {r['planned']['classification']}, {r['planned']['stage']})" if r["planned"] else ""
                print(f"{r['classification']:<11} {r['name']} [{r['type']}]{planned}")
                for prop in r["properties"]:
                    print(f"{'':<11}   {prop['property']}: {prop['status']}")
            s = result["summary"]
            print(f"overall: {s['overall']}; runs on this PC: {'yes' if s['runs_on_this_pc'] else 'no'}; "
                  f"needs Fabric capacity: {len(s['needs_fabric_capacity'])} of {s['activities']} activities")
            if s["needs_fabric_capacity"]:
                print("these can't run on this PC yet, and Fabric meters every activity run"
                      + (f"; Fabric only: {', '.join(s['fabric_only'])}" if s["fabric_only"] else ""))
            c = result["counts"]
            print(f"{c['error']} errors, {c['warning']} warnings, {c.get('info', 0)} notes")
            return 0 if result["ok"] else 1
    else:
        result = svc.pipeline_validate(path=args.path)
        for d in result["diagnostics"]:
            where = f" [{d['activity']}]" if d.get("activity") else ""
            print(f"{d['severity']}: {d['code']}{where} at {d['path']}: {d['message']}")
        c = result["counts"]
        print(f"{c['error']} errors, {c['warning']} warnings, {c.get('info', 0)} notes")
        return 0 if result["ok"] else 1
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


def _pipeline_schedule(args: argparse.Namespace) -> int:
    pairs = lambda items: dict(item.partition("=")[::2] for item in items)  # noqa: E731
    try:
        mocks = json.loads(open(args.mocks, encoding="utf-8").read()) if args.mocks else {}
        target = {"kind": "pipeline", "path": os.path.abspath(args.path), "parameters": pairs(args.param),
                  "mocks": mocks, "notebooks": pairs(args.notebook), "sql_projects": pairs(args.sql_project),
                  "read_lakehouses": args.read_lakehouse, "workspace_id": args.workspace or ""}
        schedule = ({"kind": "cron", "every_minutes": args.every_minutes} if args.every_minutes else
                    {"kind": "weekly" if args.weekday else "daily", "times": args.at, "weekdays": args.weekday})
        added = Service().add_pc_schedule(target, schedule)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 2
    item = added["schedule"]
    print(f"Scheduled {item['target']['name']} on this PC ({item['id']}).")
    if not added["windows"]:
        print("Windows Task Scheduler isn't available here, so it won't fire until the app runs on Windows.")
    return 0


def _pipeline_run(args: argparse.Namespace) -> int:
    from .orchestration import load
    from .orchestration.definition import DefinitionError
    from .orchestration.runner import PipelineRun, RunRefused

    try:
        imp = load(args.path)
        children = {}
        for item in args.child:
            key, _, path = item.partition("=")
            children[key] = load(path)
        mocks = json.loads(open(args.mocks, encoding="utf-8").read()) if args.mocks else None
    except (DefinitionError, OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 2
    params = dict(item.partition("=")[::2] for item in args.param)
    run_id, notebooks, data = str(uuid.uuid4()), None, None
    if args.notebook or args.read_lakehouse or args.sql_project:
        svc = Service()
    if args.notebook:
        from .pipeline_notebooks import LocalNotebooks
        notebooks = LocalNotebooks(svc, run_id, dict(item.partition("=")[::2] for item in args.notebook),
                                   args.allow_uncontained, args.keep_outputs)
    if args.read_lakehouse or args.sql_project:
        from .pipeline_data import LocalData
        data = LocalData(svc, run_id, notebooks, args.workspace, lakehouse_reads=args.read_lakehouse,
                         sql_projects=dict(item.partition("=")[::2] for item in args.sql_project))
    run = PipelineRun(imp, parameters=params, mocks=mocks, children=children, max_parallel=args.max_parallel,
                      wait_scale=args.wait_scale, timeout_seconds=args.timeout, run_id=run_id, notebooks=notebooks,
                      data=data)
    try:
        problems = run.check()
        acts = imp.pipeline.all_activities()
        if not problems and ((notebooks is not None and notebooks.lakehouses(acts))
                             or (data is not None and data.lakehouses(acts))):
            svc.auth.storage_token()  # sign in before anything runs
        run.run()
    except RunRefused as exc:
        for p in exc.problems:
            print(f"refused: {p['code']}" + (f" [{p['activity']}]" if p.get("activity") else "") + f": {p['message']}")
        return 2
    except KeyboardInterrupt:
        run.cancel()
        if notebooks is not None:
            notebooks.stop()
        print("cancelled")
        return 1
    finally:
        if notebooks is not None:
            notebooks.finish()
    record = run.to_json()
    if args.json:
        print(json.dumps(record, indent=2, default=str))
    else:
        for a in record["activities"]:
            where = f" ({a['label']})" if a["label"] else ""
            how = " [mock]" if a["executed_by"] == "mock" else ""
            err = f": {a['error']['message']}" if isinstance(a.get("error"), dict) else ""
            print(f"{a['state']:<10} {a['name']}{where}{how}{err}")
        print(f"run {record['run_id']}: {record['state']} in {record['seconds']} s")
    return 0 if run.state == "succeeded" else 1


def _audit(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .audit import AuditLog
    from .service import data_dir
    log = AuditLog(Path(args.folder) if args.folder else data_dir() / "audit")
    if args.verify:
        result = log.verify()
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    for entry in log.recent(args.limit, args.action):
        print(json.dumps(entry, default=str))
    return 0


def _run_schedule(schedule_id: str, trigger: str) -> None:
    """No console and no one watching: log to a file, never open a sign-in window."""

    from .server import _log_file
    if sys.stdout is None or sys.stderr is None or sys.executable.lower().endswith("pythonw.exe"):
        sys.stdout = sys.stderr = _log_file()
    import time
    service = Service(headless=True)
    started = time.monotonic()
    result = service.run_schedule(schedule_id, trigger)
    try:
        service.audit.record("run_schedule", "error" if result.get("status") == "failed" else "ok",
                             time.monotonic() - started, {"schedule_id": schedule_id, "trigger": trigger},
                             who=(service._auth.signed_in_user() or "") if service._auth else "",
                             error=str(result.get("error") or result.get("message") or "") if result.get("status") == "failed" else "")
    except Exception as exc:  # noqa: BLE001 - the run's own result matters more than its audit line
        print(f"audit: {type(exc).__name__}: {exc}")
    print(json.dumps(result, default=str))
    raise SystemExit(0 if result["status"] != "failed" else 1)


if __name__ == "__main__":
    main()
