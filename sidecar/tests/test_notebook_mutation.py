"""Mutation testing batch 7 (docs/quality/mutation-testing.md): notebook.py outside the kernel.

Each test pins a behavior a surviving mutant changed without any other test noticing: the error
and preview limits, a cell run's clear and snapshot, where printed text and outputs go, the %fs
magic, the Variables pane's sizes, columns, memory and previews, watches, IPython-style times,
listing nested notebooks, cell metadata kept on save, the saved compute and workspace, a delete's
resources folder and a move whose copy went away. The Kernel class and the debugger are left out."""

from __future__ import annotations

import io
import sys
import threading

import pandas as pd
import polars as pl
import pytest

from fabric_desktop import notebook as nb
from fabric_desktop.notebook import NotebookShelf, NotebookStore

CELLS = [{"type": "code", "source": "x = 1"}]


# Limits


def test_an_error_is_cut_only_past_error_chars():
    at = nb.capped_error({"type": "error", "evalue": "e" * nb.ERROR_CHARS, "traceback": "t" * nb.ERROR_CHARS})
    assert at["evalue"] == "e" * nb.ERROR_CHARS and at["traceback"] == "t" * nb.ERROR_CHARS
    past = nb.capped_error({"type": "error", "evalue": "e" * (nb.ERROR_CHARS + 1),
                            "traceback": ["t" * nb.ERROR_CHARS, "x"]})
    assert past["evalue"].startswith("e" * nb.ERROR_CHARS + "\n[KratosForge kept the first 200,000")
    assert past["traceback"].endswith("t" * (nb.ERROR_CHARS - 1) + "x")
    assert past["traceback"].startswith("[KratosForge kept the last 200,000 of this traceback's 200,001")


def test_a_lazy_polars_frame_and_a_pandas_frame_preview_one_row_past_the_limit():
    rows = nb.PREVIEW_ROWS + 500
    assert nb.as_table(pl.LazyFrame({"a": range(rows)})).num_rows == nb.PREVIEW_ROWS + 1
    assert nb.as_table(pd.DataFrame({"a": range(rows)})).num_rows == nb.PREVIEW_ROWS + 1


# A cell run's outputs


def test_clear_now_empties_the_outputs_and_clear_wait_keeps_them_until_the_next():
    run = nb._Run(1)
    run.add({"type": "text", "text": "first"})
    run.clear(wait=True)
    assert run.outputs == [{"type": "text", "text": "first"}]
    run.add({"type": "text", "text": "second"})
    assert run.outputs == [{"type": "text", "text": "second"}]
    run.clear()
    assert run.outputs == []


def test_a_snapshot_has_progress_and_onelake_only_when_there_are_some(monkeypatch):
    run = nb._Run(1)
    run.thread = threading.current_thread()
    monkeypatch.setattr(nb, "_query_progress", lambda ident: None)
    snap = run.snapshot()
    assert "progress" not in snap and "onelake" not in snap
    monkeypatch.setattr(nb, "_query_progress", lambda ident: 0.25)
    run.onelake.wait = 1.0
    snap = run.snapshot()
    assert snap["progress"] == 0.25 and snap["onelake"]["wait_seconds"] == 1.0


def test_query_progress_shows_only_while_the_cells_thread_is_in_spark_code(monkeypatch):
    class Spark:
        @staticmethod
        def progress():
            return 0.5
    monkeypatch.setitem(sys.modules, "fabric_desktop.spark_local", Spark)
    me = threading.get_ident()
    assert nb._query_progress(me) is None  # this test isn't Spark code
    assert nb._query_progress(None) is None
    scope = {"__name__": "duckdb.stand_in", "nb": nb, "me": me}
    exec("def in_spark():\n    return nb._query_progress(me)\n", scope)
    assert scope["in_spark"]() == 0.5


def test_an_output_widgets_sink_clears_now_unless_told_to_wait():
    class Out:
        def __init__(self):
            self.waits = []

        def clear_output(self, wait):
            self.waits.append(wait)
    out = Out()
    sink = nb._OutputSink(out)
    sink.clear()
    sink.clear(True)
    assert out.waits == [False, True]


def test_the_router_returns_the_length_written_and_no_cell_takes_an_output(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    router = nb._Router(io.StringIO())
    assert router.write("abc") == 3
    assert nb._to_running_cell({"type": "text", "text": "x"}) is False


# %fs


class FakeFs:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return [] if name == "ls" else True
        return call


def test_fs_magic_runs_the_command_its_first_word_names():
    fs = FakeFs()
    nb._fs_magic(fs, "ls Files/raw")
    nb._fs_magic(fs, "cp Files/a Files/b")
    nb._fs_magic(fs, "cp -r Files/a Files/c")
    assert fs.calls == [("ls", ("Files/raw",), {}), ("cp", ("Files/a", "Files/b"), {"recurse": False}),
                        ("cp", ("Files/a", "Files/c"), {"recurse": True})]


def test_fs_magic_checks_every_path_but_not_the_text_put_writes():
    fs = FakeFs()
    with pytest.raises(ValueError):
        nb._fs_magic(fs, "cp Files/a /mnt/b")  # Databricks storage, refused as in dbutils.fs
    assert fs.calls == []
    nb._fs_magic(fs, 'put Files/note.txt "/mnt/b"')  # only the path is a path
    assert fs.calls == [("put", ("Files/note.txt", "/mnt/b"), {"overwrite": False})]


# The Variables pane


def test_sizes_columns_and_previews_of_frames_and_other_values():
    frame = pd.DataFrame({"a": [1, 2]})
    assert nb._size(frame) == "2 × 1"
    assert nb._expandable([1]) and not nb._expandable([])
    assert nb._columns(pl.DataFrame({"a": [1]})) == [{"name": "a", "type": "Int64"}]

    class HasColumns:
        columns = ["not", "a", "frame"]

        def __repr__(self):
            return "HasColumns()"
    assert nb._preview(HasColumns()) == "HasColumns()"
    assert nb._preview("a" * 10) == repr("a" * 10)
    assert nb._preview("a" * 100).endswith("...") and len(nb._preview("a" * 100)) == 80


def test_memory_is_asked_only_of_frames_and_scaled_from_a_sample():
    class LooksLikeAFrame:
        def estimated_size(self):
            return 5

        def memory_usage(self, deep=False):
            return 5
    assert nb._memory(LooksLikeAFrame()) is None
    values = [1, 2, 3]
    assert nb._memory(values) == sys.getsizeof(values) + 3 * sys.getsizeof(1)
    mapping = {"k": "v" * 100}
    assert nb._memory(mapping) == sys.getsizeof(mapping) + sys.getsizeof("k") + sys.getsizeof("v" * 100)


def test_watches_are_capped_at_max_watches():
    assert nb.check_watches([f"x{i}" for i in range(nb.MAX_WATCHES)]) == [f"x{i}" for i in range(nb.MAX_WATCHES)]
    with pytest.raises(ValueError, match="up to 10 watches"):
        nb.check_watches([f"x{i}" for i in range(nb.MAX_WATCHES + 1)])


def test_times_print_the_way_ipython_does():
    assert nb._duration(2.5) == "2.5 s"
    assert nb._duration(0.0025) == "2.5 ms"
    assert nb._duration(2.5e-6) == "2.5 µs"
    assert nb._duration(1e-12) == "0.001 ns"


# Notebooks on disk


def test_notebooks_are_listed_down_to_the_deepest_folder_allowed(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "Deep.ipynb").write_text("{}")
    (deep / "d").mkdir()
    (deep / "d" / "TooDeep.ipynb").write_text("{}")
    assert [name for name, _ in nb.notebook_files(tmp_path)] == ["a/b/c/Deep"]


def test_a_store_lists_its_notebooks(tmp_path):
    store = NotebookStore(tmp_path)
    store.save("One", CELLS)
    assert [n["name"] for n in store.list()] == ["One"]


def test_saved_metadata_keeps_other_keys_of_the_apps_own_section():
    kept = nb._merge_cell_metadata({}, {"fabric_desktop": {"chart": {"kind": "bar"}, "note": "kept"}})
    assert kept == {"fabric_desktop": {"note": "kept"}}


def test_a_save_without_compute_or_workspace_keeps_what_the_file_has(tmp_path):
    store = NotebookStore(tmp_path)
    ws = {"workspace_id": "w1", "workspace_name": "Sales"}
    store.save("N", CELLS, compute="local", workspace=ws)
    store.save("N", CELLS, workspace={})
    assert store.load("N")["compute"] == "local"
    store.save("N", CELLS, compute="fabric", workspace=ws)
    store.save("N", CELLS, compute="local")
    assert store.load("N")["workspace"] == ws


def test_a_delete_names_the_resources_folder_it_keeps(tmp_path):
    store = NotebookStore(tmp_path)
    store.save("Plain", CELLS)
    assert store.delete("Plain") == {"resources_kept": None}
    store.save("WithFiles", CELLS)
    folder = nb.notebook_resources.folder_for(store.path("WithFiles"))
    folder.mkdir()
    assert store.delete("WithFiles") == {"resources_kept": str(folder)}


def test_a_move_whose_copy_went_away_says_the_notebook_is_still_where_it_was(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shelf = NotebookShelf(tmp_path / "app", places=lambda: [(str(repo), "repo", repo / "notebooks")])
    shelf.save("A", CELLS)
    src = shelf.path("A")
    real = nb.Path.read_bytes

    def vanish(self):
        if self != src and self.name == "A.ipynb":
            self.unlink()  # the copy is gone before it can be checked
            raise FileNotFoundError(str(self))
        return real(self)
    monkeypatch.setattr(nb.Path, "read_bytes", vanish)
    with pytest.raises(OSError, match="The notebook is still where it was"):
        shelf.move("A", shelf.places()[0]["root"])
    assert src.is_file()
