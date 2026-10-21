"""Per-cell output caps: printed text past STREAM_CHARS goes to a file, rich outputs past RICH_CHARS stop."""

import re
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
    path = Path(re.search(r"All of it is in (.+)\.$", note["text"]).group(1))
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
