"""sql_exec.py: the mutants that test_sql_exec_mutation.py and the module's other tests leave alive. Earlier
sessions are cleaned up once, at the first run rather than at every run, and a local run with no time limit
anywhere has none, and a failure leaves no folder behind."""

import io
import json
import threading

from fabric_desktop import sql_exec as se


LIMITS = {"sql_page_rows": 1000, "sql_time_limit_minutes": 0, "sql_memory_limit_percent": 60, "sql_max_concurrent": 2,
          "sql_result_retention_hours": 24, "sql_temp_dir": "", "sql_time_limit_by_policy": False}
LH = {"workspace_id": "ws", "lakehouse_id": "lh", "name": "Sales"}


def _manager(tmp_path, **kw):
    kw.setdefault("limits", lambda: dict(LIMITS))
    return se.SqlExecutions(tmp_path / "data", token=lambda: "", **kw)


class _Stdin(io.StringIO):
    """The worker's stdin: what was written to it is a job once the engine closes it."""

    def close(self):
        if not self.closed and self.getvalue():
            _Worker.jobs.append(json.loads(self.getvalue()))
        super().close()


class _Worker:
    """Stands in for the worker process: keeps the job written to it, says nothing and exits."""
    jobs: list = []

    def __init__(self, *args, **kwargs):
        self.stdin, self.stdout = _Stdin(), iter([])

    def wait(self, timeout=None):
        return 1

    def poll(self):
        return 1


def _join(run_id):
    for t in threading.enumerate():
        if t.name == f"sql-run-{run_id}":
            t.join(10)


def test_earlier_sessions_are_cleaned_up_once_at_the_first_run_not_at_every_run(tmp_path):
    limits = dict(LIMITS, sql_result_retention_hours=0)
    m = _manager(tmp_path, limits=lambda: limits, command=[str(tmp_path / "no-such-python")])
    m._memory_budget = lambda percent: {"duckdb_mb": 512, "cap_mb": 1024, "bound": "percent", "free_mb": 4096}
    left = m.base() / "aaaaaaaaaaaa"
    left.mkdir(parents=True)
    m.start("SELECT 1", lakehouse_ref=LH)
    assert not left.exists()  # an earlier session's folder goes at the first run
    later = m.base() / "bbbbbbbbbbbb"
    later.mkdir()
    m.start("SELECT 2", lakehouse_ref=LH)
    assert later.exists()  # not looked for again


class _Project:
    id = name = "crm"
    settings = type("Settings", (), {"timeout_seconds": 0})()


class _Engine:
    project = _Project()

    def __init__(self, fail):
        self.fail = fail

    def execute_reader(self, query, **kw):
        self.fail()


def test_a_local_run_with_no_time_limit_anywhere_has_none_and_a_failure_leaves_no_folder(tmp_path):
    import shutil
    m = _manager(tmp_path)
    box = {}

    def fail():
        shutil.rmtree(box["run"].folder)  # the folder is already gone when the run tidies up
        raise RuntimeError("Binder Error: no table t")
    m._local_engine = lambda project: _Engine(fail)
    run = se.Execution(id="loc1", query="SELECT * FROM t", dialect="duckdb", lakehouse={}, allow=se.TARGET_ALLOWS["local"],
                       folder=tmp_path / "loc1", target="local", project="crm")
    box["run"] = run
    m._runs[run.id] = run
    m._launch(run, dict(LIMITS))
    _join(run.id)
    s = run.status()
    assert (s["state"], s["error_kind"], s["error"]) == ("failed", "sql", "Binder Error: no table t")
    assert (s["time_limit_minutes"], s["time_limit_from"]) == (0.0, "")
    assert not run.folder.exists()
    assert run.query == ""  # the run finished: its SQL isn't kept


















