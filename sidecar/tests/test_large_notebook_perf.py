"""WR-134: a 2,000-cell notebook opens and saves within a measured budget (NotebookStore and the
open_notebook / save_notebook RPCs, which also run the environment check, fabric_run.compute_check).

Measured on a Linux container (best of three; 2,000 cells: 1,600 code, 200 of them %%sql, 400
Markdown, a third of the code cells with saved outputs):

    NotebookStore.save (new file)      ~0.035 s
    NotebookStore.load                 ~0.016 s
    NotebookStore.save (one edit)      ~0.037 s
    open_notebook RPC                  ~1.16 s before WR-134, ~0.73 s after
    save_notebook RPC                  ~1.23 s before WR-134, ~0.82 s after

Most of the RPCs' time is compute_check reading every code cell. It used to scan each cell for
line magics character by character five times over (magics._lines), and walk every cell's syntax
tree once more than it needed (fabric_run._names); both are fixed.

The budgets are about 5x the measurement (more for the sub-0.1 s store calls, where timer noise
and a first fsync dominate), for a slow CI runner and for Windows, the target, where file writes
(fsync, antivirus) are slower. They are proxies for what a person waits for; a
budget is never widened to make this pass: find what got slow instead.
"""

from __future__ import annotations

import json
import time

import pytest

from fabric_desktop import server
from fabric_desktop.notebook import NotebookStore
from fabric_desktop.service import Service

CELLS = 2000

STORE_SAVE_NEW_S = 0.5
STORE_LOAD_S = 0.5
STORE_SAVE_EDIT_S = 1.0
RPC_OPEN_S = 4.0
RPC_SAVE_S = 4.5


def cells() -> list[dict]:
    out = []
    for i in range(CELLS):
        if i % 5 == 0:
            out.append({"type": "markdown", "source": f"## Section {i}\n\nSome *text* about step {i}, with `code`.\n"})
        elif i % 10 == 3:
            out.append({"type": "code", "source": f"%%sql\nSELECT id, amount FROM dbo.sales_{i % 7} WHERE id > {i}\n"})
        else:
            out.append({"type": "code", "source": f"df_{i} = spark.read.table('dbo.t{i % 7}')\n"
                        + "".join(f"x_{j} = df_{i}.count() + {j}\n" for j in range(10)) + f"display(df_{i})\n"})
    return out


def with_outputs(path) -> None:
    """Outputs as Jupyter or a Fabric run would leave them, on a third of the code cells."""
    nb = json.loads(path.read_text(encoding="utf-8"))
    for i, cell in enumerate(nb["cells"]):
        if cell["cell_type"] == "code" and i % 3 == 0:
            cell["execution_count"] = i
            cell["outputs"] = [{"output_type": "stream", "name": "stdout", "text": [f"row {j}\n" for j in range(20)]}]
    path.write_text(json.dumps(nb, indent=1), encoding="utf-8")


def best(fn, runs: int = 3) -> tuple[float, object]:
    times, out = [], None
    for _ in range(runs):
        started = time.perf_counter()
        out = fn()
        times.append(time.perf_counter() - started)
    return min(times), out


def call(svc, method, **params):
    out = json.loads(json.dumps(server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))))
    assert "error" not in out, out["error"]
    return out["result"]


@pytest.fixture
def svc(tmp_path):
    service = Service(client_id="x", root=tmp_path)
    call(service, "save_notebook", name="Warm", cells=[{"type": "code", "source": "x = 1"}])
    call(service, "open_notebook", name="Warm")  # imports and caches out of the timings
    return service


def test_notebook_store_saves_and_loads_2000_cells_within_budget(tmp_path):
    store = NotebookStore(tmp_path)
    took, _ = best(lambda: store.save("Big", cells()))
    assert took < STORE_SAVE_NEW_S, f"save of a new 2,000-cell notebook took {took:.3f}s"
    with_outputs(store.path("Big"))

    took, nb = best(lambda: store.load("Big"))
    assert took < STORE_LOAD_S, f"load took {took:.3f}s"
    assert len(nb["cells"]) == CELLS
    assert len({c["cell_id"] for c in nb["cells"]}) == CELLS

    edited = [dict(c) for c in nb["cells"]]
    edited[1234]["source"] += "\ny = 2"
    took, out = best(lambda: store.save("Big", edited, expected_stamp=None))
    assert took < STORE_SAVE_EDIT_S, f"save of one edited cell took {took:.3f}s"
    assert out["cell_ids"] == [c["cell_id"] for c in nb["cells"]]
    saved = json.loads(store.path("Big").read_text(encoding="utf-8"))["cells"]
    assert saved[3]["outputs"] and not saved[1234].get("outputs")  # unchanged cells keep their outputs


def test_open_and_save_rpcs_on_2000_cells_stay_within_budget(svc):
    call(svc, "save_notebook", name="Big", cells=cells())
    with_outputs(svc.notebooks.path("Big"))

    took, opened = best(lambda: call(svc, "open_notebook", name="Big"))
    assert took < RPC_OPEN_S, f"open_notebook took {took:.3f}s"
    assert len(opened["cells"]) == CELLS and "check" in opened

    edited = [dict(c) for c in opened["cells"]]
    stamp = opened["disk_stamp"]

    def save():
        nonlocal stamp
        edited[42]["source"] += "\n# edited"
        out = call(svc, "save_notebook", name="Big", cells=edited, disk_stamp=stamp)
        assert not out.get("changed_on_disk")
        stamp = out["disk_stamp"]
        return out

    took, out = best(save)
    assert took < RPC_SAVE_S, f"save_notebook took {took:.3f}s"
    assert "check" in out
