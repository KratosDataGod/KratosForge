"""Notebooks at their exact edges (boundary re-score of batch 7, mutation batch 156,
docs/quality/mutation-testing.md): the rich output cap, display ids kept, and folder depth."""

from __future__ import annotations

import io
import json
import os
import threading
import types

import duckdb
import polars as pl
import pyarrow as pa
import pytest

from fabric_desktop import notebook as N


def test_an_update_that_lands_exactly_on_the_rich_cap_is_shown():
    old = {"type": "html", "display_id": "d", "html": "<b>1</b>"}
    new = {"type": "html", "display_id": "d", "html": "<b>2</b>"}
    size = len(json.dumps(new))
    rich = N.RICH_CHARS - size + len(json.dumps(old))
    total, shown = N._replace_capped([old], [0], new, rich)
    assert shown is new and total == N.RICH_CHARS
    total, shown = N._replace_capped([old], [0], new, rich + 1)
    assert shown["type"] == "text" and shown["text"].startswith("Output truncated")


def test_exactly_the_most_display_ids_are_kept():
    k = N.Kernel()
    for i in range(N.MAX_DISPLAY_IDS):
        k._keep_display_sink(str(i), object())
    assert len(k._display_sinks) == N.MAX_DISPLAY_IDS and next(iter(k._display_sinks)) == "0"
    k._keep_display_sink("one more", object())
    assert len(k._display_sinks) == N.MAX_DISPLAY_IDS and next(iter(k._display_sinks)) == "1"


def test_a_notebook_exactly_the_most_folders_deep_is_allowed():
    assert N.check_notebook_name("a/b/c/n") == "a/b/c/n" and N.MAX_FOLDER_DEPTH == 3
    with pytest.raises(ValueError, match="3 levels deep at most"):
        N.check_notebook_name("a/b/c/d/n")


def test_a_notebook_run_one_short_of_the_most_nested_runs_and_one_at_it_is_stopped():
    k = N.Kernel()
    k.load_notebook = lambda name: None
    k._run_cells = lambda name, namespace, arguments, cell_timeout: "ran"
    k._local.depth = N.MAX_RUN_DEPTH - 1
    assert k._run_notebook_in("child", {}, {}) == "ran" and k._local.depth == N.MAX_RUN_DEPTH - 1
    k._local.depth = N.MAX_RUN_DEPTH
    with pytest.raises(RecursionError, match=f"stops at {N.MAX_RUN_DEPTH}"):
        k._run_notebook_in("child", {}, {})


def test_spill_text_that_exactly_fills_the_room_keeps_the_file_open():
    class Held(io.StringIO):
        def close(self):  # keep the text readable
            self.closed_by_run = True

    run = N._Run(1)
    held = Held()
    run.spill_file, run.spill_chars = held, N.SPILL_CHARS - 3
    run._spill("abc")
    assert run.spill_file is held and held.getvalue() == "abc" and run.spill_chars == N.SPILL_CHARS
    run._spill("d")
    assert run.spill_file is None and held.closed_by_run and "stopped keeping" in held.getvalue()


def test_a_spill_file_written_exactly_at_the_cutoff_is_kept(monkeypatch, tmp_path):
    at = 1_800_000_000
    monkeypatch.setattr(N.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(N, "time", types.SimpleNamespace(time=lambda: float(at + 3600)))
    edge, old = tmp_path / f"{N.SPILL_PREFIX}edge.txt", tmp_path / f"{N.SPILL_PREFIX}old.txt"
    folder = tmp_path / f"{N.SPILL_DIR_PREFIX}edge"
    for path in (edge, old):
        path.write_text("x")
    folder.mkdir()
    os.utime(edge, (at, at))
    os.utime(folder, (at, at))
    os.utime(old, (at - 1, at - 1))
    N.sweep_spills(3600)
    assert edge.exists() and folder.exists() and not old.exists()


def test_a_duckdb_result_of_exactly_the_preview_rows_has_a_known_total():
    rel = duckdb.sql(f"SELECT * FROM range({N.PREVIEW_ROWS})")
    assert N._total_rows(rel, pa.table({"a": list(range(N.PREVIEW_ROWS))})) == N.PREVIEW_ROWS
    assert N._total_rows(rel, pa.table({"a": list(range(N.PREVIEW_ROWS + 1))})) is None


def test_a_poll_with_no_wait_answers_at_once_even_when_the_clock_hasnt_moved(monkeypatch):
    k = N.Kernel()
    run = N._Run(1)
    k._current = run
    reads = []

    def monotonic():
        reads.append(1)
        if len(reads) > 10:  # a poll still waiting would spin here; let it go
            run.done.set()
        return 5.0
    monkeypatch.setattr(N, "time", types.SimpleNamespace(monotonic=monotonic))
    assert k.poll(known_version=run.version, wait=0.0)["status"] == "running"
    assert len(reads) <= 3  # the deadline, one look at the clock, and the snapshot's time


def test_timeit_with_one_run_has_no_spread(capsys):
    N.Kernel()._timeit("-n 1 -r 1", "pass", {})
    out = capsys.readouterr().out
    assert "± 0 ns per loop" in out and "1 run, 1 loop each" in out


def test_a_duration_of_exactly_one_unit_is_shown_in_that_unit():
    assert N._duration(1.0) == "1 s" and N._duration(1e-3) == "1 ms" and N._duration(1e-6) == "1 µs"


def test_a_rich_output_that_exactly_fills_the_cap_is_shown():
    run = N._Run(1)
    html = {"type": "html", "html": "<b>x</b>"}
    run.rich_chars = N.RICH_CHARS - len(json.dumps(html))
    run.add(html)
    assert run.rich_chars == N.RICH_CHARS and not run.rich_dropped and run.outputs[-1]["html"] == "<b>x</b>"


def test_an_error_chain_keeps_exactly_20():
    first = cur = ValueError("first")
    for i in range(30):
        cur.__cause__ = cur = ValueError(str(i))
    assert len(N._chain(first)) == 20


def test_counts_at_exactly_a_billion_a_million_and_ten_thousand():
    assert (N._count(1_000_000_000), N._count(1_000_000), N._count(10_000)) == ("1B", "1M", "10K")


def test_previews_at_their_edges():
    assert N._preview(pl.DataFrame({c: [1] for c in "abcdef"})) == "a, b, c, d, e, f"  # six columns: no "..."
    hundred = N._preview(list(range(N._PREVIEW_ITEMS)))  # exactly the most items: repr of them all, cut to 80
    assert hundred.startswith("[0, 1, 2") and "20, 21" in hundred and len(hundred) == 80
    text = N._preview("x" * 78)  # its repr is exactly 80 characters
    assert text == repr("x" * 78) and len(text) == 80
    # 20 items whose text is exactly 76 characters with "[" and ", ...": shown whole.
    many = [1] * 8 + [10] * 12 + [0] * 81
    assert N._preview(many) == "[" + ", ".join(map(str, many[:20])) + ", ...]"


def test_a_watch_of_exactly_200_characters_is_taken():
    assert N.check_watches(["w" * 200]) == ["w" * 200]
    with pytest.raises(ValueError, match="up to 200"):
        N.check_watches(["w" * 201])


def test_a_table_whose_total_isnt_known_shows_exactly_the_preview_rows():
    assert N._total_rows(object(), pa.table({"a": list(range(N.PREVIEW_ROWS))})) == N.PREVIEW_ROWS


def test_breakpoints_are_lines_1_to_a_million():
    assert N.breakpoints_meta([0, 1, 1_000_000, 1_000_001]) == [1, 1_000_000]


def test_an_attached_image_of_exactly_the_most_characters_is_drawn():
    data = "A" * N.ATTACHMENT_IMAGE_MAX
    assert N._attachment_images({"i.png": {"image/png": data}}, "![x](attachment:i.png)") == \
        {"i.png": {"image/png": data}}


def test_a_file_with_a_del_character_and_escapes_keeps_escaping():
    assert N.dumps_like({"a": "é"}, '{"a": "\\u00e9\x7f"}'.encode()) == '{"a":"\\u00e9"}'


def test_fs_head_with_only_a_path_reads_64_kb(capsys):
    class FS:
        def head(self, path, n):
            return f"{path}:{n}"
    assert N._fs_magic(FS(), "head Files/p") is None
    assert capsys.readouterr().out.strip() == "Files/p:65536"


def test_writefile_with_one_name_writes_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert N.Kernel().run("%%writefile x.txt\nhello")["status"] == "ok"
    assert (tmp_path / "x.txt").read_text().strip() == "hello"


def test_fs_says_how_many_paths_a_command_needs():
    with pytest.raises(ValueError, match=r"%fs head needs 1 path\.$"):
        N._fs_magic(object(), "head")
    with pytest.raises(ValueError, match=r"%fs cp needs 2 paths\.$"):
        N._fs_magic(object(), "cp Files/a")


def test_an_interrupt_stops_waiting_at_exactly_its_deadline(monkeypatch):
    k = N.Kernel()
    run = N._Run(1)
    run.thread = threading.current_thread()  # not armed, so nothing is sent to it
    k._current = run
    reads = []

    def monotonic():
        reads.append(1)
        if len(reads) > 20:  # an interrupt still waiting would spin here; let it go
            run.done.set()
        return 0.0 if len(reads) == 1 else 2.0  # the deadline is set at 0, then the clock reads it exactly
    monkeypatch.setattr(N, "time", types.SimpleNamespace(monotonic=monotonic))
    assert k.interrupt() == {"interrupted": True, "stopped": False}
    assert len(reads) < 20
