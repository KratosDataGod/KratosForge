"""Mutation batch 509, a re-score of notebook.py (batches 7 and 156). Cells run in a real Kernel on
this process, as in test_notebook_run.py; a clock that moves a fixed step for each read stands in for
time where a rounding is checked."""

from __future__ import annotations

import ast
import io
import threading
import time

import pyarrow as pa
import pytest

from fabric_desktop import lakehouse
from fabric_desktop import notebook as N


class _Clock:
    """notebook.time with a monotonic clock that moves ``step`` seconds each time it is read."""

    def __init__(self, step: float):
        self._now, self._step = 1000.0, step

    def monotonic(self) -> float:
        self._now += self._step
        return self._now

    def __getattr__(self, name):
        return getattr(time, name)


def _texts(out) -> list[str]:
    return [o.get("text", "") for o in out["outputs"]]


@pytest.fixture
def k():
    kernel = N.Kernel()
    yield kernel
    kernel._local.__dict__.clear()


# ---- values and displays


def test_a_query_naming_sqldf_reads_the_view_made_for_it():
    made = []

    class Frame:
        def createOrReplaceTempView(self, name):  # noqa: N802 - Spark's name
            made.append(name)
    ns = {"_sqldf": Frame()}
    out = N._with_sqldf("SELECT * FROM _sqldf WHERE x = '_sqldf'", ns)
    assert out == f"SELECT * FROM {made[0]} WHERE x = '_sqldf'"
    assert N._with_sqldf("SELECT 1 FROM _sqldf", ns) == f"SELECT 1 FROM {made[0]}" and len(made) == 1


def test_an_arrow_table_is_its_own_table():
    t = pa.table({"a": [1]})
    assert N.as_table(t) is t


def test_a_plotly_figure_shows_as_plotly():
    go = pytest.importorskip("plotly.graph_objects")
    assert N.display(go.Figure())["type"] == "plotly"


def test_a_spark_frame_shown_says_it_has_a_query_to_profile(k):
    [out] = k.run("spark.sql('select 1 as a')")["outputs"]
    assert out["type"] == "table" and out["spark"] is True


def test_the_size_of_text_and_lists_is_their_length():
    assert N._size("abcd") == "4" and N._size([1, 2]) == "2"


def test_an_update_past_the_rich_cap_names_the_cap_in_mb():
    old = {"type": "html", "display_id": "d", "html": "x"}
    _, shown = N._replace_capped([old], [0], {"type": "html", "display_id": "d", "html": "y" * 100}, N.RICH_CHARS)
    assert shown["text"].endswith("past 16 MB, so it isn't shown.")


def test_captured_output_counts_one_output_and_two_outputs(k):
    one = N.CapturedOutput([{"type": "stream", "text": "a"}], k)
    two = N.CapturedOutput([{"type": "stream", "text": "a"}, {"type": "text", "text": "b"}], k)
    assert repr(one) == "<CapturedOutput: 1 output>" and repr(two) == "<CapturedOutput: 2 outputs>"


# ---- a cell's run


def test_each_print_moves_the_runs_version():
    run = N._Run(1)
    run.write("a")
    run.write("b")
    assert run.version == 2 and run.outputs == [{"type": "stream", "text": "ab"}]


def test_an_update_replaces_the_output_shown_with_its_display_id():
    run = N._Run(1)
    run.add({"type": "html", "display_id": "d", "html": "old"})
    run.replace("d", {"type": "html", "display_id": "d", "html": "new"})
    assert run.outputs == [{"type": "html", "display_id": "d", "html": "new"}]


def test_a_finished_run_says_its_seconds_to_the_hundredth(monkeypatch):
    monkeypatch.setattr(N, "time", _Clock(0.1111))
    run = N._Run(1)
    run.finish("ok")
    assert run.seconds in {round(n * 0.1111, 2) for n in range(1, 10)}


def test_clear_output_clears_at_once_by_default(k):
    run = N._Run(1)
    run.write("a")
    k._local.run = run
    k._clear_output()
    assert run.outputs == []


def test_text_printed_with_no_cell_goes_to_the_fallback():
    back = io.StringIO()
    router = N._Router(back)
    assert router.write("hello") == 5 and back.getvalue() == "hello"


def test_a_cell_knows_a_cell_is_running(k):
    out = k.run("from fabric_desktop import notebook as _nb\n_nb._cell_running()")
    assert _texts(out) == ["True"]


def test_breakpoint_pauses_a_cell_under_the_debugger():
    paused = []

    class Debugger:
        def pause_here(self, frame):
            paused.append(frame)
            return False
    run = N._Run(1)
    run.debugger, run.thread = Debugger(), threading.current_thread()
    out, err = N._routers()
    ident = threading.get_ident()
    out.targets[ident] = err.targets[ident] = run
    try:
        assert N._breakpoint_hook() is None
    finally:
        out.targets.pop(ident, None)
        err.targets.pop(ident, None)
    assert len(paused) == 1 and run.outputs == []


@pytest.mark.xfail(strict=True, reason="bug: breakpoint() in a %%capture cell calls the hook that was there "
                                       "before (pdb, which waits for input that never comes) instead of carrying on "
                                       "with the note, because %%capture's run is made with count 0, the mark of a watch")
def test_breakpoint_in_a_capture_cell_carries_on(k, monkeypatch):
    before = []
    monkeypatch.setattr(N._breakpoint_hook, "previous", lambda *a, **kw: before.append(1), raising=False)
    out = k.run("%%capture c\nbreakpoint()\nprint('after')")
    assert out["status"] == "ok" and before == []


def test_capture_with_no_cell_leaves_no_target_behind(k):
    ns = k.namespace
    k._capture("c", "print(1)", ns, None)
    ident = threading.get_ident()
    assert all(ident not in r.targets for r in N._routers())
    assert ns["c"].stdout == "1\n"


def test_two_places_showing_one_display_id_are_both_kept(k):
    a, b = object(), object()
    k._keep_display_sink("d", a)
    k._keep_display_sink("d", b)
    k._keep_display_sink("d", a)
    assert k._display_sinks["d"] == [a, b]


def test_a_local_first_kernel_keeps_one_write_queue(k):
    from fabric_desktop import write_queue
    k.onelake_writes = "local_first"
    try:
        first = k._write_queue()
        assert first is not None and k._write_queue() is first
    finally:
        write_queue.cell_writes.queue = None


def test_a_watch_runs_after_the_cell(k):
    k.start("x = 41", watches=["x + 1"])
    assert k._current.done.wait(60)
    [watch] = k.poll()["watches"]
    assert watch["expr"] == "x + 1" and watch["value"] == "42"


def test_a_watch_says_its_seconds_to_the_thousandth(k, monkeypatch):
    k.namespace["x"] = 1
    monkeypatch.setattr(N, "time", _Clock(0.11111))
    run = N._Run(1)
    k._watch(["x"], run)
    assert run.watches[0]["seconds"] in {round(n * 0.11111, 3) for n in range(1, 10)}


def test_bounded_with_no_time_left_never_starts_the_read_or_a_timer(monkeypatch):
    reads = []

    def no_timer(*a, **kw):
        raise AssertionError("a timer was started")
    monkeypatch.setattr(N.threading, "Timer", no_timer)
    assert N._bounded(lambda: reads.append(1), lambda: "fallback", 0) == "fallback" and reads == []
    monkeypatch.undo()
    assert N._bounded(lambda: "read", lambda: "fallback", 5) == "read"


# ---- the shared session


def test_a_guests_namespace_is_its_own_and_kept(k):
    with k.as_guest("Guest"):
        ns = k.namespace
        assert isinstance(ns, dict) and "notebookutils" in ns
        ns["mine"] = 1
        assert k.namespace["mine"] == 1
    assert "mine" not in k.namespace


def test_forgetting_a_guest_doesnt_cancel_another_guests_cell(k):
    run = N._Run(1)
    run.guest = "B"
    k._current = run
    cancelled = []
    k.interrupt = lambda: cancelled.append(1)
    k.forget_guest("A")
    assert cancelled == []
    run.guest = "A"
    k.forget_guest("A")
    assert cancelled == [1]


def test_forgetting_a_guest_with_no_cell_run_yet_cancels_nothing(k):
    assert k.forget_guest("A") == {"forgotten": False}


def test_a_second_cell_of_the_same_notebook_is_told_one_is_running(k):
    k._current = N._Run(1)
    with pytest.raises(RuntimeError, match="^A cell is already running"):
        k.check_free()


def test_a_control_changed_while_a_handler_runs_is_refused(k):
    k._handler_alive = lambda: True
    with pytest.raises(RuntimeError, match="The last change is still running"):
        k.ipywidget_event("m", {})


# ---- magics


def test_timeit_refuses_zero_loops(k):
    with pytest.raises(ValueError, match="need a number of 1 or more"):
        k._timeit("-n 0", "1", {})


def test_writefile_says_writing_then_overwriting_then_appending(k, tmp_path):
    p = tmp_path / "h.py"
    assert _texts(k.run(f"%%writefile {p}\nx = 1"))[0].startswith("Writing ")
    assert _texts(k.run(f"%%writefile {p}\nx = 2"))[0].startswith("Overwriting ")
    assert _texts(k.run(f"%%writefile -a {p}\nx = 3"))[0].startswith("Appending to ")


def test_env_hides_secret_looking_names_only(monkeypatch):
    monkeypatch.setenv("KF_MUT_TOKEN", "abc")
    monkeypatch.setenv("KF_MUT_PLAIN", "1")
    listed = N.Kernel._env("")
    assert listed["KF_MUT_TOKEN"] == "hidden" and listed["KF_MUT_PLAIN"] == "1"


def test_env_refuses_the_apps_own_settings_and_sets_others(monkeypatch):
    monkeypatch.setenv("KF_MUT_SET", "0")
    with pytest.raises(ValueError, match="KratosForge's own setting"):
        N.Kernel._env("FABRIC_DESKTOP_MUT=1")
    N.Kernel._env("KF_MUT_SET=5")
    import os
    assert os.environ["KF_MUT_SET"] == "5"


def test_sx_returns_the_lines_and_sh_prints_them(k):
    assert k._magic("sx", "echo hi") == ["hi"]
    assert _texts(k.run("%sh echo hi")) == ["hi\n"]
    assert _texts(k.run("x = %sx echo hi\nlen(x)")) == ["1"]


def test_pip_install_in_a_guest_notebook_is_refused(k):
    k._local.guest = "Other"
    with pytest.raises(ValueError, match="shares another notebook's session"):
        k._magic("pip", "install nothing-here")


def test_autoreload_takes_its_mode(k):
    assert k._magic("autoreload", "2") is None
    assert k._reloader() is not None


def test_run_c_inside_a_notebook_run_is_refused(k):
    k._local.depth = 1
    with pytest.raises(NotImplementedError, match="isn't supported on this PC yet"):
        k._run_builtin("-c", "helper.py")


def test_arguments_are_set_first_in_a_notebook_without_a_parameters_cell(k):
    k.load_notebook = lambda name: [{"type": "code", "source": "y = x * 2", "meta": {}}]
    ns = k._base_namespace()
    k._run_cells("child", ns, {"x": 5}, None)
    assert ns["y"] == 10


def test_an_empty_quoted_run_name_is_no_name():
    with pytest.raises(ValueError, match="needs a notebook name"):
        N._run_target('"" {"a": 1}')


def test_configure_takes_force_and_refuses_a_list(monkeypatch):
    assert N.configure_report('-f {"driverMemory": "28g"}').startswith("%%configure checked")
    with pytest.raises(ValueError, match="takes a JSON object of settings"):
        N.configure_report("[1]")


def test_configure_says_when_the_same_lakehouse_is_attached(monkeypatch):
    monkeypatch.setattr(lakehouse, "name", lambda: "Sales")
    assert "- defaultLakehouse: Sales (the same one is attached here)" in N.configure_report(
        '{"defaultLakehouse": {"name": "Sales"}}')
    assert "attach Other with Add data items" in N.configure_report('{"defaultLakehouse": {"name": "Other"}}')


def test_fs_needs_a_command_and_rm_needs_a_path():
    with pytest.raises(ValueError, match="%fs needs a command"):
        N._fs_magic(None, "")
    with pytest.raises(ValueError, match=r"%fs rm needs 1 path\."):
        N._fs_magic(None, "rm")


def test_fs_blocker_lets_mounts_run_and_names_an_unknown_command():
    assert N.fs_magic_blocker("mounts") is None
    assert N.fs_magic_blocker("frobnicate x") == ("runs %fs frobnicate, which this PC doesn't have (it has ls, "
                                                  "head, cp, mv, rm, mkdirs and put)")


# ---- the Variables pane, completion and hover


def test_a_missing_variable_is_missing(k):
    assert k.variable_detail("nope") == {"name": "nope", "missing": True}


def test_a_sets_items_are_sorted(k):
    k.namespace["s"] = {"b", "c", "a"}
    assert [i["value"] for i in k.variable_detail("s")["items"]] == ["'a'", "'b'", "'c'"]


def test_a_secret_read_as_bytes_is_hidden(k):
    k._secrets.add("s3cr3t-value")  # pragma: allowlist secret (test value)
    assert k._hidden("plain", b"s3cr3t-value") is True  # pragma: allowlist secret (test value)
    assert k._hidden("plain", "s3cr3t-value") is True  # pragma: allowlist secret (test value)
    assert k._hidden("plain", "other") is False


def test_completion_names_a_function_and_a_class(k):
    k.run("def my_fn(): pass\nclass MyCls: pass")
    items = {i["label"]: i["type"] for i in k.complete("My")["items"]} | {
        i["label"]: i["type"] for i in k.complete("my_")["items"]}
    assert items["my_fn"] == "function" and items["MyCls"] == "class"


def test_hover_reads_the_name_at_the_cursor_from_the_namespace(k):
    k.namespace["x"] = 41
    assert k.inspect("x + len", 1) == {"name": "x", "kind": "variable", "type": "int", "value": "41", "from": 0, "to": 1}
    assert k.inspect("len", 1)["to"] == 3


def test_a_calls_signature_is_given(k):
    assert k.inspect("len(", call=True)["name"] == "len"


# ---- builtin/ paths, folders and files


def test_a_builtin_path_with_a_method_on_it_is_rewritten():
    tree = N._BuiltinPaths("/res").visit(ast.parse('p = "builtin/x.csv".strip()'))
    text = ast.unparse(tree)
    assert text != "p = 'builtin/x.csv'.strip()" and text.startswith("p = '") and text.endswith("x.csv'.strip()")


def test_a_name_too_many_folders_deep_says_how_many():
    with pytest.raises(ValueError, match=r"is in 4\.$"):
        N.check_notebook_name("a/b/c/d/n")


def test_a_cells_chart_and_its_other_charts_are_kept_apart():
    chart = {"kind": "bar", "x": "a"}
    assert N._cell_metadata({"chart": chart})["fabric_desktop"] == {"chart": chart}
    assert N._cell_metadata({"charts": [chart]})["fabric_desktop"] == {"charts": [chart]}


def test_settings_that_arent_settings_are_dropped():
    assert N._charts_meta("x") == []
    assert N._cell_meta("x") == {}


def test_a_new_notebook_takes_the_next_free_number(tmp_path):
    store = N.NotebookStore(tmp_path)
    got = {}
    worker = threading.Thread(target=lambda: got.update(a=store.new(), b=store.new()), daemon=True)
    worker.start()
    worker.join(30)
    assert not worker.is_alive(), "new() didn't finish"
    assert got["a"]["name"] == "Notebook 1" and got["b"]["name"] == "Notebook 2"

