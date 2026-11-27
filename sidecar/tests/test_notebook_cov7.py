"""notebook.py: line and branch coverage of the kernel's display, output, run and save paths that
the other notebook tests leave out. Nothing starts a real kernel process or reaches Fabric: each test
runs cells in an in-process Kernel, or calls the module's helpers directly."""
import io
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop import notebook
from fabric_desktop.notebook import Kernel, NotebookStore, _Router, _Run


# ---- display


def test_display_draws_a_matplotlib_figure_as_png_and_closes_it():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3])
    out = notebook.display(fig)
    assert out["type"] == "image" and out["mime"] == "image/png" and len(out["data"]) > 100
    assert fig.number not in plt.get_fignums(), "a figure display() showed is closed, as Jupyter does"
    # An Axes isn't a figure: its figure is drawn.
    fig2, ax2 = plt.subplots()
    assert notebook.display(ax2)["type"] == "image"
    plt.close("all")


def test_update_display_replaces_the_output_in_place_and_needs_an_id():
    k = Kernel()
    out = k.run("h = display('first', display_id='d1')\nh.update('second')\ndisplay('third')")
    assert out["status"] == "ok"
    texts = [o.get("text") for o in out["outputs"]]
    assert texts == ["'second'", "'third'"]
    assert out["outputs"][0]["display_id"] == "d1"
    # An update for an id this cell never showed changes nothing.
    assert [o.get("text") for o in k.run("display('x', display_id='nowhere', update=True)\nprint('done')")["outputs"]] == ["done\n"]
    bad = k.run("display('x', update=True)")
    assert bad["status"] == "error" and bad["outputs"][0]["evalue"] == "update_display needs a display_id."


def test_display_of_a_raw_bundle_and_a_summary_table():
    k = Kernel()
    out = k.run("display({'text/plain': 'plain only'}, raw=True)\n"
                "import polars as pl\ndisplay(pl.DataFrame({'a': [1]}), summary=True)")
    first, second = out["outputs"]
    assert first == {"type": "text", "text": "plain only"}
    assert second["type"] == "table" and second["summary"] is True


def test_display_outside_a_cell_prints_what_it_would_show(capsys):
    k = Kernel()
    k._display("hello")
    k._display_html(b"<b>bytes</b>")
    printed = capsys.readouterr().out
    assert "'hello'" in printed
    assert "<b>bytes</b>" in printed
    k._clear_output()  # no cell: nothing to clear, and no error


def test_display_ids_kept_for_updates_are_capped(monkeypatch):
    monkeypatch.setattr(notebook, "MAX_DISPLAY_IDS", 2)
    k = Kernel()
    run = _Run(1)
    for did in ("a", "b", "c"):
        k._keep_display_sink(did, run)
    k._keep_display_sink("c", run)  # the same place again isn't kept twice
    assert list(k._display_sinks) == ["b", "c"]
    assert k._display_sinks["c"] == [run]


# ---- a run's outputs


def test_set_paused_keeps_a_json_copy_and_counts_a_change():
    run = _Run(1)
    run.set_paused({"line": 3, "where": Path("x")})
    assert run.paused == {"line": 3, "where": "x"} and run.version == 1
    run.set_paused(None)
    assert run.paused is None and run.version == 2


def test_the_first_write_past_the_cap_keeps_nothing_when_there_is_no_room(monkeypatch, tmp_path):
    monkeypatch.setattr(notebook, "STREAM_CHARS", 10)
    monkeypatch.setenv(notebook.SPILL_DIR_ENV, str(tmp_path / "spills"))
    run = _Run(1)
    run.write("0123456789")  # exactly full
    run.write("abc")         # no room left: all of it goes to the file
    run.finish("ok")
    snap = run.snapshot()
    assert snap["outputs"][0]["text"].startswith("0123456789")
    assert "Output truncated" in json.dumps(snap["outputs"])
    assert run.spill is not None
    notebook._drop_spill(run.spill)


def test_a_spill_file_that_fails_to_write_is_let_go(monkeypatch):
    run = _Run(1)

    class Broken(io.StringIO):
        def write(self, _text):
            raise OSError("disk gone")

    f = Broken()
    run.spill_file = f
    run._spill_out("text")
    assert run.spill_file is None and f.closed


def test_spill_file_stops_at_its_own_cap(monkeypatch):
    monkeypatch.setattr(notebook, "SPILL_CHARS", 5)
    run = _Run(1)
    f = io.StringIO()
    f.close = lambda: None  # keep the text readable after the run lets go of it
    run.spill_file = f
    run._spill_out("0123456789")
    assert f.getvalue().startswith("01234\n[KratosForge stopped keeping this cell's output at 5 characters.]")
    assert run.spill_file is None


def test_output_widget_sink_routes_replace_through_the_widget():
    calls = []
    widget = SimpleNamespace(_replace=lambda did, out: calls.append((did, out)))
    notebook._OutputSink(widget).replace("d1", {"type": "text", "text": "x"})
    assert calls == [("d1", {"type": "text", "text": "x"})]


def test_router_writes_to_the_fallback_off_a_cell_thread():
    fallback = io.StringIO()
    r = _Router(fallback)
    assert r.writable() and r.encoding == "utf-8"
    assert r.write("hi") == 2
    assert fallback.getvalue() == "hi"
    assert _Router(None).write("lost") == 4


# ---- shared sessions


def test_forget_guest_drops_the_guests_variables_only():
    k = Kernel()
    with k.as_guest("Other"):
        k.run("guest_var = 1")
    k.run("host_var = 2")
    assert k.forget_guest("Other") == {"forgotten": True}
    assert k.forget_guest("Other") == {"forgotten": False}
    assert "host_var" in k.namespace


def test_a_guest_sees_its_own_last_cell_and_the_host_busy():
    k = Kernel()
    with k.as_guest("Other"):
        k.run("1 + 1")
    gate = threading.Event()
    k.namespace["gate"] = gate
    k.start("gate.wait(5)")
    try:
        with k.as_guest("Other"):
            snap = k.poll()
            assert snap["session_running"] is True
            assert snap["outputs"] == [{"type": "text", "text": "2"}]
            with pytest.raises(RuntimeError, match="running a cell in this shared session"):
                k.check_free()
    finally:
        gate.set()
        k._current.done.wait(5)


def test_a_guest_restarting_python_clears_only_its_own_variables():
    k = Kernel()
    k.run("host_var = 1")
    with k.as_guest("Other"):
        k.run("guest_var = 1")
        out = k.run("dbutils.library.restartPython()")
    assert "Python restarted for this notebook" in json.dumps(out["outputs"])
    assert "Other" not in k._guests and k.namespace["host_var"] == 1


def test_a_spark_that_cant_start_leaves_plain_python(monkeypatch, capfd):
    from fabric_desktop import spark_local

    def broken(_ns):
        raise RuntimeError("no duckdb here")
    monkeypatch.setattr(spark_local, "install", broken)
    k = Kernel()
    assert "spark is not available on this PC: no duckdb here" in capfd.readouterr().err
    assert k.run("1 + 2")["outputs"] == [{"type": "text", "text": "3"}]


# ---- starting a cell


def test_a_cells_write_check_and_widget_values_are_handed_to_its_thread():
    from fabric_desktop import lakehouse
    k = Kernel()
    k.run("dbutils.widgets.text('env', 'dev')")
    k.widget_values = {"env": "prod"}
    seen = {}
    k.write_check = object()
    k.namespace["seen"] = seen
    k.namespace["lakehouse"] = lakehouse
    k.start("import threading\nseen['check'] = lakehouse.cell_write_checks.get(threading.current_thread())\n"
            "seen['env'] = dbutils.widgets.get('env')")
    k._current.done.wait(10)
    assert seen == {"check": k.write_check, "env": "prod"}


# ---- writes at the end of a cell


class _Writes:
    def __init__(self, failed=(), warnings=(), interrupt=False):
        self.failed, self.warnings, self.interrupt, self.flushed = list(failed), list(warnings), interrupt, []

    def flush(self, cancel=False):
        self.flushed.append(cancel)
        if self.interrupt and len(self.flushed) == 1:
            raise KeyboardInterrupt
        return self.failed


def test_finish_writes_says_a_failed_write_and_warns():
    k = Kernel()
    run = _Run(1)
    status = k._finish_writes(_Writes(failed=["sales: 403"], warnings=["slow upload"]), run, "ok")
    assert status == "error"
    outs = run.snapshot()["outputs"]
    assert outs[0] == {"type": "text", "text": "Warning: slow upload\n"}
    assert outs[1]["ename"] == "WriteFailed" and "sales: 403" in outs[1]["evalue"]


def test_finish_writes_cancelled_by_a_second_interrupt_lists_what_was_dropped():
    k = Kernel()
    run = _Run(1)
    writes = _Writes(failed=["orders: not written"], interrupt=True)
    assert k._finish_writes(writes, run, "ok") == "cancelled"
    assert writes.flushed == [False, True] and run.interrupted
    assert run.snapshot()["outputs"] == [{"type": "text", "text": "orders: not written"}]
    assert k._finish_writes(_Writes(), _Run(2), "cancelled") == "cancelled"


# ---- magics


def test_capture_inside_a_capture_puts_the_outer_one_back():
    k = Kernel()
    out = k.run("%%capture outer\nprint('a')\nk_inner = 1")
    assert out["outputs"] == []
    shown = k.run("outer.show()\nrepr(outer)")
    assert shown["outputs"][0] == {"type": "stream", "text": "a\n"}
    assert shown["outputs"][1]["text"] == "'<CapturedOutput: 1 output>'"


def test_writefile_refuses_a_bad_quote_and_a_folder_it_cant_write(tmp_path):
    k = Kernel()
    bad = k.run('%%writefile "unclosed\nx = 1')
    assert bad["status"] == "error" and bad["outputs"][0]["ename"] == "ValueError"
    blocker = tmp_path / "file"
    blocker.write_text("x")
    out = k.run(f"%%writefile {blocker / 'inside.py'}\nx = 1")
    assert out["status"] == "error" and out["outputs"][0]["ename"] in ("FileExistsError", "NotADirectoryError")
    assert not (blocker / "inside.py").exists()


def test_pip_install_in_a_guest_is_refused_and_conda_isnt_here():
    k = Kernel()
    with k.as_guest("Other"):
        out = k.run("%pip install requests")
    assert out["status"] == "error" and "shares another notebook's session" in out["outputs"][0]["evalue"]
    conda = k.run("%conda install numpy")
    assert "%conda isn't on this PC" in conda["outputs"][0]["evalue"]


def test_history_prints_what_ran():
    k = Kernel()
    k.run("x = 41")
    out = k.run("%history")
    assert "x = 41" in out["outputs"][0]["text"]


def test_configure_report_reads_each_kind_of_setting(monkeypatch):
    from fabric_desktop import lakehouse
    monkeypatch.setattr(lakehouse, "name", lambda: "Sales")
    text = notebook.configure_report(json.dumps({"conf": {}, "defaultLakehouse": {"name": "Sales"},
                                                 "driverMemory": "28g", "jars": ["a.jar"], "Bogus": 1}))
    assert "- conf: none" in text
    assert "- defaultLakehouse: Sales (the same one is attached here)" in text
    assert "- driverMemory: 28g" in text
    assert "Fabric doesn't know Bogus" in text
    with pytest.raises(ValueError, match="JSON object"):
        notebook.configure_report("[1]")
    monkeypatch.setattr(lakehouse, "name", lambda: "")
    assert "no attached lakehouse; attach Gold" in notebook.configure_report('{"defaultLakehouse": {"name": "Gold"}}')


def test_duration_of_no_time_is_in_ns():
    assert notebook._duration(0) == "0 ns"
    assert notebook._duration(2.5) == "2.5 s"


def test_fs_magic_without_a_command():
    assert notebook.fs_magic_blocker("") is None
    with pytest.raises(ValueError, match="%fs needs a command"):
        notebook._fs_magic(object(), "")


def test_run_builtin_refuses_current_resources_inside_another_run(tmp_path):
    k = Kernel()
    k._local.depth = 1
    try:
        with pytest.raises(NotImplementedError, match="%run -c inside a notebook"):
            k._run_builtin("-c", "x.py")
    finally:
        k._local.depth = 0
    with pytest.raises(FileNotFoundError, match="no resources folder yet"):
        k._run_builtin("-b", "x.py")


def test_run_builtin_sql_and_other_files(tmp_path):
    k = Kernel()
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    (builtin / "q.sql").write_text("SELECT 1 AS one", encoding="utf-8")
    (builtin / "bad.sql").write_text("SELEC nonsense", encoding="utf-8")
    (builtin / "notes.txt").write_text("hi", encoding="utf-8")
    k.resources_dir = str(tmp_path)
    with pytest.raises(ValueError, match="takes no arguments"):
        k._run_builtin("-b", 'q.sql {"a": 1}')
    with pytest.raises(ValueError, match="neither"):
        k._run_builtin("-b", "notes.txt")
    with pytest.raises(RuntimeError, match="bad.sql failed"):
        k._run_builtin("-b", "bad.sql")


def test_run_notebook_without_a_loader_and_too_deep():
    k = Kernel()
    with pytest.raises(RuntimeError, match="can't be opened here"):
        k._run_notebook_in("x", {}, {})
    k.load_notebook = lambda _n: []
    k._local.depth = notebook.MAX_RUN_DEPTH
    try:
        with pytest.raises(RecursionError, match="stops at"):
            k._run_notebook_in("x", {}, {})
    finally:
        k._local.depth = 0


def test_a_run_notebook_that_asks_to_restart_python_is_refused():
    cells = [{"type": "code", "source": "dbutils.library.restartPython()"}]
    k = Kernel(load_notebook=lambda _n: cells)
    out = k.run('%run child')
    assert out["status"] == "error"
    assert "asked to restart Python, which only the notebook you run can do" in out["outputs"][-1]["evalue"]


def test_run_arguments_that_arent_a_dict():
    with pytest.raises(ValueError, match="neither JSON nor a Python dict"):
        notebook._run_target("child {nope")
    with pytest.raises(ValueError, match="are a {name: value} object"):
        notebook._run_target("child {1, 2}")


# ---- proofs, variables, intellisense


def test_finish_proof_without_one_and_unbind_without_one():
    k = Kernel()
    k.unbind_proof()
    with pytest.raises(RuntimeError, match="No proof is running"):
        k.finish_proof([])


def test_complete_and_inspect_with_nothing_to_read():
    k = Kernel()
    assert k.complete("1 + ") == {"from": 4, "items": []}
    assert k.complete("nothing_here.") == {"from": 13, "items": []}
    assert k.inspect("undefined_fn(", call=True) is None
    assert k.inspect("   ", cursor=1) is None
    assert k.inspect("undefined_name", cursor=3) is None


def test_lookup_stops_at_a_property_and_a_missing_attribute():
    k = Kernel()
    k.run("class C:\n    @property\n    def p(self):\n        raise AssertionError('not run')\nc = C()")
    assert k._lookup("c.p") is notebook._MISSING
    assert k._lookup("c.nope") is notebook._MISSING
    assert k._lookup("c.p.more") is notebook._MISSING


def test_frame_page_and_chart_read_a_shown_frame():
    k = Kernel()
    out = k.run("import polars as pl\npl.DataFrame({'region': ['E', 'W'], 'n': [1, 2]})")
    frame = out["outputs"][0]["frame"]
    page = k.frame_page(frame, 0, 10)
    assert page["rows"] == [["E", 1], ["W", 2]]
    chart = k.frame_chart(frame, kind="bar", x=0, y=1, agg="sum")
    assert chart["categories"] == ["W", "E"], "bar keeps the largest first"
    assert chart["series"] == [{"name": None, "values": [2.0, 1.0]}]


# ---- names, watches and helpers


def test_check_watches_refuses_what_isnt_a_watch():
    assert notebook.check_watches(None) == []
    with pytest.raises(ValueError, match="list of expressions"):
        notebook.check_watches("x")
    assert notebook.check_watches(["x", " x ", ""]) == ["x"]
    with pytest.raises(ValueError, match="up to 200 characters"):
        notebook.check_watches(["x" * 201])
    with pytest.raises(ValueError, match="isn't an expression"):
        notebook.check_watches(["x = 1"])


def test_size_and_preview_of_values():
    import pyarrow as pa
    assert notebook._size(pa.table({"a": [1, 2]})) == "2 × 1"
    assert notebook._size([1, 2, 3]) == "3"
    import polars as pl
    wide = pl.DataFrame({f"c{i}": [i] for i in range(8)})
    assert notebook._preview(wide).endswith(", ...")


def test_notebook_files_skip_a_folder_that_cant_be_read(tmp_path, monkeypatch):
    (tmp_path / "a.ipynb").write_text("{}")
    sub = tmp_path / "Sub"
    sub.mkdir()
    real = Path.iterdir

    def iterdir(self):
        if self == sub:
            raise PermissionError("denied")
        return real(self)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    assert [n for n, _p in notebook.notebook_files(tmp_path)] == ["a"]


def test_cell_metadata_keeps_breakpoints_both_ways():
    md = notebook._cell_metadata({"breakpoints": [3, 1, 3]})
    assert md["fabric_desktop"]["breakpoints"] == [1, 3]
    assert notebook._cell_meta(md)["breakpoints"] == [1, 3]


def test_assign_cell_ids_makes_a_new_one_when_the_made_one_repeats():
    cells = [{"cell_id": "abc"}, {"cell_id": "abc"}]
    ids = notebook.assign_cell_ids(cells, make=lambda _i, _c: "abc")
    assert ids[0] == "abc" and ids[1] != "abc" and len(set(ids)) == 2


def test_merge_cell_metadata_drops_what_the_app_owns_and_keeps_the_rest():
    before = {"tags": ["parameters"], "editable": False, "deletable": True,
              "jupyter": {"source_hidden": True, "other": 1}, "run_control": {"frozen": True},
              "fabric_desktop": {"chart": {}, "mine": 1}, "keep": 2}
    out = notebook._merge_cell_metadata({}, before)
    assert out == {"deletable": True, "jupyter": {"other": 1}, "fabric_desktop": {"mine": 1}, "keep": 2}
    out2 = notebook._merge_cell_metadata({"fabric_desktop": {"x": 1}}, {"fabric_desktop": {"chart": {}}})
    assert out2 == {"fabric_desktop": {"x": 1}}


def test_unchanged_when_a_cell_isnt_a_dict():
    nb = {"cells": [{"cell_type": "code"}], "metadata": {}}
    assert notebook.unchanged(nb, {"cells": ["oops"], "metadata": {}}) is False


def test_dumps_like_keeps_a_file_with_unparsable_keys(tmp_path):
    nb = {"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    text = notebook.dumps_like(nb, b"not json\n  at all")
    assert json.loads(text) == nb


def test_merge_saved_keeps_a_higher_minor_version():
    new = notebook.to_ipynb([{"type": "code", "source": "x"}], None)
    before = json.loads(json.dumps(new))
    before["nbformat_minor"] = 99
    assert notebook.merge_saved(new, before)["nbformat_minor"] == 99


# ---- the store


def test_write_atomic_says_a_folder_it_cant_write(tmp_path, monkeypatch):
    from fabric_desktop import file_locks
    path = tmp_path / "nb.ipynb"
    real_open = open

    def refuse(p, *a, **kw):
        if str(p).endswith(".tmp"):
            raise PermissionError(13, "Access is denied", str(p))
        return real_open(p, *a, **kw)
    monkeypatch.setattr("builtins.open", refuse)
    monkeypatch.setattr(file_locks, "folder_error", lambda exc, p: PermissionError(f"{p.parent} is protected"))
    with pytest.raises(PermissionError, match="is protected"):
        notebook._write_atomic(path, "{}")
    monkeypatch.setattr(file_locks, "folder_error", lambda exc, p: None)
    with pytest.raises(PermissionError, match="Access is denied"):
        notebook._write_atomic(path, "{}")


def test_raw_stamp_never_matches_a_notebooks():
    assert notebook.raw_stamp(b"<<<<<<<").startswith("raw-")


def test_store_refuses_a_stamp_for_a_file_it_cant_read(tmp_path):
    store = NotebookStore(tmp_path)
    store.save("Daily", [{"type": "code", "source": "x = 1"}], None)
    path = store.path("Daily")
    path.write_text("<<<<<<< HEAD\nconflict", encoding="utf-8")
    out = store.save("Daily", [{"type": "code", "source": "x = 2"}], None, expected_stamp="anything")
    assert out["changed_on_disk"] is True and out["unreadable"] is True
    assert out["disk_stamp"] == notebook.raw_stamp(path.read_bytes())


def test_store_set_refuses_keys_saved_with_the_notebook(tmp_path):
    store = NotebookStore(tmp_path)
    with pytest.raises(ValueError, match="saved with the notebook"):
        store.update_extra("Daily", "compute", lambda old, nb: old)


def test_store_rename_onto_an_existing_notebook_is_refused(tmp_path):
    store = NotebookStore(tmp_path)
    store.save("A", [], None)
    store.save("B", [], None)
    with pytest.raises(ValueError, match="already exists"):
        store.rename("A", "B")


def test_rename_puts_resources_back_when_the_notebook_cant_move(tmp_path, monkeypatch):
    store = NotebookStore(tmp_path)
    store.save("A", [], None)
    resources = notebook.notebook_resources.folder_for(tmp_path / "A.ipynb")
    (resources / "builtin").mkdir(parents=True)
    (resources / "builtin" / "helper.py").write_text("x = 1")
    real = Path.rename

    def rename(self, target):
        if self.suffix == ".ipynb":
            raise OSError("in use")
        return real(self, target)
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OSError, match="in use"):
        store.rename("A", "C")
    assert (tmp_path / "A.ipynb").exists() and not (tmp_path / "C.ipynb").exists()
    assert (resources / "builtin" / "helper.py").read_text() == "x = 1", "its resources are put back"
    assert not notebook.notebook_resources.folder_for(tmp_path / "C.ipynb").exists()

# ---- more of the kernel


def _wait_paused(k, timeout=30):
    # By the clock, not by poll count: a poll can return at once, and a busy Windows runner is slow to pause.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = k.poll(wait=0.05)
        if snap.get("paused"):
            return snap
    raise AssertionError("the cell never paused")


def test_a_breakpoint_pauses_in_a_function_and_the_last_expression_still_shows():
    k = Kernel()
    k.start("def f(a):\n    b = a + 1\n    return b\nx = f(1)\nx", breakpoints=[2])
    try:
        paused = _wait_paused(k)["paused"]
        assert paused["line"] == 2 and paused["reason"] == "breakpoint"
        assert paused["variables"] == [{"name": "a", "type": "int", "size": "", "value": "1", "memory": 28, "expandable": False}]
        assert k.debug("continue") == {"resumed": "continue"}
    finally:
        k._current.done.wait(30)
    assert k.poll()["outputs"] == [{"type": "text", "text": "2"}]


def test_a_cell_started_on_its_thread_can_capture_its_output():
    k = Kernel()
    k.start("%%capture kept\nprint('quiet')")
    k._current.done.wait(10)
    assert k.poll()["outputs"] == []
    assert k.namespace["kept"].outputs == [{"type": "stream", "text": "quiet\n"}]


def test_a_cell_runs_without_dbutils_widgets():
    k = Kernel()
    k.run("del dbutils")
    k.widget_values = {"env": "prod"}
    k.start("1 + 1")
    k._current.done.wait(10)
    assert k.poll()["outputs"] == [{"type": "text", "text": "2"}]


def test_history_with_nothing_run_prints_nothing():
    assert Kernel().run("%history")["outputs"] == []


def test_run_passes_arguments_to_a_notebook_with_no_parameters_cell():
    cells = [{"type": "code", "source": "print(day)"}]
    k = Kernel(load_notebook=lambda _n: cells)
    out = k.run('%run child {"day": "2026-01-01"}')
    assert out["status"] == "ok" and out["outputs"] == [{"type": "stream", "text": "2026-01-01\n"}]


def test_complete_skips_a_name_dir_lists_but_the_object_doesnt_have():
    k = Kernel()
    k.run("class Odd:\n    real = 1\n    def __dir__(self):\n        return ['real', 'ghost']\no = Odd()")
    items = k.complete("o.")["items"]
    names = {i["label"] if isinstance(i, dict) else i for i in items}
    assert {"real", "ghost"} <= names


def test_inspect_with_no_open_call_has_nothing_to_say():
    assert Kernel().inspect("abc", call=True) is None


def test_plain_error_says_what_application_control_blocked(monkeypatch):
    from fabric_desktop import app_control
    monkeypatch.setattr(app_control, "explain", lambda exc: "Windows blocked a package.")
    assert notebook.plain_error(ImportError("DLL load failed")).startswith("Windows blocked a package.")


def test_a_timeout_raised_inside_the_deadline_carries_the_deadlines_message():
    with pytest.raises(notebook.RunTimeout, match="^Cell 1 took too long$"):
        with notebook._deadline(600, "Cell 1 took too long"):  # far off: the timer never fires
            raise notebook.RunTimeout("inner")


def test_databricks_shim_leaves_an_installed_sdk_alone(monkeypatch):
    import importlib.util
    import sys
    monkeypatch.delitem(sys.modules, "databricks", raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object() if name == "databricks" else None)
    notebook._databricks_runtime(SimpleNamespace())
    assert "databricks" not in sys.modules


def test_builtin_paths_name_only_calls():
    assert notebook._BuiltinPaths._call_name(None) == ""
    import ast
    assert notebook._BuiltinPaths._call_name(ast.parse("open('x')").body[0].value) == "open"


def test_fs_magic_blocker_leaves_bad_quotes_to_the_kernel():
    assert notebook.fs_magic_blocker('ls "unclosed') is None


def test_size_of_a_value_with_a_shape():
    shaped = SimpleNamespace(shape=(2, 3))
    assert notebook._size(shaped) == "2 × 3"


def test_merge_saved_keeps_the_files_own_kernel_and_language():
    new = notebook.to_ipynb([{"type": "code", "source": "x"}], None)
    new["metadata"]["kernelspec"] = {"name": "python3"}
    new["metadata"]["extra"] = {"kept": True}
    before = json.loads(json.dumps(new))
    before["metadata"]["kernelspec"] = {"name": "synapse_pyspark"}
    merged = notebook.merge_saved(new, before)
    assert merged["metadata"]["kernelspec"] == {"name": "synapse_pyspark"}
    assert merged["metadata"]["extra"] == {"kept": True}


def test_capture_off_a_cell_thread_lets_go_of_the_router_after():
    k = Kernel()
    out, _err = notebook._routers()
    ident = threading.get_ident()
    out.targets.pop(ident, None)
    assert k._capture("got", "print('kept')", k.namespace, None) == "ok"
    assert k.namespace["got"].outputs == [{"type": "stream", "text": "kept\n"}]
    assert ident not in out.targets
