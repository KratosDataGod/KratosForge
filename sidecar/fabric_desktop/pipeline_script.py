"""Script activities in local pipeline runs (pipelines plan P2), through the SQL page's runner.

Each script block runs as one managed SQL run (sql_exec.py), so it gets the SQL page's process,
Stop, time and memory limits, and its policy (sql_policy.py) decides what may run:

* On a **lakehouse** (a ``Lakehouse`` connection, read when the run is started with lakehouse
  reads on), the T-SQL is translated to DuckDB SQL the way local data projects translate it
  (localdata/tsql.py; what can't be carried is refused) and runs read-only against the
  lakehouse's tables. A statement that would change a table, Fabric or files is refused before
  the run. Notes on what may differ from the SQL analytics endpoint go in ``outputLogs``.
* In a **local data project**, named for the activity or its connection with ``sql_projects``
  (``{activity or connection name: project}``), it runs in the project's own DuckDB database and
  may change the project's own tables. DROP, TRUNCATE and DELETE or UPDATE without WHERE need the
  SQL page's confirmation, which a pipeline can't give, so they are refused.

Query blocks return their rows like Fabric: ``resultSets`` with ``rowCount`` and ``rows``, at most
5,000 rows each (``outputTruncated`` says when more were cut). Warehouse connections, script
parameters and other connections need a mock. Tables are read as they are now, not at the run's
pinned versions. No Fabric job is started; reading OneLake uses some capacity.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from . import sql, sql_policy
from .localdata import tsql
from .orchestration.adapters import ActivityFailed, timespan
from .orchestration.definition import Activity

SCRIPT_ROWS = 5000        # rows kept per result set, as Fabric's Script activity
LOCAL_DIALECT = "tsql"     # a local data project translates T-SQL itself (localdata/tsql.py)
_POLL = 0.1


def _problem(code: str, message: str, a: Activity) -> dict[str, Any]:
    return {"code": code, "message": message, "activity": a.name, "path": a.path}


def _expr(value: Any) -> bool:
    return (isinstance(value, str) and value.startswith("@") and not value.startswith("@@")) or (
        isinstance(value, dict) and value.get("type") == "Expression")


def _service(ls: Any) -> dict[str, Any]:
    ls = ls if isinstance(ls, dict) else {}
    props = ls.get("properties") if isinstance(ls.get("properties"), dict) else {}
    tp = props.get("typeProperties") if isinstance(props.get("typeProperties"), dict) else {}
    return {"name": ls.get("name"), "type": props.get("type"), **tp}


def lakehouse_sql(text: str) -> tuple[str, list[str]]:
    """T-SQL for a lakehouse as the DuckDB SQL the SQL page runs here, translated the way local
    data projects translate it, and notes on what may differ. Raises ValueError for what isn't carried."""
    texts, notes = tsql.translate(text)
    return ";\n".join(texts), notes


def _text(block: Any) -> Any:
    text = block.get("text") if isinstance(block, dict) else None
    return text.get("value") if isinstance(text, dict) and text.get("type") == "Expression" else text


class LocalScripts:
    """Script activities for one pipeline run; ``data`` is the run's LocalData (pipeline_data.py)."""

    def __init__(self, data: Any, projects: dict[str, str] | None = None):
        self.data = data
        self.projects = {k: v for k, v in (projects or {}).items() if isinstance(k, str) and isinstance(v, str)}

    def _project(self, a: Activity) -> str | None:
        return self.projects.get(a.name) or self.projects.get(str(_service(a.raw.get("linkedService")).get("name")))

    def on_lakehouse(self, a: Activity) -> bool:
        return self._project(a) is None and _service(a.raw.get("linkedService")).get("type") == "Lakehouse"

    # ---- before the run

    def check(self, a: Activity) -> list[dict[str, Any]]:
        blocks = a.type_properties.get("scripts")
        project = self._project(a)
        service = _service(a.raw.get("linkedService"))
        if project is None:
            kind = service.get("type")
            if kind == "DataWarehouse":
                return [_problem("script_warehouse", f"{a.name} runs on a warehouse, which runs only in Fabric. Give "
                                 "a mock for it.", a)]
            if kind != "Lakehouse":
                return [_problem("script_connection", f"{a.name} runs on {kind or 'a connection'}; Script on this PC "
                                 "runs on a lakehouse, or in a local data project you name for it (sql_projects). "
                                 "Give a mock for it.", a)]
            if not self.data.lakehouse_reads:
                return [_problem("data_needs_engine", f"{a.name} (Script) reads a lakehouse. It runs when you start "
                                 "the run with lakehouse reads on (fabric-desktop pipeline run --read-lakehouse); "
                                 "give a mock for it here.", a)]
            ws = service.get("workspaceId")
            if not _expr(ws) and (not ws or ws == "00000000-0000-0000-0000-000000000000") and not self.data.workspace_id:
                return [_problem("data_workspace_unknown", f"{a.name}'s lakehouse doesn't name its workspace. Say "
                                 "which workspace the pipeline is in (workspace_id), or give a mock.", a)]
        if not isinstance(blocks, list) or not blocks:
            return [_problem("script_empty", f"{a.name} has no scripts.", a)]
        for i, block in enumerate(blocks):
            if isinstance(block, dict) and block.get("parameters"):
                return [_problem("script_parameters", f"{a.name}: script {i + 1} has parameters, which Script on this "
                                 "PC doesn't pass yet. Give a mock for it.", a)]
            text = _text(block)
            if _expr(text):
                continue   # known only when the activity runs; the policy checks it then
            if not isinstance(text, str) or not text.strip():
                return [_problem("script_empty", f"{a.name}: script {i + 1} is empty.", a)]
            problem = self._policy(a, i, text, project)
            if problem:
                return [problem]
        return []

    def _policy(self, a: Activity, i: int, text: str, project: str | None) -> dict[str, Any] | None:
        """What the SQL page's policy says of one block, without running it."""
        try:
            if project:
                plan = self.data.svc.sql_runs.start(text, None, LOCAL_DIALECT, None, target="local", project=project,
                                                    dry_run=True)
            else:
                duck = lakehouse_sql(text)[0]
                # A lakehouse script reads the lakehouse's tables only: in Fabric it can't reach this PC's files,
                # settings or SQL text run inside the statement, so neither does it here.
                for n, st in enumerate(sql.parse(duck, "duckdb"), 1):
                    source = sql_policy._reads_files(st)
                    if source:
                        return _problem("script_refused", f"{a.name}: script {i + 1}, statement {n}, {source} reads "
                                        "a file or setting on this PC, which a lakehouse script can't. Give a mock "
                                        "for it.", a)
                plan = self.data.svc.sql_runs.start(duck, {"workspace_id": "-", "lakehouse_id": "-", "name": "-"},
                                                    "duckdb", ["read_only"], dry_run=True)
        except KeyError:
            return _problem("script_project_missing", f"{a.name}: there is no local data project {project!r}.", a)
        except ValueError as exc:
            return _problem("script_unreadable", f"{a.name}: script {i + 1} can't be read: {exc}", a)
        if plan.get("confirm_required"):
            return _problem("script_needs_confirm", f"{a.name}: script {i + 1} removes a table or every row, which the "
                            "SQL page runs only after you confirm it. A pipeline can't confirm; give a mock for it.", a)
        stop = plan.get("refused")
        if stop:
            return _problem("script_refused", f"{a.name}: script {i + 1}, statement {stop.get('statement')}, "
                            f"{stop.get('label') or 'is refused'} ({stop.get('reason') or 'not allowed here'}). Give a "
                            "mock for it.", a)
        return None

    # ---- running

    def run(self, a: Activity, props: dict[str, Any], cancelled: Callable[[], bool],
            deadline: float | None) -> dict[str, Any]:
        project = self._project(a)
        ref = None
        if project is None:
            _, _, ref = self.data._ref(a, {"linkedService": props.get("linkedService")})
        limit = timespan(props.get("scriptBlockExecutionTimeout"))
        out: dict[str, Any] = {"resultSetCount": 0, "resultSets": [], "outputParameters": {}, "outputLogs": "",
                               "outputLogsLocation": "", "outputTruncated": False}
        for i, block in enumerate(props.get("scripts") or []):
            text = block.get("text") if isinstance(block, dict) else None
            if not isinstance(text, str) or not text.strip():
                raise ActivityFailed(f"{a.name}: script {i + 1} is empty.", "LocalScriptFailed")
            problem = self._policy(a, i, text, project)
            if problem:
                raise ActivityFailed(problem["message"], "LocalScriptRefused")
            if project is None:
                text, notes = lakehouse_sql(text)
                out["outputLogs"] += "".join(f"Script {i + 1}: {n}\n" for n in notes)
            rows, cut = self._block(a, i, text, ref, project, cancelled, deadline, limit)
            if str(block.get("type", "Query")).lower() != "nonquery" and rows is not None:
                out["resultSets"].append({"rowCount": len(rows), "rows": rows})
                out["outputTruncated"] = out["outputTruncated"] or cut
        out["resultSetCount"] = len(out["resultSets"])
        return out

    def _block(self, a: Activity, i: int, text: str, ref: dict[str, str] | None, project: str | None,
               cancelled: Callable[[], bool], deadline: float | None,
               limit: float | None) -> tuple[list[dict[str, Any]] | None, bool]:
        runs = self.data.svc.sql_runs
        if project:
            started = runs.start(text, None, LOCAL_DIALECT, None, target="local", project=project, timeout=limit)
        else:
            started = runs.start(text, ref, "duckdb", ["read_only"])
        run_id, state = started["id"], started
        while state["state"] not in ("completed", "failed", "cancelled", "timed_out"):
            if cancelled() or (deadline is not None and time.monotonic() > deadline):
                runs.cancel(run_id)
                if cancelled():
                    raise ActivityFailed("Cancelled.", "LocalCancelled")
                raise ActivityFailed("The activity reached its policy timeout.", "LocalActivityTimeout")
            time.sleep(_POLL)
            state = runs.status(run_id)
        if state["state"] == "timed_out":
            raise ActivityFailed(f"{a.name}: script {i + 1} reached its time limit.", "LocalScriptTimeout")
        if state["state"] == "cancelled":
            raise ActivityFailed(f"{a.name}: script {i + 1} was stopped.", "LocalCancelled")
        if state["state"] == "failed":
            why = (state.get("refused") or {}).get("message") or state.get("error") or "failed"
            raise ActivityFailed(f"{a.name}: script {i + 1}: {why}",
                                 "LocalScriptRefused" if state.get("refused") else "LocalScriptFailed")
        if not state.get("columns"):
            return None, False
        names = [c["name"] for c in state["columns"]]
        rows: list[dict[str, Any]] = []
        while len(rows) < min(state["rows"], SCRIPT_ROWS):   # the SQL page's pages can be smaller
            page = runs.page(run_id, len(rows), SCRIPT_ROWS - len(rows))
            if not page["rows"]:
                break
            rows.extend(dict(zip(names, r)) for r in page["rows"][:SCRIPT_ROWS - len(rows)])
        return rows, state["rows"] > len(rows)
