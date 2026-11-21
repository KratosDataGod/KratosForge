"""TPC-H benchmark notebook paths that mutation testing (batch 16, docs/quality/mutation-testing.md)
showed no test checked: the query numbers, the hidden queries cell, the .ipynb files write() makes
(names, nested folders, writing twice, the trailing newline and indent), and running the module
to write them. Nothing here generates TPC-H data or reaches a lakehouse."""

from __future__ import annotations

import json
import runpy
import sys

import pytest

from fabric_desktop import benchmark

FILES = ["tpch_data.ipynb", "tpch_benchmark.ipynb"]


@pytest.fixture(autouse=True)
def app_data(tmp_path, monkeypatch):
    """App data goes to this test's folder, never the real one."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def test_the_queries_are_numbered_1_to_22():
    assert sorted(benchmark.QUERIES) == list(range(1, 23))
    assert benchmark.QUERIES[1].startswith("SELECT\n    l_returnflag,")
    assert "cntrycode" in benchmark.QUERIES[22]


def test_the_queries_cell_is_hidden_and_the_others_are_not():
    cells = benchmark.notebooks()[benchmark.BENCHMARK_NOTEBOOK]
    hidden = [c for c in cells if c.get("meta", {}).get("source_hidden")]
    assert len(hidden) == 1 and hidden[0]["meta"]["source_hidden"] is True
    assert hidden[0]["source"].startswith("# The 22 TPC-H queries")
    numbers = [line.split(":")[0] for line in hidden[0]["source"].splitlines() if line.endswith(': """')]
    assert numbers == [str(n) for n in range(1, 23)]


def test_write_makes_both_notebooks_in_a_new_nested_folder(tmp_path):
    folder = tmp_path / "a" / "b"
    paths = benchmark.write(folder)
    assert paths == [folder / name for name in FILES]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert text.endswith("}\n") and not text.endswith("\n\n")
        assert text.startswith('{\n "')  # indent=1
        assert json.loads(text)["cells"]


def test_write_takes_a_string_and_writes_again_over_its_own_files(tmp_path):
    first = [p.read_text(encoding="utf-8") for p in benchmark.write(str(tmp_path))]
    second = benchmark.write(str(tmp_path))
    assert [p.read_text(encoding="utf-8") for p in second] == first
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".ipynb") == sorted(FILES)


@pytest.mark.filterwarnings("ignore:'fabric_desktop.benchmark' found in sys.modules")
def test_running_the_module_writes_to_the_folder_given(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["benchmark", str(tmp_path / "out")])
    runpy.run_module("fabric_desktop.benchmark", run_name="__main__")
    printed = capsys.readouterr().out.splitlines()
    assert printed == [str(tmp_path / "out" / name) for name in FILES]


@pytest.mark.filterwarnings("ignore:'fabric_desktop.benchmark' found in sys.modules")
def test_running_the_module_with_no_folder_writes_here(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["benchmark"])
    runpy.run_module("fabric_desktop.benchmark", run_name="__main__")
    assert capsys.readouterr().out.splitlines() == FILES
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".ipynb") == sorted(FILES)
