"""A notebook's resources folder: files beside the notebook, read as builtin/... in its cells."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from fabric_desktop import notebook_resources
from fabric_desktop.notebook import Kernel, NotebookShelf, NotebookStore
from fabric_desktop.notebook_resources import MAX_FILE_BYTES, MAX_FILES, Resources, check_name
from fabric_desktop.service import Service


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.notebooks.save("Sales", [{"type": "code", "source": ""}])
    yield s
    s.kernel.shutdown() if hasattr(s.kernel, "shutdown") else None


def test_files_are_copied_in_listed_and_never_overwritten(svc):
    out = svc.notebook_resource_add("Sales", "regions.csv", b64(b"region\nEast\n"))
    assert out == {"added": True, "name": "regions.csv", "size": 12, "path": "builtin/regions.csv"}
    folder = svc.notebooks.folder / "Sales.resources" / "builtin"
    assert (folder / "regions.csv").read_bytes() == b"region\nEast\n"
    again = svc.notebook_resource_add("Sales", "regions.csv", b64(b"other"))
    assert again == {"added": False, "exists": True, "name": "regions.csv", "suggested": "regions (2).csv"}
    assert (folder / "regions.csv").read_bytes() == b"region\nEast\n"  # untouched
    listed = svc.notebook_resources("Sales")
    assert [f["name"] for f in listed["files"]] == ["regions.csv"]
    assert listed["files"][0]["size"] == 12 and listed["max_files"] == MAX_FILES


def test_rename_refuses_to_replace_and_delete_removes_one_file(svc):
    svc.notebook_resource_add("Sales", "a.csv", b64(b"a"))
    svc.notebook_resource_add("Sales", "b.csv", b64(b"b"))
    with pytest.raises(ValueError, match="already exists"):
        svc.notebook_resource_rename("Sales", "a.csv", "b.csv")
    assert svc.notebook_resource_rename("Sales", "a.csv", "c.csv") == {"name": "c.csv", "path": "builtin/c.csv"}
    svc.notebook_resource_delete("Sales", "b.csv")
    assert [f["name"] for f in svc.notebook_resources("Sales")["files"]] == ["c.csv"]
    with pytest.raises(FileNotFoundError):
        svc.notebook_resource_delete("Sales", "b.csv")


@pytest.mark.parametrize("bad", ["", "..", "../x.csv", "a/b.csv", "a\\b.csv", "CON", "nul.txt", "com1.csv",
                                 "x.", "a:b", "a*b"])
def test_unsafe_names_are_refused(bad):
    with pytest.raises(ValueError):
        check_name(bad)


def test_size_and_count_caps(tmp_path):
    res = Resources(tmp_path / "N.resources")
    with pytest.raises(ValueError, match="50 MB"):
        res.add("big.bin", b"x" * (MAX_FILE_BYTES + 1))
    for i in range(MAX_FILES):
        res.add(f"f{i}.txt", b"")
    with pytest.raises(ValueError, match="100 resource files"):
        res.add("one-more.txt", b"")


def test_a_missing_notebook_has_no_resources(svc):
    with pytest.raises(ValueError, match="no notebook"):
        svc.notebook_resource_add("Nope", "a.csv", b64(b"a"))


def test_rename_and_delete_of_the_notebook_take_its_folder(tmp_path):
    store = NotebookStore(tmp_path)
    store.save("A", [{"type": "code", "source": ""}])
    Resources(store.resources("A")).add("x.csv", b"1")
    store.rename("A", "B")
    assert (tmp_path / "B.resources" / "builtin" / "x.csv").read_bytes() == b"1"
    assert not (tmp_path / "A.resources").exists()

    # A delete never removes the files: the folder stays, and a new notebook of that name has them again.
    assert store.delete("B") == {"resources_kept": str(tmp_path / "B.resources")}
    assert not (tmp_path / "B.ipynb").exists()
    assert (tmp_path / "B.resources" / "builtin" / "x.csv").read_bytes() == b"1"
    store.save("B", [{"type": "code", "source": ""}])
    assert [f["name"] for f in Resources(store.resources("B")).list()["files"]] == ["x.csv"]
    store.save("C", [{"type": "code", "source": ""}])
    assert store.delete("C") == {"resources_kept": None}


def test_delete_notebook_keeps_the_resources(svc):
    svc.notebook_resource_add("Sales", "a.csv", b64(b"a"))
    out = svc.delete_notebook("Sales")
    folder = svc.notebooks.folder / "Sales.resources"
    assert out["deleted"] == "Sales" and out["resources_kept"] == str(folder)  # to Recently deleted
    assert (folder / "builtin" / "a.csv").read_bytes() == b"a"
    assert not (svc.notebooks.folder / "Sales.ipynb").exists()
    svc.notebook_restore(out["id"])  # restored under its name: the files are its own again
    assert [f["name"] for f in svc.notebook_resources("Sales")["files"]] == ["a.csv"]


def test_rename_stops_when_a_folder_of_the_new_name_is_there(tmp_path):
    shelf = NotebookShelf(tmp_path)
    shelf.save("A", [{"type": "code", "source": ""}])
    Resources(shelf.resources("A")).add("x.csv", b"1")
    (tmp_path / "B.resources").mkdir()
    with pytest.raises(ValueError, match="Nothing was renamed"):
        shelf.rename("A", "B")
    assert (tmp_path / "A.ipynb").is_file() and (tmp_path / "A.resources" / "builtin" / "x.csv").is_file()


def test_moving_a_notebook_moves_its_resources(tmp_path):
    repo = tmp_path / "repo"
    shelf = NotebookShelf(tmp_path / "app", places=lambda: [(str(repo), "repo", repo / "notebooks")])
    shelf.save("A", [{"type": "code", "source": ""}])
    Resources(shelf.resources("A")).add("x.csv", b"12")
    assert shelf.move("A", str(repo))["moved"]
    assert (repo / "notebooks" / "A.resources" / "builtin" / "x.csv").read_bytes() == b"12"
    assert not (tmp_path / "app" / "A.resources").exists()
    assert shelf.resources("A") == repo / "notebooks" / "A.resources"


def test_builtin_paths_in_a_cell_read_the_resources_folder(tmp_path):
    folder = tmp_path / "N.resources"
    Resources(folder).add("data.csv", b"a,b\n1,2\n")
    k = Kernel()
    k.resources_dir = str(folder)
    out = k.run('import polars as pl\nname = "data.csv"\n'
                'print(open("builtin/data.csv").read().splitlines()[0], pl.read_csv(f"builtin/{name}").height, '
                'notebookutils.nbResPath == r"' + str(folder) + '", [f.name for f in notebookutils.fs.ls("builtin/")])')
    assert out["status"] == "ok", out
    assert out["outputs"][0]["text"].strip() == "a,b 1 True ['data.csv']"
    k.resources_dir = ""
    out = k.run('print("builtin/data.csv")')
    assert out["outputs"][0]["text"].strip() == "builtin/data.csv"  # no notebook: left as written


def test_resolve_only_changes_builtin_paths(tmp_path):
    assert notebook_resources.resolve("builtin/x.csv", tmp_path) == f"{(tmp_path / 'builtin').as_posix()}/x.csv"
    assert notebook_resources.resolve("./builtin/x.csv", tmp_path).endswith("/builtin/x.csv")
    assert notebook_resources.resolve("builtins are fun", tmp_path) is None
    assert notebook_resources.resolve("builtin", tmp_path) is None
    assert notebook_resources.resolve("Files/builtin/x.csv", tmp_path) is None


def test_start_cell_points_the_kernel_at_the_notebooks_folder(svc):
    svc.notebook_resource_add("Sales", "t.txt", b64(b"hello"))
    svc.start_cell('print(open("builtin/t.txt").read())', notebook="Sales")
    snap = svc.poll_cell(-1, 2.0)
    while snap["status"] == "running":
        snap = svc.poll_cell(snap["version"], 2.0)
    assert snap["status"] == "ok" and snap["outputs"][0]["text"].strip() == "hello"


def cell_text(k, code):
    out = k.run(code)
    assert out["status"] == "ok", out
    return "".join(o.get("text", "") for o in out["outputs"]).strip()


@pytest.fixture
def kernel(tmp_path):
    k = Kernel()
    k.resources_dir = str(tmp_path / "N.resources")
    Resources(Path(k.resources_dir)).add("a.csv", b"x")
    return k


def test_the_bare_word_builtin_is_data_not_a_path(kernel):
    assert cell_text(kernel, 'kind = "builtin"\nprint(kind, kind == "builtin", "builtins/x")') == "builtin True builtins/x"


def test_only_an_fstrings_leading_text_is_rewritten(kernel, tmp_path):
    base = "abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Files/"
    assert cell_text(kernel, f'base = "{base}"\nprint(f"{{base}}builtin/x.csv")') == f"{base}builtin/x.csv"
    folder = (tmp_path / "N.resources" / "builtin").as_posix()
    assert cell_text(kernel, 'n = "a.csv"\nprint(f"builtin/{n}")') == f"{folder}/a.csv"
    assert cell_text(kernel, 'print(f"{open(\'builtin/a.csv\').read()}!")') == "x!"  # code in the braces is code


def test_nothing_is_rewritten_when_the_notebook_has_no_resources_folder(tmp_path):
    k = Kernel()
    k.resources_dir = str(tmp_path / "Empty.resources")  # a saved notebook with no files
    assert cell_text(k, 'print("builtin/a.csv")') == "builtin/a.csv"
    assert cell_text(k, 'print(notebookutils.nbResPath.endswith("Empty.resources"))') == "True"


def test_dotdot_stays_inside_the_folder(tmp_path):
    folder = (tmp_path / "builtin").as_posix()
    assert notebook_resources.resolve("builtin/../../outside.txt", tmp_path) == f"{folder}/outside.txt"
    assert notebook_resources.resolve("builtin/sub/../a.csv", tmp_path) == f"{folder}/a.csv"
    assert notebook_resources.resolve("builtin/..", tmp_path) == f"{folder}/"


def test_a_pc_schedule_run_reads_builtin_paths(svc):
    svc.notebook_resource_add("Sales", "t.txt", b64(b"hi"))
    svc.notebooks.save("Sales", [{"type": "code", "source": 'assert open("builtin/t.txt").read() == "hi"'}])
    assert svc._run_notebook_here("Sales")["status"] == "ok"


def test_a_builtin_path_after_another_base_stays_as_written(kernel, tmp_path):
    base = "abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Files/"
    assert cell_text(kernel, f'base = "{base}"\nprint(base + "builtin/x.csv")') == f"{base}builtin/x.csv"
    assert cell_text(kernel, f'import posixpath\nprint(posixpath.join("{base}", "builtin/x.csv"))') == f"{base}builtin/x.csv"
    assert cell_text(kernel, 'from pathlib import PurePosixPath as P\nprint(P("/data") / "builtin/x.csv")') == "/data/builtin/x.csv"
    assert cell_text(kernel, 'print(list({"builtin/k": 1}))') == "['builtin/k']"  # a dict key is a name
    folder = (tmp_path / "N.resources" / "builtin").as_posix()
    # Where nothing comes before it, it is still the notebook's folder.
    assert cell_text(kernel, 'n = "a.csv"\nprint("builtin/" + n)') == f"{folder}/a.csv"
    assert cell_text(kernel, 'import posixpath\nprint(posixpath.join("builtin/", "a.csv"))') == f"{folder}/a.csv"
    assert cell_text(kernel, 'print({"src": "builtin/a.csv"}["src"])') == f"{folder}/a.csv"
    assert cell_text(kernel, 'print(open(file="builtin/a.csv").read())') == "x"



@pytest.mark.parametrize("code", [
    'base + f"builtin/{n}"',
    'os.path.join(base, f"builtin/{n}")',
    '"/".join([base, "builtin/x.csv"])',
    '"%s/%s" % (base, "builtin/x.csv")',
    '"{}{}".format(base, "builtin/x.csv")',
    'base + ("builtin/x.csv" + "")',
    '"builtin/%s" % n',
    '"builtin/{}".format(n)',
])
def test_formatting_and_text_after_a_base_stay_as_written(kernel, code):
    out = cell_text(kernel, f'import os\nbase = "abfss://ws@onelake/lh/Files"\nn = "x.csv"\nprint({code})')
    assert "N.resources" not in out, out


@pytest.mark.parametrize("code", [
    'open("builtin/a.csv").read()',
    'open(file="builtin/a.csv").read()',
    'open("builtin/" + "a.csv").read()',
    'open(f"builtin/{n}").read()',
    'open(os.path.join("builtin/", n)).read()',
    'open(("builtin/a" + ".csv")).read()',
])
def test_a_builtin_path_on_its_own_still_reads_the_folder(kernel, code):
    assert cell_text(kernel, f'import os\nn = "a.csv"\nprint({code})') == "x"


def test_run_b_runs_a_python_or_sql_file_from_the_resources(tmp_path):
    folder = tmp_path / "N.resources"
    res = Resources(folder)
    res.add("helpers.py", b"def double(x):\n    return x * factor\nloaded = True\n")
    res.add("count.sql", b"SELECT 41 + 1 AS answer")
    res.add("notes.txt", b"hi")
    k = Kernel()
    k.resources_dir = str(folder)
    out = k.run('%run -b helpers.py {"factor": 2}\nprint(loaded, double(21))')
    assert out["status"] == "ok", out
    assert out["outputs"][-1]["text"].strip() == "True 42"
    out = k.run("%run --builtin count.sql\n_sqldf.collect()[0]['answer']")
    assert out["status"] == "ok", out
    assert out["outputs"][-1] == {"type": "text", "text": "42"}
    for line, says in [("%run -b notes.txt", "runs .py and .sql"), ("%run -b missing.py", "isn't in this notebook"),
                       ("%run -b ../../secrets.py", "isn't in this notebook")]:
        out = k.run(line)
        assert out["status"] == "error" and says in out["outputs"][-1]["evalue"], (line, out)
    k.resources_dir = ""
    out = k.run("%run -b helpers.py")
    assert out["status"] == "error" and "no resources folder" in out["outputs"][-1]["evalue"]


def test_run_b_is_a_resources_file_to_the_dependency_view():
    from fabric_desktop import depgraph
    out = depgraph.analyze("%run -b helpers.py")
    assert any("resources" in d["text"] for d in out["dynamic"])
