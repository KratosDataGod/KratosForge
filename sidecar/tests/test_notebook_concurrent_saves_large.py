"""A large notebook written by two callers of the engine at once (an autosave and Publish, the local
API or an MCP tool, a settings write): the saves take turns, so one never writes over another
unseen, a reader never sees half a file, and a settings write never drops cells (NotebookStore)."""
import json
import threading
import time

import pytest

from fabric_desktop.service import Service

CELLS = 1000


@pytest.fixture
def svc(tmp_path):
    return Service(client_id="x", root=tmp_path)


def big(svc):
    cells = [{"type": "code" if i % 4 else "markdown", "source": f"# cell {i}\n" + "x = 1\n" * 80} for i in range(CELLS)]
    svc.save_notebook("Big", cells)
    return svc.open_notebook("Big")


def together(*jobs):
    """Runs the jobs at once, from a shared start, and returns what each returned (or raised)."""
    start = threading.Barrier(len(jobs))
    results: list = [None] * len(jobs)

    def run(i, job):
        start.wait()
        try:
            results[i] = job()
        except Exception as exc:  # noqa: BLE001 - returned for the test to check
            results[i] = exc
    threads = [threading.Thread(target=run, args=(i, j)) for i, j in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert not any(t.is_alive() for t in threads), "a save didn't finish within two minutes"
    return results


def test_two_saves_from_the_same_read_one_lands_and_the_other_is_refused_and_kept(svc):
    opened = big(svc)
    a = [dict(c) for c in opened["cells"]]
    b = [dict(c) for c in opened["cells"]]
    a[100]["source"] = "from the autosave"
    b[900]["source"] = "from the local API"
    results = together(lambda: svc.save_notebook("Big", a, disk_stamp=opened["disk_stamp"]),
                       lambda: svc.save_notebook("Big", b, disk_stamp=opened["disk_stamp"]))
    assert not any(isinstance(r, Exception) for r in results), results
    refused = [r.get("changed_on_disk", False) for r in results]
    assert sorted(refused) == [False, True]  # exactly one written, the other told
    won, lost = (a, b) if not refused[0] else (b, a)
    on_disk = svc.notebooks.load("Big")["cells"]
    assert [c["source"] for c in on_disk] == [c["source"] for c in won]
    versions = svc.notebook_versions("Big")["versions"]
    kept = next(v for v in versions if v["kind"] == "not saved")
    assert [c["source"] for c in svc.notebook_version("Big", kept["id"])["cells"]] == [c["source"] for c in lost]


def test_a_settings_write_during_a_save_keeps_both(svc):
    opened = big(svc)
    mine = [dict(c) for c in opened["cells"]]
    mine[500]["source"] = "edited"
    together(lambda: svc.save_notebook("Big", mine, disk_stamp=opened["disk_stamp"]),
             lambda: svc.notebook_inputs_set("Big", mine, {"parameters": {"x": "2"}}))
    nb = json.loads(svc.notebooks.path("Big").read_text(encoding="utf-8"))
    assert len(nb["cells"]) == CELLS and "".join(nb["cells"][500]["source"]) == "edited"
    assert nb["metadata"]["fabric_desktop"]["inputs"] == {"parameters": {"x": "2"}}


def test_a_reader_never_sees_half_a_notebook_while_it_is_saved(svc):
    opened = big(svc)
    path = svc.notebooks.path("Big")
    stop = threading.Event()
    bad: list[str] = []

    def read():
        while not stop.is_set():
            time.sleep(0.001)  # leave the saves room to run
            try:
                nb = json.loads(path.read_bytes())
            except (FileNotFoundError, PermissionError):  # Windows: mid-swap the file can be briefly missing or locked, never half
                continue
            except ValueError as exc:
                bad.append(str(exc))
                return
            if len(nb["cells"]) != CELLS:
                bad.append(f"{len(nb['cells'])} cells")
                return
    reader = threading.Thread(target=read)
    reader.start()
    try:
        stamp = opened["disk_stamp"]
        cells = [dict(c) for c in opened["cells"]]
        for i in range(8):
            cells[i]["source"] = f"save {i}"
            out = svc.save_notebook("Big", cells, disk_stamp=stamp)
            assert "changed_on_disk" not in out
            stamp = out["disk_stamp"]
    finally:
        stop.set()
        reader.join(60)
    assert bad == []
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")] == []


def test_many_saves_of_one_large_notebook_end_with_the_last_one_and_no_temp_files(svc):
    opened = big(svc)
    variants = []
    for i in range(4):
        cells = [dict(c) for c in opened["cells"]]
        cells[i * 200]["source"] = f"writer {i}"
        variants.append(cells)
    results = together(*[lambda v=v: svc.save_notebook("Big", v) for v in variants])  # no stamp: each saves
    assert not any(isinstance(r, Exception) for r in results), results
    on_disk = [c["source"] for c in svc.notebooks.load("Big")["cells"]]
    assert on_disk in [[c["source"] for c in v] for v in variants]  # one whole save, never a mix
    path = svc.notebooks.path("Big")
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")] == []
