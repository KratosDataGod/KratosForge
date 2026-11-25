"""Notebooks (notebook.py): the remaining edges. %%configure, %fs and %run arguments read on their own;
cell ids, cell metadata, saved files and their JSON layout; the store's rename and extra keys; and kernel
paths reached through ordinary cells: completion and hover on names that aren't there, %%writefile that
can't write, update_display without an id. All on this PC, in tmp folders."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import notebook as nb_mod
from fabric_desktop.notebook import Kernel, NotebookStore


# ---- read on their own


def test_configure_must_be_a_json_object_and_names_other_settings_as_given():
    with pytest.raises(ValueError, match="^%%configure takes a JSON object of settings.$"):
        nb_mod.configure_report("[1, 2]")
    report = nb_mod.configure_report('{"sessionTimeoutInSeconds": 1200, "conf": {}}')
    assert "- sessionTimeoutInSeconds: 1200" in report.splitlines()
    assert "- conf: none (Spark settings" in report


def test_fs_lines_with_a_broken_quote_or_nothing_are_left_to_the_kernel():
    assert nb_mod.fs_magic_blocker('ls "Files/raw') is None
    assert nb_mod.fs_magic_blocker("") is None
    with pytest.raises(ValueError, match="^%fs needs a command: ls, head, cp, mv, rm, mkdirs or put.$"):
        nb_mod._fs_magic(object(), "")


def test_run_arguments_must_be_an_object():
    with pytest.raises(ValueError, match=r"^%run arguments are a \{name: value\} object.$"):
        nb_mod._run_target("helpers {1, 2}")


def test_a_call_name_is_empty_for_anything_but_a_call():
    import ast
    assert nb_mod._BuiltinPaths._call_name(None) == ""
    assert nb_mod._BuiltinPaths._call_name(ast.parse("x").body[0].value) == ""
    assert nb_mod._BuiltinPaths._call_name(ast.parse("pl.read_csv(1)").body[0].value) == "read_csv"


def test_a_table_with_columns_previews_its_names_and_a_frame_without_them_its_repr():
    import polars as pl
    assert nb_mod._preview(pl.DataFrame({c: [1] for c in "abcdefgh"})) == "a, b, c, d, e, f, ..."


# ---- cells and saved files


def test_a_made_id_that_repeats_one_already_given_is_made_again():
    cells = [{"cell_id": "keep-1"}, {"cell_id": "bad id!"}]
    ids = nb_mod.assign_cell_ids(cells, make=lambda i, c: "keep-1")
    assert ids[0] == "keep-1" and ids[1] != "keep-1" and cells[1]["cell_id"] == ids[1]


def test_cell_settings_the_file_had_on_are_taken_from_the_app():
    before = {"editable": False, "deletable": True, "jupyter": {"source_hidden": True}, "other": 1}
    merged = nb_mod._merge_cell_metadata({}, before)
    assert merged == {"deletable": True, "other": 1}


def code_cell(source, cid):
    return {"cell_type": "code", "id": cid, "metadata": {}, "source": [source], "outputs": [], "execution_count": None}


def test_a_newer_format_version_in_the_file_is_kept():
    new = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": []}
    out = nb_mod.merge_saved(new, {"nbformat": 4, "nbformat_minor": 9, "metadata": {}, "cells": []})
    assert out["nbformat_minor"] == 9


def test_a_cell_in_the_file_that_isnt_an_object_means_it_changed():
    nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [code_cell("x", "a")]}
    assert nb_mod.unchanged(nb, nb | {"cells": ["not a cell"]}) is False
    assert nb_mod.unchanged(nb, json.loads(json.dumps(nb))) is True


def test_a_file_that_isnt_json_is_rewritten_in_its_layout_without_sorting():
    nb = {"b": 1, "a": 2}
    raw = b'{\n  "b": 1,\n  oops\n'
    assert nb_mod.dumps_like(nb, raw) == '{\n  "b": 1,\n  "a": 2\n}\n'


def test_a_sorted_file_without_cells_is_written_sorted():
    assert nb_mod.dumps_like({"b": 1, "a": 2}, b'{\n "a": 0,\n "b": 0\n}') == '{\n "a": 2,\n "b": 1\n}'


# ---- the store


def test_extra_keys_the_notebook_saves_itself_cant_be_set_alone(tmp_path):
    store = NotebookStore(tmp_path)
    store.new()
    with pytest.raises(ValueError, match="^lakehouse is saved with the notebook, not set on its own.$"):
        store.set_extra("Notebook 1", "lakehouse", {"name": "LH"})


def test_renaming_onto_a_notebook_that_exists_is_refused(tmp_path):
    store = NotebookStore(tmp_path)
    store.new()
    store.new()
    with pytest.raises(ValueError, match="^A notebook named 'Notebook 2' already exists.$"):
        store.rename("Notebook 1", "Notebook 2")
    assert {n["name"] for n in store.list()} == {"Notebook 1", "Notebook 2"}


def test_a_failed_rename_puts_the_resources_folder_back(tmp_path, monkeypatch):
    store = NotebookStore(tmp_path)
    store.new()
    res = store.resources("Notebook 1")
    res.mkdir()
    (res / "a.txt").write_text("x", encoding="utf-8")
    real = type(tmp_path).rename

    def refuse(self, target):
        if self.suffix == ".ipynb":
            raise OSError("in use")
        return real(self, target)
    monkeypatch.setattr(type(tmp_path), "rename", refuse)
    with pytest.raises(OSError, match="in use"):
        store.rename("Notebook 1", "Sales")
    monkeypatch.undo()
    assert (res / "a.txt").exists() and not store.resources("Sales").exists()


# ---- the kernel


def test_completion_and_hover_on_names_that_arent_there_offer_nothing():
    k = Kernel()
    k.run("class P:\n    @property\n    def v(self):\n        raise RuntimeError('never run')\np = P()\nimport os")
    assert k.complete("1 + ") == {"from": 4, "items": []}
    assert k.complete("nothing_here.va") == {"from": 13, "items": []}
    assert k.complete("p.v.re") == {"from": 4, "items": []}  # a property isn't run to look inside
    assert k.complete("os.path.nope_") == {"from": 8, "items": []}
    assert k.inspect("nothing_here.x") is None
    assert k.inspect("   ", 1) is None
    assert k.inspect("nothing_here(", call=True) is None


def test_writefile_with_a_broken_quote_or_a_folder_it_cant_write_is_an_error(tmp_path):
    k = Kernel()
    out = k.run('%%writefile "unclosed\nx = 1')
    assert out["status"] == "error" and out["outputs"][-1]["ename"] == "ValueError"
    (tmp_path / "file").write_text("x", encoding="utf-8")
    out = k.run(f'%%writefile "{(tmp_path / "file" / "inside.py").as_posix()}"\nx = 1')
    assert out["status"] == "error" and out["outputs"][-1]["ename"] in ("FileExistsError", "NotADirectoryError")


def test_an_update_needs_a_display_id_and_a_display_outside_a_cell_is_printed(capsys):
    k = Kernel()
    out = k.run("display(1, update=True)")
    assert out["status"] == "error" and out["outputs"][-1]["evalue"] == "update_display needs a display_id."
    k._display(41)
    assert capsys.readouterr().out == "41\n"


def test_a_type_shows_as_its_repr_and_a_bundle_of_unknown_views_too():
    assert nb_mod.display(int) == {"type": "text", "text": "<class 'int'>"}

    class Odd:
        def _repr_mimebundle_(self, include=None, exclude=None):
            return {"application/x-unknown": "?"}

        def __repr__(self):
            return "Odd()"
    assert nb_mod.display(Odd()) == {"type": "text", "text": "Odd()"}


def test_a_matplotlib_figure_shows_as_a_png_and_is_closed():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    ax.plot([1, 2])
    out = nb_mod.display(ax)  # an axes shows its figure
    assert out["type"] == "image" and out["mime"] == "image/png" and out["data"]
    assert not plt.fignum_exists(fig.number)


def test_a_paused_place_is_kept_as_json_and_counts_as_news():
    run = nb_mod._Run(1)
    run.set_paused({"line": 3, "at": nb_mod._MISSING})
    assert run.paused["line"] == 3 and isinstance(run.paused["at"], str) and run.version == 1
    run.set_paused(None)
    assert run.paused is None and run.version == 2


def test_a_cell_another_notebook_runs_in_a_shared_session_is_named():
    k = Kernel()
    run = nb_mod._Run(1)
    run.guest, run.notebook = "Sales", "Sales"
    k._current = run
    with pytest.raises(RuntimeError, match="^Sales is running a cell in this shared session."):
        k.check_free()
    assert k.forget_guest("Sales") == {"forgotten": False}


def test_a_guest_can_t_install_packages_into_the_host_s_environment():
    k = Kernel()
    with k.as_guest("Sales"):
        with pytest.raises(ValueError, match="^Sales shares another notebook's session"):
            k._magic("pip", "install requests")


def test_a_proof_must_be_running_to_finish_and_paused_locals_dont_open():
    k = Kernel()
    k._proof = None
    with pytest.raises(RuntimeError, match="^No proof is running in this session.$"):
        k.finish_proof([])
    assert k._frame_row("items", [1, 2])["expandable"] is False


def test_run_b_and_other_notebooks_refuse_what_this_pc_cant_do(tmp_path):
    k = Kernel()
    k._local.depth = 1
    with pytest.raises(NotImplementedError, match="^%run -c inside a notebook that another one runs"):
        k._run_builtin("-c ", "helpers.py")
    k._local.depth = 0
    with pytest.raises(RuntimeError, match="^Other notebooks can't be opened here.$"):
        k._run_notebook_in("Other", {}, {})
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    (builtin / "q.sql").write_text("SELECT * FROM no_such_table_here", encoding="utf-8")
    k.resources_dir = str(tmp_path)
    with pytest.raises(ValueError, match="^%run -b with a .sql file takes no arguments.$"):
        k._run_builtin("-b ", 'q.sql {"a": 1}')
    with pytest.raises(RuntimeError, match="^q.sql failed; its error is shown above.$"):
        k._run_builtin("-b ", "q.sql")


def test_captured_output_shown_outside_a_cell_prints_its_text_only(capsys):
    captured = nb_mod.CapturedOutput([{"type": "stream", "text": "hi\n"}, {"type": "text", "text": "2"},
                                      {"type": "error", "ename": "E"}], Kernel())
    captured.show()
    assert capsys.readouterr().out == "hi\n"
    assert repr(captured) == "<CapturedOutput: 2 outputs>"
    assert repr(nb_mod.CapturedOutput([{"type": "text", "text": "2"}], Kernel())) == "<CapturedOutput: 1 output>"


def test_names_an_object_lists_but_doesnt_have_are_offered_as_variables_and_not_looked_into():
    k = Kernel()
    k.run("class D:\n    def __dir__(self):\n        return ['ghost', 'real']\n    real = 1\nd = D()")
    assert k.complete("d.") == {"from": 2, "items": [{"label": "ghost", "type": "variable"},
                                                     {"label": "real", "type": "variable"}]}
    assert k.inspect("d.ghost") is None
    assert k.inspect("print", call=True) is None  # no open call


def test_a_series_previews_as_its_repr_and_a_missing_folder_has_no_notebooks(tmp_path):
    import polars as pl
    assert nb_mod._preview(pl.Series("s", [1])).startswith("shape: (1,)")
    assert nb_mod.notebook_files(tmp_path / "missing") == []


def test_notebook_keys_of_another_tool_in_the_new_metadata_are_written():
    new = {"nbformat": 4, "nbformat_minor": 5, "metadata": {"title": "Sales"}, "cells": []}
    out = nb_mod.merge_saved(new, {"metadata": {"kernelspec": {"name": "x"}}, "cells": []})
    assert out["metadata"] == {"kernelspec": {"name": "x"}, "title": "Sales"}


def test_a_chart_of_a_result_frame_comes_from_the_frames(monkeypatch):
    k = Kernel()
    monkeypatch.setattr(k.frames, "chart", lambda frame, **spec: {"frame": frame, **spec})
    assert k.frame_chart("f1", kind="bar") == {"frame": "f1", "kind": "bar"}


def test_a_folder_that_cant_be_listed_has_no_notebooks(tmp_path, monkeypatch):
    (tmp_path / "Sales").mkdir()
    (tmp_path / "Sales" / "a.ipynb").write_text("{}", encoding="utf-8")
    (tmp_path / "b.ipynb").write_text("{}", encoding="utf-8")
    real = type(tmp_path).iterdir

    def iterdir(self):
        if self.name == "Sales":
            raise PermissionError("access denied")
        return real(self)
    monkeypatch.setattr(type(tmp_path), "iterdir", iterdir)
    assert [name for name, _ in nb_mod.notebook_files(tmp_path)] == ["b"]


def test_output_with_no_cell_running_goes_to_the_fallback_or_nowhere():
    import io
    sink = io.StringIO()
    router = nb_mod._Router(sink)
    assert router.writable() and router.encoding == "utf-8"
    assert router.write("hi") == 2 and sink.getvalue() == "hi"
    assert nb_mod._Router(None).write("lost") == 4


def test_a_guest_polling_sees_its_own_last_cell_while_the_host_runs_one():
    k = Kernel()
    with k.as_guest("Sales"):
        k.run("1 + 1")
    host = nb_mod._Run(9)
    k._current = host
    with k.as_guest("Sales"):
        got = k.poll()
    assert got["session_running"] is True and got["outputs"] == [{"type": "text", "text": "2"}]


def test_run_c_at_the_top_reads_the_notebook_s_resources():
    with pytest.raises(FileNotFoundError, match="this notebook has no resources folder yet"):
        Kernel()._run_builtin("-c ", "helpers.py")
