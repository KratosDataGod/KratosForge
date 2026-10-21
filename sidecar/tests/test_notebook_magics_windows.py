"""Magics with what Windows brings: cells saved with \\r\\n, backslash paths, names with spaces or
accents, and files saved with a byte order mark (Notepad)."""

from pathlib import Path

import pytest

from fabric_desktop import history, magics, pip_magic
from fabric_desktop.notebook import Kernel
from fabric_desktop.notebook_resources import Resources


def _text(out):
    assert out["status"] == "ok", out
    return "".join(o.get("text", "") for o in out["outputs"])


def test_a_magic_in_a_crlf_cell_gets_its_argument_without_the_carriage_return():
    assert magics.rewrite("x = 1\r\n%env FD_WIN_TEST=on\r\ny = %sql SELECT 1\r\n") == \
        "x = 1\r\n__fd_magic__('env', 'FD_WIN_TEST=on')\ny = __fd_magic__('sql', 'SELECT 1')\n"
    k = Kernel()
    k.run("%env FD_WIN_TEST=on\r\n")
    assert _text(k.run("import os\r\nos.environ['FD_WIN_TEST']")) == "'on'"


def test_a_backslash_continuation_in_a_crlf_cell_isnt_read_as_a_magic():
    code = "x = 1 + \\\r\n%time\r\n"
    assert magics.find(code) == []


def test_words_keep_windows_backslashes_and_quoted_spaces():
    assert magics.words(r'install -r "C:\Users\Chris Smith\req.txt" polars') == \
        ["install", "-r", r"C:\Users\Chris Smith\req.txt", "polars"]
    assert magics.words(r"ls C:\data\raw") == ["ls", r"C:\data\raw"]


def test_words_follow_the_windows_command_line_for_quotes_inside_quotes():
    assert magics.words(r'put Files/x.txt "say \"hi\""') == ["put", "Files/x.txt", 'say "hi"']
    assert magics.words(r'ls "C:\My Folder\" Files') == ["ls", "C:\\My Folder\\", "Files"]
    assert magics.words(r"put Files/x.txt 'a \"b'") == ["put", "Files/x.txt", r'a \"b']
    assert magics.words(r"ls \\server\share") == ["ls", r"\\server\share"]
    assert magics.words(r'cp "Files/a b.txt" Files/c.txt') == ["cp", "Files/a b.txt", "Files/c.txt"]
    assert magics.words('x "" y') == ["x", "", "y"]
    with pytest.raises(ValueError, match="closing quotation"):
        magics.words('put Files/x.txt "open')


def test_fs_put_writes_escaped_quotes(tmp_path):
    k = Kernel()
    target = tmp_path / "x.txt"
    _text(k.run(f'%fs put -f "{target}" "say \\"hi\\""'))
    assert target.read_text() == 'say "hi"'


def test_pip_reads_a_requirements_file_with_a_bom_crlf_and_a_space_in_its_path(tmp_path):
    folder = tmp_path / "Chris Smith"
    folder.mkdir()
    req = folder / "réq.txt"
    req.write_bytes("\ufeffpolars==1.0\r\n# pinned\r\n\r\nrequests>=2\r\n".encode("utf-8"))
    calls = []

    def engine(name, *args):
        calls.append((name, args))
        return {"id": "j", "state": "done", "environment": "N"}
    pip_magic.run(f'install -r "{req}"', engine, lambda p: pip_magic.read_text(p, ""))
    assert calls[0] == ("install_packages", (["polars==1.0", "requests>=2"],))


def test_writefile_takes_a_quoted_path_with_spaces_and_writes_one_line_ending(tmp_path):
    target = tmp_path / "my notes" / "été.py"
    k = Kernel()
    _text(k.run(f'%%writefile "{target}"\r\nx = 1\r\ny = 2\r\n'))
    assert target.read_bytes().replace(b"\r\n", b"\n") == b"x = 1\ny = 2\n"
    assert b"\r\r" not in target.read_bytes()


def test_run_b_runs_a_bom_file_with_a_space_and_accent_in_its_name(tmp_path):
    folder = tmp_path / "N.resources"
    Resources(folder).add("mes outils.py", "\ufeffvaleur = 'été'\r\n".encode("utf-8"))
    k = Kernel()
    k.resources_dir = str(folder)
    assert _text(k.run('%run -b "mes outils.py"\r\nprint(valeur)')).strip() == "été"


def test_history_shows_a_crlf_cell_without_carriage_returns():
    ns = {"In": [""], "Out": {}}
    history.record_input(ns, 1, "a = 1\r\nb = 2\r\n")
    history.record_input(ns, 2, "%history -n")
    assert history.command(ns, "-n") == "  1: a = 1\n     b = 2"


def test_autoreload_reloads_a_module_in_a_folder_with_spaces_and_accents(tmp_path, monkeypatch):
    import os
    import sys
    import time
    folder = tmp_path / "Données partagées"
    folder.mkdir()
    monkeypatch.syspath_prepend(str(folder))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    mod = folder / "fd_win_mod.py"
    mod.write_text("VALUE = 1\n", encoding="utf-8")
    try:
        k = Kernel()
        assert _text(k.run("%autoreload 2\nimport fd_win_mod\nprint(fd_win_mod.VALUE)")).strip() == "1"
        mod.write_text("VALUE = 2\n", encoding="utf-8")
        later = time.time() + 2
        os.utime(mod, (later, later))
        assert _text(k.run("print(fd_win_mod.VALUE)")).strip() == "2"
    finally:
        sys.modules.pop("fd_win_mod", None)
