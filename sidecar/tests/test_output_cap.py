"""Per-cell output caps: printed text past STREAM_CHARS goes to a file, rich outputs past RICH_CHARS stop."""

import os
import re
import time
from pathlib import Path

from fabric_desktop import notebook
from fabric_desktop.notebook import Kernel, _Run


def test_printed_output_past_the_cap_is_truncated_and_kept_in_a_file(monkeypatch, tmp_path):
    monkeypatch.setattr(notebook, "STREAM_CHARS", 100)
    monkeypatch.setattr(notebook.tempfile, "tempdir", str(tmp_path))
    out = Kernel().run("for i in range(50):\n    print('line', i)\nprint('done')")
    assert out["status"] == "ok"
    stream, note = out["outputs"]
    assert stream["type"] == "stream" and len(stream["text"]) == 100 and stream["text"].startswith("line 0\n")
    assert note["type"] == "text" and note["text"].startswith("Output truncated: this cell printed more than 100 characters.")
    path = Path(re.search(r", is in (.+)\.$", note["text"]).group(1))
    full = "".join(f"line {i}\n" for i in range(50)) + "done\n"
    assert path.parent == tmp_path and path.read_text(encoding="utf-8") == full


def test_output_under_the_cap_is_untouched(monkeypatch):
    monkeypatch.setattr(notebook, "STREAM_CHARS", 100)
    out = Kernel().run("print('x' * 99)")
    assert out["outputs"] == [{"type": "stream", "text": "x" * 99 + "\n"}]


def test_when_the_file_cant_be_written_the_line_still_shows(monkeypatch):
    monkeypatch.setattr(notebook, "STREAM_CHARS", 10)

    def refuse(**_):
        raise OSError("disk full")
    monkeypatch.setattr(notebook.tempfile, "mkstemp", refuse)
    run = _Run(1)
    run.write("a" * 8)
    run.write("b" * 8)
    run.write("c" * 8)
    assert run.outputs == [{"type": "stream", "text": "a" * 8 + "bb"},
                           {"type": "text", "text": "Output truncated: this cell printed more than 10 characters. "
                                                    "The rest wasn't kept."}]


def test_rich_outputs_past_the_cap_stop_with_one_line(monkeypatch):
    monkeypatch.setattr(notebook, "RICH_CHARS", 2_000_000)
    run = _Run(1)
    big = {"type": "html", "html": "x" * 900_000}
    for _ in range(4):
        run.add(big)
    run.write("still printed")
    assert run.outputs[:2] == [big, big]
    assert run.outputs[2]["type"] == "text" and "passed 2 MB" in run.outputs[2]["text"]
    assert run.outputs[3] == {"type": "stream", "text": "still printed"}


def test_clearing_output_starts_the_count_again(monkeypatch, tmp_path):
    monkeypatch.setattr(notebook, "STREAM_CHARS", 10)
    monkeypatch.setattr(notebook.tempfile, "tempdir", str(tmp_path))
    run = _Run(1)
    run.write("a" * 20)
    run.clear()
    run.write("b" * 10)
    assert run.outputs == [{"type": "stream", "text": "b" * 10}]
    run.clear(wait=True)
    run.write("c" * 5)
    assert run.outputs == [{"type": "stream", "text": "c" * 5}]


def test_the_file_stops_at_its_own_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(notebook, "STREAM_CHARS", 10)
    monkeypatch.setattr(notebook, "SPILL_CHARS", 25)
    monkeypatch.setattr(notebook.tempfile, "tempdir", str(tmp_path))
    run = _Run(1)
    for _ in range(10):
        run.write("x" * 8)
    run.finish("ok")
    [path] = tmp_path.glob("kratosforge-output-*.txt")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("x" * 25 + "\n[KratosForge stopped keeping") and len(text) < 200


def test_an_error_shows_after_the_rich_cap(monkeypatch):
    monkeypatch.setattr(notebook, "RICH_CHARS", 1_000_000)
    out = Kernel().run("for i in range(3):\n    displayHTML('x' * 600_000)\nraise ValueError('the real problem')")
    assert out["status"] == "error"
    assert [o["type"] for o in out["outputs"]] == ["html", "text", "error"]
    assert out["outputs"][2]["evalue"] == "the real problem"


def test_restart_and_clear_output_delete_the_files(monkeypatch, tmp_path):
    monkeypatch.setattr(notebook, "STREAM_CHARS", 10)
    monkeypatch.setattr(notebook.tempfile, "tempdir", str(tmp_path))
    k = Kernel()
    k.run("print('a' * 20)")
    assert len(list(tmp_path.glob("kratosforge-output-*"))) == 1
    k.restart()
    assert not list(tmp_path.glob("kratosforge-output-*"))
    k.run("from IPython.display import clear_output\nprint('b' * 20)\nclear_output()\nprint('after')")
    assert not list(tmp_path.glob("kratosforge-output-*"))


def test_the_engine_sweeps_files_left_by_closed_sessions(monkeypatch, tmp_path):
    monkeypatch.setattr(notebook.tempfile, "tempdir", str(tmp_path))
    old, new = tmp_path / "kratosforge-output-old.txt", tmp_path / "kratosforge-output-new.txt"
    other = tmp_path / "someone-else.txt"
    for p in (old, new, other):
        p.write_text("x")
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    os.utime(other, (time.time() - 7200, time.time() - 7200))
    notebook.sweep_spills()
    assert not old.exists() and new.exists() and other.exists()


def test_the_sweep_empties_old_kernel_folders(monkeypatch, tmp_path):
    monkeypatch.setattr(notebook.tempfile, "tempdir", str(tmp_path))
    folder = tmp_path / "kratosforge-kernel-abc"
    folder.mkdir()
    left = folder / "kratosforge-output-x.txt"
    left.write_text("x")
    hour_ago = time.time() - 7200
    os.utime(left, (hour_ago, hour_ago))
    notebook.sweep_spills()
    assert not left.exists()
    os.utime(folder, (hour_ago, hour_ago))  # deleting the file touched the folder
    notebook.sweep_spills()
    assert not folder.exists()
