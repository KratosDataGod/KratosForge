"""Mutation tests for the SQL page's runs (sql_exec.py): behaviour the module's other tests ran without
pinning, found by tools/mutate.py (docs/quality/mutation-testing.md). No worker process is started."""

import io
import json
import threading

import pytest

from fabric_desktop import sql_exec as se

LIMITS = {"sql_page_rows": 1000, "sql_time_limit_minutes": 0, "sql_memory_limit_percent": 60, "sql_max_concurrent": 2,
          "sql_result_retention_hours": 24, "sql_temp_dir": "", "sql_time_limit_by_policy": False}
LH = {"workspace_id": "ws", "lakehouse_id": "lh", "name": "Sales"}


def _manager(tmp_path, **kw):
    kw.setdefault("limits", lambda: dict(LIMITS))
    return se.SqlExecutions(tmp_path / "data", token=lambda: "", **kw)


def _no_worker(tmp_path):
    """A manager whose worker can't start: a run is queued and kept, then fails at once."""
    m = _manager(tmp_path, command=[str(tmp_path / "no-such-python")])
    m._memory_budget = lambda percent: {"duckdb_mb": 512, "cap_mb": 1024, "bound": "percent", "free_mb": 4096}
    return m


def test_each_statement_has_its_parameters_written_as_placeholders(tmp_path):
    m = _no_worker(tmp_path)
    s = m.start("", lakehouse_ref=LH, statements=["SELECT {{a}} AS x;", "SELECT {{b}} + 1 AS y"],
                params=[{"name": "a", "type": "number", "value": "1"}, {"name": "b", "type": "number", "value": "2"}])
    assert m._runs[s["id"]].units == ["SELECT :a AS x;", "SELECT :b + 1 AS y"]


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


def _job(tmp_path, monkeypatch, run):
    """The job a run's worker is given."""
    _Worker.jobs = []
    monkeypatch.setattr(se.subprocess, "Popen", _Worker)
    m = _no_worker(tmp_path)
    m._runs[run.id] = run
    m._launch(run, dict(LIMITS))
    for t in threading.enumerate():
        if t.name == f"sql-run-{run.id}":
            t.join(10)
    return _Worker.jobs[0]


def test_a_warehouse_script_sends_each_statement_its_values_and_its_precondition(tmp_path, monkeypatch):
    run = se.Execution(id="w1", query="UPDATE t SET a = :a", dialect="tsql", lakehouse={}, allow=("read_only", "warehouse_write"),
                       folder=tmp_path / "w1", target="warehouse", units=["UPDATE t SET a = :a", "SELECT 1"],
                       params=[{"name": "a", "type": "number", "value": "1"}])
    run.warehouse = {"workspace_id": "ws", "item_id": "wh", "kind": "warehouse", "server": "srv", "database": "Sales"}
    run.precondition = {"kind": "table", "schema": "dbo", "name": "t", "exists": False}
    job = _job(tmp_path, monkeypatch, run)
    assert job["statements"] == ["UPDATE t SET a = :a", "SELECT 1"] and job["parts_dir"] == str(run.folder)
    assert job["params"] == [{"name": "a", "type": "number", "value": "1"}]
    assert job["precondition"] == run.precondition and job["writes"] is True
    assert run.params == []  # the values go to the worker only


def test_a_one_query_warehouse_run_sends_no_statements_values_or_precondition(tmp_path, monkeypatch):
    run = se.Execution(id="w2", query="SELECT 1", dialect="tsql", lakehouse={}, allow=("read_only",),
                       folder=tmp_path / "w2", target="warehouse")
    run.warehouse = {"workspace_id": "ws", "item_id": "wh", "kind": "warehouse", "server": "srv", "database": "Sales"}
    job = _job(tmp_path, monkeypatch, run)
    assert not {"statements", "parts_dir", "params", "precondition"} & set(job) and job["writes"] is False


def test_a_lakehouse_run_sends_its_values_explain_and_memory_and_only_what_it_has(tmp_path, monkeypatch):
    run = se.Execution(id="l1", query="SELECT :a", dialect="spark", lakehouse=dict(LH, extra="x"), allow=("read_only",),
                       folder=tmp_path / "l1", params=[{"name": "a", "type": "number", "value": "1"}], profile=True)
    job = _job(tmp_path, monkeypatch, run)
    assert job["params"] == [{"name": "a", "type": "number", "value": "1"}] and job["explain"] == "profile"
    assert job["lakehouse"] == LH and job["temp_dir"] == str(run.folder / "spill")
    assert (job["memory_mb"], job["memory_cap_mb"], job["memory_bound"], job["free_mb"]) == (512, 1024, "percent", 4096)
    plain = se.Execution(id="l2", query="SELECT 1", dialect="spark", lakehouse=dict(LH), allow=("read_only",),
                         folder=tmp_path / "l2")
    job = _job(tmp_path, monkeypatch, plain)
    assert not {"params", "explain", "statements", "parts_dir", "local_root"} & set(job)


def _arrow(path, values):
    import pyarrow as pa
    table = pa.table({"n": values})
    path.parent.mkdir(parents=True, exist_ok=True)
    with pa.OSFile(str(path), "wb") as sink, pa.ipc.new_file(sink, table.schema) as w:
        w.write_table(table)


def test_a_page_of_a_statements_result_says_which_statement_and_the_runs_own_page_doesnt(tmp_path):
    m = _manager(tmp_path)
    run = se.Execution(id="p1", query="q", dialect="spark", lakehouse=dict(LH), allow=("read_only",), folder=tmp_path / "p1",
                       units=["SELECT 1", "SELECT 2"])
    run.state, run.rows, run.offsets, run.columns = "completed", 2, [0], [{"name": "n", "type": "int64"}]
    run.parts = [se._part({"statement": 1, "rows": 3, "batches": [3], "columns": [{"name": "n", "type": "int64"}]}),
                 se._part({"statement": 2, "rows": 2, "batches": [2], "columns": [{"name": "n", "type": "int64"}]})]
    _arrow(run.part_file(1), [1, 2, 3])
    _arrow(run.part_file(2), [7, 8])
    m._runs[run.id] = run
    first = m.page("p1", part=1)
    assert first["part"] == 1 and first["rows"] == [[1], [2], [3]]
    own = m.page("p1")
    assert "part" not in own and own["rows"] == [[7], [8]]
    sorted_part = m.page("p1", part=1, sort=[{"column": 0, "desc": True}])
    assert sorted_part["part"] == 1 and sorted_part["rows"] == [[3], [2], [1]]


def test_a_json_export_notes_whole_numbers_too_big_for_javascript_and_writes_without_notes(tmp_path):
    import pyarrow as pa
    from fabric_desktop.frames import ExportNotes
    path = tmp_path / "r.arrow"
    _arrow(path, [1, 2 ** 53, None, 2 ** 53 - 1])
    with pa.memory_map(str(path)) as source:
        reader = pa.ipc.open_file(source)
        sink, notes = io.BytesIO(), ExportNotes(reader.schema)
        export = se.Export(id="e1", execution="p1", format="json", path=tmp_path / "r.json")
        se.SqlExecutions._write_rows(export, reader, reader.schema, sink, notes)
        assert json.loads(sink.getvalue()) == [{"n": 1}, {"n": 2 ** 53}, {"n": None}, {"n": 2 ** 53 - 1}]
        assert len(notes.sentences()) == 1 and notes.sentences()[0].startswith("n: 1 whole number is beyond")
        plain = io.BytesIO()
        se.SqlExecutions._write_rows(se.Export(id="e2", execution="p1", format="json", path=tmp_path / "s.json"),
                                     reader, reader.schema, plain)
        assert json.loads(plain.getvalue()) == [{"n": 1}, {"n": 2 ** 53}, {"n": None}, {"n": 2 ** 53 - 1}]


def test_a_full_disk_names_the_file_that_failed_or_else_the_projects_folder(monkeypatch):
    import errno
    import types

    from fabric_desktop import disk_full
    monkeypatch.setattr(disk_full, "free_space", lambda path: f"looked at {path}")
    project = types.SimpleNamespace(settings=None, root="C:/data/crm")
    named = OSError(errno.ENOSPC, "No space left on device", "D:/spill/t.tmp")
    assert se.local_error(named, project, None) == ("disk", disk_full.message("D:/spill/t.tmp", "The statement that ran out of room didn't finish."))
    assert "(looked at D:/spill/t.tmp)" in se.local_error(named, project, None)[1]
    unnamed = OSError(errno.ENOSPC, "No space left on device")
    assert "(looked at C:/data/crm)" in se.local_error(unnamed, project, None)[1]
    assert "(looked at snap.arrow)" in se.local_error(unnamed, types.SimpleNamespace(settings=None), "snap.arrow")[1]



def test_an_export_takes_names_up_to_999_and_no_further(tmp_path):
    (tmp_path / "r.csv").touch()
    for n in range(2, 999):
        (tmp_path / f"r ({n}).csv").touch()
    assert se._reserve(tmp_path, "r", ".csv") == tmp_path / "r (999).csv"

    with pytest.raises(ValueError, match="Couldn't find a free file name in Downloads for the export."):
        se._reserve(tmp_path, "r", ".csv")
    assert not (tmp_path / "r (1000).csv").exists()


def test_a_runs_repr_leaves_out_its_sql_values_process_and_timer(tmp_path):
    run = se.Execution(id="r1", query="SELECT secret_col FROM t", dialect="spark", lakehouse=dict(LH), allow=("read_only",),
                       folder=tmp_path, units=["SELECT secret_col FROM t"], params=[{"name": "a", "value": "v"}])
    run.proc, run.timer = object(), object()
    text = repr(run)
    assert "secret_col" not in text and "'v'" not in text
    assert "proc=" not in text and "timer=" not in text and "id='r1'" in text


def test_a_snapshot_is_offered_only_for_a_completed_run_whose_file_is_there(tmp_path):
    run = se.Execution(id="s1", query="q", dialect="spark", lakehouse=dict(LH), allow=("read_only",), folder=tmp_path)
    run.state = "completed"
    assert run.status()["snapshot"] is False  # no file
    _arrow(run.snapshot, [1])
    assert run.status()["snapshot"] is True
    run.state = "failed"
    assert run.status()["snapshot"] is False  # a file left by a failed run isn't a result


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


def test_a_short_snapshot_needs_a_rerun_when_paged(tmp_path):
    m = _manager(tmp_path)
    run = se.Execution(id="p2", query="q", dialect="spark", lakehouse=dict(LH), allow=("read_only",), folder=tmp_path / "p2")
    run.state, run.rows, run.offsets, run.columns = "completed", 5, [0], [{"name": "n", "type": "int64"}]
    _arrow(run.snapshot, [1, 2])
    m._runs[run.id] = run
    with pytest.raises(se.RerunNeeded, match="holds fewer rows than the 5 the query returned"):
        m.page("p2")


def test_a_result_that_was_cleaned_up_cant_be_pinned(tmp_path):
    m = _manager(tmp_path)
    run = se.Execution(id="p3", query="q", dialect="spark", lakehouse=dict(LH), allow=("read_only",), folder=tmp_path / "gone")
    run.state = "completed"
    m._runs[run.id] = run
    with pytest.raises(se.RerunNeeded, match="This result was cleaned up, so it can't be pinned"):
        m.pin("p3")
    assert run.pinned is False


def test_a_warehouse_read_keeps_a_begin_and_commit_as_statements_of_their_own(tmp_path):
    plan = _manager(tmp_path).start("", target="warehouse", dialect="tsql", dry_run=True,
                                    warehouse_ref={"workspace_id": "ws", "item_id": "wh", "kind": "warehouse"},
                                    statements=["BEGIN TRAN", "SELECT 1", "COMMIT"])
    assert [s["statement"] for s in plan["statements"]] == [1, 2, 3]
    assert plan["refused"]["statement"] == 1


@pytest.mark.parametrize("error, shown", [(OSError("Python couldn't start"), "Python couldn't start"), (OSError(), "OSError")])
def test_a_worker_that_cant_start_says_why_in_its_own_words(tmp_path, monkeypatch, error, shown):
    def popen(*args, **kwargs):
        raise error
    monkeypatch.setattr(se.subprocess, "Popen", popen)
    m = _no_worker(tmp_path)
    s = m.start("SELECT 1", lakehouse_ref=LH)
    assert (m.status(s["id"])["state"], m.status(s["id"])["error"]) == ("failed", shown)


def test_a_finished_query_that_changed_nothing_says_so(tmp_path):
    m = _manager(tmp_path)
    run = se.Execution(id="d1", query="q", dialect="spark", lakehouse=dict(LH), allow=("read_only",), folder=tmp_path / "d1")
    run.state = "running"
    m._runs[run.id] = run
    proc = _Worker()
    proc.stdout = iter([json.dumps({"event": "done", "rows": 2, "batches": [2]}) + "\n"])
    m._read(run, proc)
    assert (run.state, run.affected, run.committed, run.rows) == ("completed", 0, False, 2)


def test_the_workers_questions_are_answered_on_daemon_threads(tmp_path, monkeypatch):
    started = []

    class Thread:
        def __init__(self, target=None, args=(), daemon=None, name=""):
            started.append((name, daemon))

        def start(self):
            pass
    monkeypatch.setattr(se.threading, "Thread", Thread)
    m = _manager(tmp_path)
    run = se.Execution(id="q1", query="q", dialect="spark", lakehouse=dict(LH), allow=("read_only",), folder=tmp_path / "q1")
    run.state = "running"
    m._runs[run.id] = run
    proc = _Worker()
    proc.stdout = iter([json.dumps({"event": "call", "name": "token"}) + "\n"])
    m._read(run, proc)
    assert started == [("sql-call", True)]


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


def test_a_pc_whose_memory_cant_be_read_is_taken_to_have_8_gb(monkeypatch):
    from fabric_desktop import free_memory, side_by_side
    monkeypatch.setattr(side_by_side, "_memory_gb", lambda: None)
    monkeypatch.setattr(free_memory, "available", lambda: None)
    asked = []
    monkeypatch.setattr(free_memory, "budget", lambda mb, free: asked.append(mb) or {"duckdb_mb": mb})
    assert se.memory_mb(50) == 4096 and asked == [4096]


def test_results_past_retention_go_and_with_retention_off_they_stay(tmp_path):
    import time
    for hours, kept in ((24, False), (0, True)):
        limits = dict(LIMITS, sql_result_retention_hours=hours)
        m = _manager(tmp_path / str(hours), limits=lambda limits=limits: limits)
        run = se.Execution(id="old1", query="q", dialect="spark", lakehouse=dict(LH), allow=("read_only",),
                           folder=tmp_path / str(hours) / "old1")
        run.folder.mkdir(parents=True)
        run.state, run.ended = "completed", time.time() - 2 * 86400
        m._runs[run.id] = run
        m.cleanup()
        assert run.folder.exists() is kept


def test_an_earlier_sessions_new_folder_stays_while_retention_is_an_hour(tmp_path):
    limits = dict(LIMITS, sql_result_retention_hours=1)
    m = _manager(tmp_path, limits=lambda: limits)
    left = m.base() / "cccccccccccc"
    left.mkdir(parents=True)
    m.cleanup(earlier_sessions=True)
    assert left.exists()


def test_a_name_ending_in_e_before_a_quote_starts_a_plain_string_and_a_leading_e_an_escape_string():
    assert se._outside_command("SELECT name'a\\'; EXPORT DATABASE 'x'") == "EXPORT"
    assert se._outside_command("E'a\\'; EXPORT DATABASE 'x' y") == ""


def test_a_warehouse_change_is_audited_with_what_it_changed_and_where_it_failed(tmp_path):
    rows = []
    m = _manager(tmp_path, audit=lambda *a: rows.append(a))
    wh = {"workspace_id": "ws", "item_id": "wh", "kind": "warehouse", "database": "Sales"}
    change = se.Execution(id="a1", query="q", dialect="tsql", lakehouse={}, allow=("read_only", "warehouse_write"),
                          folder=tmp_path / "a1", target="warehouse", plan=False)
    change.warehouse, change.state, change.affected, change.failed_batch, change.error_kind = wh, "failed", 3, 2, "sql"
    read = se.Execution(id="a2", query="q", dialect="tsql", lakehouse={}, allow=("read_only",), folder=tmp_path / "a2",
                        target="warehouse")
    read.warehouse, read.state = wh, "completed"
    m._finished(change)
    m._finished(read)
    (kind, outcome, _s, first), (_k, ok, _t, second) = rows
    assert (kind, outcome, ok) == ("sql_execution", "error", "ok")
    assert (first["warehouse"], first["affected"], first["committed"], first["failed_batch"], first["error_kind"]) == (
        "Sales", 3, False, 2, "sql")
    assert not {"affected", "committed", "failed_batch", "error_kind", "plan"} & set(second)
    assert change.query == "" and read.query == ""  # the SQL isn't kept once the run is over


def test_an_error_with_no_batch_names_no_batch(tmp_path):
    m = _manager(tmp_path)
    run = se.Execution(id="b1", query="q", dialect="spark", lakehouse=dict(LH), allow=("read_only",), folder=tmp_path / "b1")
    run.state = "running"
    m._runs[run.id] = run
    proc = _Worker()
    proc.stdout = iter([json.dumps({"event": "error", "message": "Table not found"}) + "\n"])
    m._read(run, proc)
    assert (run.state, run.error_kind, run.error, run.failed_batch) == ("failed", "sql", "Table not found", 0)
    assert "failed_batch" not in run.status()


@pytest.mark.parametrize("query, word", [
    ("SELECT 'a'; EXPORT DATABASE 'x'", "EXPORT"),
    ('SELECT "a"; IMPORT DATABASE \'x\'', "IMPORT"),
    ("/* note */EXPORT DATABASE 'x'", "EXPORT"),
    ("SELECT 1; /* note */IMPORT DATABASE 'x'", "IMPORT"),
    ("/**/EXPORT DATABASE 'x'", "EXPORT"),
    ("SELECT a_$$; EXPORT DATABASE 'x'", "EXPORT"),
])
def test_a_command_right_after_a_string_or_a_comment_is_still_found(query, word):
    assert se._outside_command(query) == word


def test_at_most_ten_results_are_pinned_at_once(tmp_path):
    m = _manager(tmp_path)
    for k in range(se.MAX_PINNED + 1):
        run = se.Execution(id=f"pin{k}", query="q", dialect="spark", lakehouse=dict(LH), allow=("read_only",),
                           folder=tmp_path / f"pin{k}")
        run.folder.mkdir()
        run.state = "completed"
        m._runs[run.id] = run
    for k in range(se.MAX_PINNED):
        assert m.pin(f"pin{k}")["pinned"] is True
    with pytest.raises(ValueError, match="Up to 10 results can be pinned at once. Unpin one first."):
        m.pin(f"pin{se.MAX_PINNED}")
    assert m._runs[f"pin{se.MAX_PINNED}"].pinned is False


def test_a_caller_may_shorten_the_organizations_time_limit_even_below_a_second(tmp_path):
    m = _manager(tmp_path)
    m._local_engine = lambda project: _Engine(lambda: None)
    limits = dict(LIMITS, sql_time_limit_minutes=10, sql_time_limit_by_policy=True)
    for timeout, expected in ((0.5, (0.5, "caller")), (3600.0, (600.0, "policy"))):
        run = se.Execution(id=f"t{timeout}", query="SELECT 1", dialect="duckdb", lakehouse={}, folder=tmp_path / f"t{timeout}",
                           allow=se.TARGET_ALLOWS["local"], target="local", project="crm", timeout_s=timeout)
        m._runs[run.id] = run
        m._launch_local(run, limits)
        _join(run.id)
        assert (run.time_limit_s, run.time_limit_from) == expected
