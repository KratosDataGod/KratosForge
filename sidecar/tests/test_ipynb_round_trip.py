"""Opening and saving a notebook in KratosForge keeps the file as other tools wrote it (NotebookStore,
merge_saved, dumps_like). The fixtures in fixtures/ipynb are shaped like files from real tools, down
to their bytes: classic Jupyter (4.4, no ids, a pasted image, a raw cell), a Fabric export (one line,
synapse_pyspark, a default lakehouse), Colab (indent 2, 4.0), VS Code on Windows (CRLF, escaped
non-ASCII, an empty and a repeated id) and a tab-indented Fabric sample."""
import difflib
import json
import shutil
from pathlib import Path

import pytest

from fabric_desktop.notebook import NotebookStore

FIXTURES = Path(__file__).parent / "fixtures" / "ipynb"
NAMES = sorted(p.stem for p in FIXTURES.glob("*.ipynb"))


def _open(tmp_path: Path, name: str) -> tuple[NotebookStore, dict, bytes]:
    raw = (FIXTURES / f"{name}.ipynb").read_bytes()
    shutil.copy(FIXTURES / f"{name}.ipynb", tmp_path / "nb.ipynb")
    store = NotebookStore(tmp_path)
    return store, store.load("nb"), raw


def _save(store: NotebookStore, nb: dict) -> bytes:
    store.save("nb", nb["cells"], nb["lakehouse"], nb["compute"], nb["workspace"], nb["disk_stamp"])
    return (store.folder / "nb.ipynb").read_bytes()


def _without_ids(cell: dict) -> dict:
    return {k: v for k, v in cell.items() if k != "id"}


def test_the_fixtures_are_all_here():
    assert NAMES == ["colab", "fabric_export", "jupyter_4_4", "tabs", "vscode_windows"]


@pytest.mark.parametrize("name", NAMES)
def test_open_then_save_with_no_change_leaves_the_file_byte_for_byte(tmp_path, name):
    store, nb, raw = _open(tmp_path, name)
    assert _save(store, nb) == raw
    again = store.load("nb")
    assert [c["cell_id"] for c in again["cells"]] == [c["cell_id"] for c in nb["cells"]]  # the same ids, read again


@pytest.mark.parametrize("name", NAMES)
def test_editing_one_cell_changes_that_cell_and_nothing_else(tmp_path, name):
    store, nb, raw = _open(tmp_path, name)
    before = json.loads(raw)
    k = max(i for i, c in enumerate(nb["cells"]) if c["type"] == "code")
    nb["cells"][k]["source"] += "\nprint('edited')"
    after_raw = _save(store, nb)
    after = json.loads(after_raw)

    for i, (old, new) in enumerate(zip(before["cells"], after["cells"], strict=True)):
        if i == k:
            assert "".join(new["source"]).endswith("print('edited')")
            assert new["outputs"] == [] and new["execution_count"] is None  # they came from the old code
            assert {m: v for m, v in new["metadata"].items()} == old["metadata"]  # every tool's settings kept
        else:
            assert _without_ids(new) == _without_ids(old)  # outputs, counts, attachments, metadata as they were
    assert {k: v for k, v in after.items() if k not in ("cells", "nbformat_minor")} == \
        {k: v for k, v in before.items() if k not in ("cells", "nbformat_minor")}
    assert after["nbformat_minor"] == max(before["nbformat_minor"], 5)  # cell ids are format 4.5

    # Written the way the file was: same line ending, final newline and indent, so Git shows the edit.
    assert (b"\r\n" in after_raw) == (b"\r\n" in raw)
    assert after_raw.count(b"\n") == after_raw.count(b"\r\n") or b"\r\n" not in raw
    assert after_raw.endswith(b"\n") == raw.endswith(b"\n")
    if all(isinstance(c.get("id"), str) and c["id"] for c in before["cells"]) and \
            len({c["id"] for c in before["cells"]}) == len(before["cells"]):
        changed = [line for line in difflib.unified_diff(raw.decode().splitlines(), after_raw.decode().splitlines(),
                                                         lineterm="", n=0)
                   if line[:1] in "+-" and not line.startswith(("+++", "---"))]
        removed_outputs = len(json.dumps(before["cells"][k]["outputs"], indent=1).splitlines())
        assert len(changed) <= 4 + 2 * removed_outputs, changed


def test_a_fabric_export_keeps_its_kernel_lakehouse_environment_and_name(tmp_path):
    store, nb, raw = _open(tmp_path, "fabric_export")
    nb["cells"][2]["source"] = "df.count() * 2"
    after = json.loads(_save(store, nb))
    before = json.loads(raw)
    assert after["metadata"]["kernelspec"]["name"] == "synapse_pyspark"
    assert after["metadata"]["dependencies"] == before["metadata"]["dependencies"]
    assert after["notebookName"] == "Load bronze"
    assert (tmp_path / "nb.ipynb").read_bytes().count(b"\n") == 0  # still on one line


def test_attaching_the_same_lakehouse_keeps_fabrics_other_lakehouse_keys(tmp_path):
    store, nb, _raw = _open(tmp_path, "fabric_export")
    lakehouse = {"workspace_id": "eb6fd9d2-ac52-43e8-9ffd-28aec7232a16",
                 "lakehouse_id": "4951bb7a-5148-401c-949f-74b22a0880d4", "name": "Sales"}
    store.save("nb", nb["cells"], lakehouse, nb["compute"], nb["workspace"], nb["disk_stamp"])
    deps = json.loads((tmp_path / "nb.ipynb").read_bytes())["metadata"]["dependencies"]
    assert deps["lakehouse"]["known_lakehouses"] == [{"id": "4951bb7a-5148-401c-949f-74b22a0880d4"}]
    assert deps["lakehouse"]["default_lakehouse"] == lakehouse["lakehouse_id"]
    assert deps["environment"]["environmentId"] == "ee1133e7-6e29-4328-9da0-15e4748f6bd4"


def test_a_lakehouse_the_app_set_then_detached_is_removed(tmp_path):
    store, nb, _raw = _open(tmp_path, "tabs")
    lakehouse = {"workspace_id": "w1", "lakehouse_id": "l1", "name": "Gold"}
    store.save("nb", nb["cells"], lakehouse, nb["compute"], nb["workspace"], nb["disk_stamp"])
    nb = store.load("nb")
    store.save("nb", nb["cells"], None, nb["compute"], nb["workspace"], nb["disk_stamp"])
    meta = json.loads((tmp_path / "nb.ipynb").read_bytes())["metadata"]
    assert "lakehouse" not in meta["dependencies"]
    assert "lakehouse" not in meta.get("fabric_desktop", {})


def test_a_pasted_image_stays_while_the_markdown_still_shows_it(tmp_path):
    store, nb, raw = _open(tmp_path, "jupyter_4_4")
    image = json.loads(raw)["cells"][0]["attachments"]
    nb["cells"][0]["source"] += "\n\nMore words."
    assert json.loads(_save(store, nb))["cells"][0]["attachments"] == image
    nb = store.load("nb")
    nb["cells"][0]["source"] = "# Sales check, no picture"
    assert "attachments" not in json.loads(_save(store, nb))["cells"][0]


def test_an_unchanged_raw_cell_stays_raw(tmp_path):
    store, nb, _raw = _open(tmp_path, "jupyter_4_4")
    nb["cells"][1]["source"] += "\n"
    after = json.loads(_save(store, nb))
    assert after["cells"][3]["cell_type"] == "raw"
    assert after["cells"][3]["source"] == ["{{ jinja }} kept as raw text"]


def test_a_setting_the_file_has_off_stays_as_the_file_has_it_and_one_turned_off_goes(tmp_path):
    store, nb, _raw = _open(tmp_path, "fabric_export")
    nb["cells"][1]["source"] += "\n"
    md = json.loads(_save(store, nb))["cells"][1]["metadata"]
    assert md["jupyter"] == {"source_hidden": False, "outputs_hidden": False}
    nb = store.load("nb")
    nb["cells"][1]["meta"] = {"outputs_hidden": True}
    md = json.loads(_save(store, nb))["cells"][1]["metadata"]
    assert md["jupyter"] == {"source_hidden": False, "outputs_hidden": True}
    nb = store.load("nb")
    nb["cells"][1]["meta"] = {}
    md = json.loads(_save(store, nb))["cells"][1]["metadata"]
    assert md.get("jupyter", {}).get("outputs_hidden") is not True


def test_settings_kept_in_the_file_by_the_app_keep_its_format(tmp_path):
    store, _nb, raw = _open(tmp_path, "vscode_windows")
    store.set_extra("nb", "inputs", {"parameters": {"x": "1"}})
    after = (tmp_path / "nb.ipynb").read_bytes()
    assert after.count(b"\n") == after.count(b"\r\n")
    assert json.loads(after)["cells"] == json.loads(raw)["cells"]


def test_a_new_notebook_is_written_as_before(tmp_path):
    store = NotebookStore(tmp_path)
    store.save("fresh", [{"type": "code", "source": "x = 1"}])
    text = (tmp_path / "fresh.ipynb").read_bytes().decode()
    nb = json.loads(text)
    assert nb["nbformat_minor"] == 5 and nb["metadata"]["kernelspec"]["name"] == "python3"
    assert text.splitlines()[1].startswith(' "') and not text.splitlines()[1].startswith('  "')  # indent 1
