"""Live tests against a real Azure Databricks workspace (KF-DBX-LIVE).

The Databricks SQL warehouse and Databricks Connect engines are tested against fakes everywhere
else. This harness calls a real workspace through the same engine code, so a person can prove on
their own workspace that sign-in, Unity Catalog, a SQL warehouse and a cluster work the way the
app relies on:

    fabric-desktop live-test-databricks --profile <cli profile> --warehouse <id> --dry-run
    fabric-desktop live-test-databricks --profile <cli profile> --warehouse <id> --cluster <id> --yes

Safety, in order:

1. The warehouse (and the cluster, when one is named) must say it is for testing: a custom tag
   ``fabric-desktop-live-test``, which someone with CAN MANAGE adds on purpose.
2. Even then, a warehouse or cluster whose name has "prod", "prd" or "production" as a word is
   refused, and so is one with auto-stop turned off: the run may start it, and it must stop by itself.
3. The run creates nothing in the workspace. It runs read-only statements (``SELECT`` over
   ``range``) and a cell that only reads ``spark.range``, then cancels a long one of each.
4. Starting the warehouse or cluster bills Databricks units (DBU). The person's "Run it?" (or
   ``--yes``) is the confirmation the engines require (ADR-003). With ``--stop-compute``, the run
   stops the warehouse and terminates the cluster afterwards, but only those that were stopped when
   it began, so it never stops compute someone else was using.

Results go to ``<out>/<run id>/result.json`` and ``summary.md``. Neither holds a token.
"""

from __future__ import annotations

import functools
import json
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .engines import authorization
from .engines.contract import CellRequest, SessionConfig
from .engines import databricks_auth
from .engines.databricks_auth import TokenProvider
from .engines.databricks_sql import Client, DatabricksError
from .livetest import MARKER, Check, scrub

SCHEMA = "databricks-live-test-result@1"
# Long enough that a 2X-Small warehouse or a small cluster is still busy when the cancel arrives.
SLOW_SQL = "SELECT sum(id * 7 % 13) AS s FROM range(100000000000)"
SLOW_CELL = "spark.range(100000000000).selectExpr('sum(id * 7 % 13) AS s').collect()"
CANCEL_WAIT = 60.0           # seconds to wait for the slow statement to be running before cancelling
_PROD_WORD = re.compile(r"(?<![a-z0-9])(prod|prd|production)(?![a-z0-9])", re.IGNORECASE)
_RUNNING = ("RUNNING", "STARTING", "PENDING", "RESIZING", "RESTARTING")


MIN_PYTHON = (3, 12)          # sidecar/pyproject.toml requires-python
MIN_CLI = (0, 205)           # the first Databricks CLI with ``auth token`` as databricks_auth reads it
_CLI_VERSION = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?")


def pc_checks(connection: dict[str, Any], *, environ: Any = None, cfg_path: Path | None = None,
              run: Callable[..., Any] = subprocess.run, version: tuple[int, ...] = tuple(sys.version_info[:3])
              ) -> list[tuple[bool, str]]:
    """What this PC needs before the live test can sign in, as (ok, what) lines: Python, the
    Databricks CLI and its version, and the profile or workspace address. It signs in to nothing and
    reads no token: the CLI is asked only for its version, and the profile only for its host."""
    found: list[tuple[bool, str]] = []
    need = ".".join(map(str, MIN_PYTHON))
    have = ".".join(map(str, version))
    found.append((version[:2] >= MIN_PYTHON, f"Python {have} (KratosForge needs {need} or later)."))
    cli = databricks_auth.find_cli(environ)
    if not cli:
        found.append((False, "The Databricks CLI isn't on PATH. Install it from "
                             "https://docs.databricks.com/dev-tools/cli/install.html, then open a new terminal."))
    else:
        found.append(_cli_version(cli, run))
    credential = connection.get("credential") or {}
    if credential.get("method") == "cli-profile":
        profile = str(credential.get("profile") or "")
        try:
            section = databricks_auth._cfg_section(profile, cfg_path)
            host = databricks_auth.check_host(section.get("host", ""))
        except databricks_auth.DatabricksAuthError as exc:
            found.append((False, f"{exc} Sign in with: databricks auth login --host <workspace address> "
                                 f"--profile {profile}"))
        else:
            found.append((True, f"Profile {profile!r} points at {host}."))
    else:
        try:
            found.append((True, f"Workspace address {databricks_auth.check_host(str(connection.get('host') or ''))}."))
        except databricks_auth.DatabricksAuthError as exc:
            found.append((False, str(exc)))
    return found


def _cli_version(cli: str, run: Callable[..., Any]) -> tuple[bool, str]:
    need = ".".join(map(str, MIN_CLI))
    try:
        done = run([cli, "--version"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"The Databricks CLI at {cli} didn't answer --version ({type(exc).__name__})."
    match = _CLI_VERSION.search(f"{done.stdout or ''} {done.stderr or ''}")
    if done.returncode != 0 or not match:
        return False, (f"The Databricks CLI at {cli} didn't say its version. This may be the old Python "
                       f"databricks-cli; install the current CLI ({need} or later).")
    got = tuple(int(g or 0) for g in match.groups())
    text = ".".join(map(str, got))
    if got[:2] < MIN_CLI:
        return False, f"The Databricks CLI at {cli} is {text}; the live test needs {need} or later. Update it."
    return True, f"Databricks CLI {text} at {cli}."


def warehouse_tags(info: dict[str, Any]) -> set[str]:
    """Custom tag keys of a SQL warehouse (``tags.custom_tags`` is a list of key/value pairs)."""
    return {str(t.get("key") or "") for t in ((info.get("tags") or {}).get("custom_tags") or [])
            if isinstance(t, dict)}


def refusals(kind: str, info: dict[str, Any], tags: set[str], auto_stop: Any) -> list[str]:
    """Every reason this warehouse or cluster isn't safe to test on; empty when it is."""
    name = str(info.get("name") or info.get("cluster_name") or "")
    reasons = []
    if MARKER not in tags:
        reasons.append(f"The {kind} {name!r} has no {MARKER} tag. Someone with CAN MANAGE adds it to say this "
                       f"{kind} is for tests.")
    if _PROD_WORD.search(name):
        reasons.append(f"The {kind} name {name!r} looks like production.")
    if not auto_stop:
        reasons.append(f"The {kind} {name!r} has auto-stop turned off, so it would keep billing after the test.")
    return reasons


class DatabricksLiveTest:
    """One run of the live checks against one workspace's warehouse and, optionally, a cluster.

    ``tokens`` is the connection's token provider; ``client`` makes the plain REST calls (warehouse, cluster, stop); ``sql_adapter`` and
    ``connect_adapter`` are the engines' own adapters, so the run uses the code notebooks use.
    Tests pass fakes of all three."""

    def __init__(self, connection: dict[str, Any], warehouse_id: str, tokens: Any, client: Any, sql_adapter: Any,
                 out_dir: Path, cluster_id: str = "", connect_adapter: Any = None, stop_compute: bool = False,
                 bad_token_client: Any = None, run_id: str = "", by: str = "live-test",
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.connection = connection
        self.host = str(connection.get("host") or "")
        self.warehouse_id, self.cluster_id = warehouse_id, cluster_id
        self.tokens, self.client = tokens, client
        self.sql_adapter, self.connect_adapter = sql_adapter, connect_adapter
        self.stop_compute = stop_compute
        self.bad_token_client = bad_token_client if bad_token_client is not None else \
            Client(TokenProvider(self.host, lambda: ("not-a-token", None), "live-test"))
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.dir = Path(out_dir) / self.run_id
        self.by = by
        self.clock, self.sleep = clock, sleep
        self.checks: list[Check] = []
        self.warehouse: dict[str, Any] = {}
        self.cluster: dict[str, Any] = {}
        self.started_by_run: list[dict[str, str]] = []   # what this run found stopped, and so started
        self.stopped: list[dict[str, str]] = []

    # ---- what it does

    def plan(self) -> list[str]:
        where = self.host or "the workspace in the CLI profile"
        steps = [
            f"Get a token for {where} from the Databricks CLI profile or sign-in named.",
            f"Read SQL warehouse {self.warehouse_id}: its name, size, state and auto-stop. Refuse it unless it has "
            f"the {MARKER} tag, auto-stop is on and its name doesn't look like production. Reading never starts it.",
            "List Unity Catalog catalogs (doesn't start the warehouse).",
            "Run SELECT statements over range() on the warehouse. A stopped warehouse starts and bills DBUs until "
            "its auto-stop.",
            "Run a statement on a table that doesn't exist, and check the error comes back as an error.",
            f"Start a long statement ({SLOW_SQL}) and cancel it on the warehouse.",
            "Close the SQL session.",
        ]
        if self.cluster_id:
            steps += [
                f"Read cluster {self.cluster_id}: its runtime, state and auto-termination. Refuse it on the same "
                "terms as the warehouse.",
                "Open a Databricks Connect session to it (databricks-connect for its runtime must already be "
                "installed from Engines). A terminated cluster starts and bills DBUs until it auto-terminates.",
                "Run a cell that counts spark.range(5) on the cluster, then start a long one and cancel it.",
                "Close the session and check its process on this PC has ended.",
            ]
        steps += ["Ask for a warehouse that doesn't exist, and call the workspace with a bad token, to check errors "
                  "are reported."]
        if self.stop_compute:
            steps.append("Stop the warehouse and terminate the cluster, but only those that were stopped when the "
                         "run began.")
        else:
            steps.append("Leave the warehouse and cluster to stop on their own auto-stop.")
        steps += ["Create nothing in the workspace: no tables, schemas, files, jobs or clusters.",
                  f"Write the results to {self.dir}."]
        return steps

    def _check(self, id: str, title: str, fn: Callable[[], str | None]) -> Check:
        check = Check(id, title)
        start = self.clock()
        try:
            check.detail = fn() or ""
            check.status = "passed"
        except Exception as exc:  # every failure is a result, not a crash
            check.status = "failed"
            check.detail = scrub(f"{type(exc).__name__}: {exc}")
        check.seconds = round(self.clock() - start, 3)
        self.checks.append(check)
        return check

    def _skip(self, id: str, title: str, why: str) -> None:
        self.checks.append(Check(id, title, "skipped", 0.0, why))

    # ---- the run

    def run(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        signed_in = self._check("auth.token", "Get a token for the workspace", self._token)
        if signed_in.status != "passed":
            return self._finish(started, [])
        found = self._check("dbx.warehouse", "Read the SQL warehouse", self._read_warehouse)
        if found.status != "passed":
            return self._finish(started, [])
        refused = refusals("warehouse", self.warehouse, warehouse_tags(self.warehouse),
                           self.warehouse.get("auto_stop_mins"))
        if self.cluster_id:
            got = self._check("dbx.cluster", "Read the cluster", self._read_cluster)
            if got.status != "passed":
                return self._finish(started, [])
            refused += refusals("cluster", self.cluster, set((self.cluster.get("custom_tags") or {})),
                                self.cluster.get("autotermination_minutes"))
        if refused:
            return self._finish(started, refused)
        try:
            self._sql_checks()
            if self.cluster_id:
                self._connect_checks()
            self._check("errors.not_found", "A missing warehouse is reported as an error", self._not_found)
            self._check("errors.bad_token", "The workspace refuses a bad token", self._bad_token)
        finally:
            if self.stop_compute:
                self._stop()
        return self._finish(started, [])

    def _token(self) -> str:
        if not self.tokens.token():
            raise RuntimeError("Sign-in returned an empty token")
        return f"Token received (not recorded), by {self.tokens.method}."

    def _read_warehouse(self) -> str:
        self.warehouse = self.client.warehouse(self.warehouse_id)
        if str(self.warehouse.get("state") or "") not in _RUNNING:
            self.started_by_run.append({"kind": "warehouse", "id": self.warehouse_id})
        w = self.warehouse
        return (f"{w.get('name', '')}: {w.get('cluster_size', '?')}, "
                f"{'serverless' if w.get('enable_serverless_compute') else w.get('warehouse_type', 'classic')}, "
                f"{w.get('state', '?')}, auto-stop {w.get('auto_stop_mins', '?')} min.")

    def _read_cluster(self) -> str:
        self.cluster = self.client.cluster(self.cluster_id)
        if str(self.cluster.get("state") or "") not in _RUNNING:
            self.started_by_run.append({"kind": "cluster", "id": self.cluster_id})
        c = self.cluster
        return (f"{c.get('cluster_name', '')}: {c.get('spark_version', '?')}, {c.get('node_type_id', '?')}, "
                f"{c.get('num_workers', 0)} workers, {c.get('state', '?')}, "
                f"auto-termination {c.get('autotermination_minutes', '?')} min.")

    def _authorization(self, engine: str, target: str) -> Any:
        # The person said "Run it?" yes (or passed --yes) for this warehouse and cluster just now.
        return authorization.confirmed(engine, target, self.by)

    def _sql_checks(self) -> None:
        config = SessionConfig(f"live-test-{self.run_id}", options={"connection": self.connection,
                                                                    "warehouse_id": self.warehouse_id})
        session = None

        def open_session() -> str:
            nonlocal session
            session = self.sql_adapter.open_session(config, self._authorization("databricks-sql", self.warehouse_id))
            return f"Session on {session.warehouse_name} (the warehouse was {session.warehouse_state or 'unknown'})."

        opened = self._check("sql.open", "Open a Databricks SQL session", open_session)
        if opened.status != "passed" or session is None:
            for id, title in _AFTER_SQL_OPEN:
                self._skip(id, title, "Needs the SQL session, which couldn't open.")
            return
        try:
            self._check("uc.catalogs", "List Unity Catalog catalogs", lambda: self._catalogs(session))
            self._check("sql.query", "Run a query on the warehouse", lambda: self._query(session))
            self._check("sql.error", "A failing statement is reported as an error", lambda: self._sql_error(session))
            self._check("sql.cancel", "Cancel a running statement", lambda: self._cancel(
                session, CellRequest(SLOW_SQL, "sql"), "cancelled"))
        finally:
            self._check("sql.close", "Close the SQL session", lambda: self._close(session))

    def _catalogs(self, session: Any) -> str:
        names = sorted({c["catalog"] for c in session.catalog() if c.get("catalog")})
        if not names:
            raise RuntimeError("Unity Catalog listed no catalogs")
        return f"{len(names)} catalogs, including {', '.join(names[:5])}."

    def _query(self, session: Any) -> str:
        result = session.execute(CellRequest("SELECT id, id * 2 AS twice FROM range(5) ORDER BY id", "sql"))
        if result.status != "ok":
            raise RuntimeError(f"The query ended {result.status}: {_last_error(result)}")
        if result.rows != 5:
            raise RuntimeError(f"Expected 5 rows, the warehouse reported {result.rows}")
        return f"5 rows in {result.seconds:.1f} s (the first query includes starting a stopped warehouse)."

    def _sql_error(self, session: Any) -> str:
        missing = f"kf_live_test_missing_{self.run_id}"
        result = session.execute(CellRequest(f"SELECT * FROM {missing}", "sql"))
        if result.status != "error":
            raise RuntimeError(f"A statement on a table that doesn't exist ended {result.status}")
        text = _last_error(result)
        if missing.lower() not in text.lower() and "TABLE_OR_VIEW_NOT_FOUND" not in text:
            raise RuntimeError(f"The error doesn't name the missing table: {text}")
        return "Reported as an error naming the table."

    def _cancel(self, session: Any, request: CellRequest, cancelled_status: str) -> str:
        box: dict[str, Any] = {}
        worker = threading.Thread(target=lambda: box.update(result=session.execute(request)), daemon=True)
        worker.start()
        deadline = self.clock() + CANCEL_WAIT
        sent = False
        while worker.is_alive() and self.clock() < deadline:
            if session.cancel():
                sent = True
                break
            self.sleep(0.5)
        worker.join(timeout=300)
        if worker.is_alive():
            raise RuntimeError("The cell was still running 5 minutes after the cancel")
        result = box.get("result")
        if result is None:
            raise RuntimeError("The cell ended without a result")
        if not sent:
            raise RuntimeError(f"The cell ended {result.status} before it could be cancelled")
        text = _last_error(result)
        if result.status == cancelled_status or "CANCEL" in text.upper():
            return f"Cancelled after {result.seconds:.1f} s; the cell reported {result.status}."
        raise RuntimeError(f"After the cancel the cell ended {result.status}: {text or 'no error'}")

    def _close(self, session: Any) -> str:
        session.close()
        if not session.closed:
            raise RuntimeError("The session still says it is open")
        proc = getattr(session, "proc", None)
        if proc is not None and proc.poll() is None:
            raise RuntimeError("The session's process on this PC is still running")
        return "Closed" + (", and its process on this PC has ended." if proc is not None else ".")

    def _connect_checks(self) -> None:
        config = SessionConfig(f"live-test-{self.run_id}", options={"connection": self.connection,
                                                                    "cluster_id": self.cluster_id})
        session = None

        def open_session() -> str:
            nonlocal session
            session = self.connect_adapter.open_session(
                config, self._authorization("databricks-connect", self.cluster_id))
            return f"Connected with databricks-connect {getattr(session, 'client', '') or '(version unknown)'}."

        opened = self._check("connect.open", "Open a Databricks Connect session", open_session)
        if opened.status != "passed" or session is None:
            for id, title in _AFTER_CONNECT_OPEN:
                self._skip(id, title, "Needs the Databricks Connect session, which couldn't open.")
            return
        try:
            self._check("connect.cell", "Run a cell on the cluster", lambda: self._cell(session))
            self._check("connect.cancel", "Cancel a running Spark job", lambda: self._cancel(
                session, CellRequest(SLOW_CELL, "pyspark"), "cancelled"))
        finally:
            self._check("connect.close", "Close the session", lambda: self._close(session))

    def _cell(self, session: Any) -> str:
        result = session.execute(CellRequest("spark.range(5).count()", "pyspark"))
        if result.status != "ok":
            raise RuntimeError(f"The cell ended {result.status}: {_last_error(result)}")
        text = " ".join(str(o.get("text") or "") for o in result.outputs if o.get("type") == "text").strip()
        if text != "5":
            raise RuntimeError(f"Expected 5, the cell showed {text or 'nothing'}")
        return f"spark.range(5).count() is 5, in {result.seconds:.1f} s."

    def _not_found(self) -> str:
        missing = uuid.uuid4().hex[:16]
        try:
            self.client.warehouse(missing)
        except DatabricksError as exc:
            status = re.search(r"HTTP (\d{3})", str(exc))
            if status and status.group(1) in ("400", "404"):
                return f"Reported as HTTP {status.group(1)}."
            raise RuntimeError(f"Expected HTTP 400 or 404: {exc}") from None
        raise RuntimeError("Asking for a warehouse that doesn't exist didn't fail")

    def _bad_token(self) -> str:
        try:
            self.bad_token_client.warehouse(self.warehouse_id)
        except DatabricksError as exc:
            status = re.search(r"HTTP (\d{3})", str(exc))
            if status and status.group(1) in ("401", "403"):
                return f"Refused with {status.group(1)}."
            raise RuntimeError(f"Expected HTTP 401 or 403: {exc}") from None
        raise RuntimeError("The workspace accepted a bad token")

    # ---- stopping what the run started

    def _stop(self) -> None:
        for target in self.started_by_run:
            kind, id = target["kind"], target["id"]
            title = "Stop the warehouse" if kind == "warehouse" else "Terminate the cluster"
            check = self._check(f"stop.{kind}", title, functools.partial(self._stop_one, kind, id))
            if check.status == "passed":
                self.stopped.append(target)

    def _stop_one(self, kind: str, id: str) -> str:
        if kind == "warehouse":
            self.client.stop_warehouse(id)
            return "Stop requested; it was stopped when the run began."
        self.client.terminate_cluster(id)
        return "Termination requested; it was terminated when the run began. The cluster is kept, not deleted."

    # ---- results

    def _finish(self, started: str, refused: list[str]) -> dict[str, Any]:
        counts = {s: sum(c.status == s for c in self.checks) for s in ("passed", "failed", "skipped")}
        outcome = "refused" if refused else ("failed" if counts["failed"] else "passed")
        result = {
            "schema": SCHEMA, "run_id": self.run_id, "outcome": outcome, "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "workspace": {"host": self.host},
            "warehouse": {"id": self.warehouse_id, "name": self.warehouse.get("name", ""),
                          "size": self.warehouse.get("cluster_size", ""),
                          "auto_stop_mins": self.warehouse.get("auto_stop_mins")},
            "cluster": ({"id": self.cluster_id, "name": self.cluster.get("cluster_name", ""),
                         "spark_version": self.cluster.get("spark_version", ""),
                         "autotermination_minutes": self.cluster.get("autotermination_minutes")}
                        if self.cluster_id else None),
            "refused": refused, "counts": counts, "checks": [asdict(c) for c in self.checks],
            "stop_compute": self.stop_compute,
            "left_running": [t for t in self.started_by_run if t not in self.stopped] if not refused else [],
        }
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (self.dir / "summary.md").write_text(summary(result), encoding="utf-8")
        result["files"] = [str(self.dir / "result.json"), str(self.dir / "summary.md")]
        return result


_AFTER_SQL_OPEN = [("uc.catalogs", "List Unity Catalog catalogs"), ("sql.query", "Run a query on the warehouse"),
                   ("sql.error", "A failing statement is reported as an error"),
                   ("sql.cancel", "Cancel a running statement")]
_AFTER_CONNECT_OPEN = [("connect.cell", "Run a cell on the cluster"), ("connect.cancel", "Cancel a running Spark job")]


def _last_error(result: Any) -> str:
    errors = [o for o in result.outputs if o.get("type") == "error"]
    return scrub(str(errors[-1].get("evalue") or "")) if errors else ""


def summary(result: dict[str, Any]) -> str:
    mark = {"passed": "Passed", "failed": "**Failed**", "skipped": "Skipped"}
    wh = result["warehouse"]
    lines = [f"# KratosForge Databricks live test {result['run_id']}", "",
             f"Outcome: **{result['outcome']}**. Workspace {result['workspace']['host'] or 'from the CLI profile'}, "
             f"warehouse {wh['name'] or wh['id']}"
             + (f", cluster {result['cluster']['name'] or result['cluster']['id']}" if result["cluster"] else "")
             + f", started {result['started_at']}.", ""]
    if result["refused"]:
        lines += ["Nothing was run, because:", ""] + [f"- {r}" for r in result["refused"]] + [""]
    lines += ["| Check | Result | Seconds | Detail |", "| --- | --- | --- | --- |"]
    for c in result["checks"]:
        detail = c["detail"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {c['title']} | {mark[c['status']]} | {c['seconds']} | {detail} |")
    if result["left_running"]:
        lines += ["", "**Started by this run and left to its auto-stop**" +
                  (" (stopping it failed)" if result["stop_compute"] else " (add --stop-compute to stop it)") + ":", ""]
        lines += [f"- {t['kind']} {t['id']}" for t in result["left_running"]]
    return "\n".join(lines) + "\n"
