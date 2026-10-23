"""notebook.run and runMultiple as in Fabric: string exit values, timeouts, nesting, validateDAG."""

import time

import pytest

from fabric_desktop.notebook import MAX_RUN_DEPTH, Kernel, NotebookStore


def _kernel(tmp_path, notebooks):
    store = NotebookStore(tmp_path)
    for name, cells in notebooks.items():
        store.save(name, [{"type": "code", "source": c} for c in cells])
    return Kernel(load_notebook=lambda name: store.load(name)["cells"])


def test_exit_value_comes_back_as_a_string(tmp_path):
    k = _kernel(tmp_path, {"child": ["notebookutils.notebook.exit(42)"], "quiet": ["x = 1"]})
    out = k.run("(notebookutils.notebook.run('child'), notebookutils.notebook.run('quiet'))")
    assert out["outputs"][-1]["text"] == "('42', None)"


def test_run_stops_at_its_timeout_and_the_kernel_keeps_going(tmp_path):
    k = _kernel(tmp_path, {"slow": ["import time\nfor _ in range(200):\n    time.sleep(0.05)"]})
    started = time.monotonic()
    out = k.run("try:\n    notebookutils.notebook.run('slow', 1)\nexcept TimeoutError as e:\n    msg = str(e)\nmsg")
    assert out["status"] == "ok", out
    assert "didn't finish within 1 seconds" in out["outputs"][-1]["text"]
    assert time.monotonic() - started < 5
    assert k.run("1 + 1")["outputs"][-1]["text"] == "2"


def test_a_child_that_catches_exception_still_times_out(tmp_path):
    k = _kernel(tmp_path, {"greedy": ["import time\ntry:\n    for _ in range(40):\n        time.sleep(0.05)\n"
                                      "except Exception:\n    pass", "notebookutils.notebook.exit('done')"]})
    out = k.run("try:\n    r = notebookutils.notebook.run('greedy', 1)\nexcept TimeoutError as e:\n    r = 'timeout'\nr")
    assert out["outputs"][-1]["text"] == "'timeout'", out


def test_run_multiple_honors_cell_and_dag_timeouts(tmp_path):
    k = _kernel(tmp_path, {"slow": ["import time\nfor _ in range(200):\n    time.sleep(0.05)"],
                           "fast": ["notebookutils.notebook.exit('ok')"]})
    out = k.run("try:\n"
                "    notebookutils.notebook.runMultiple({'activities': ["
                "{'name': 's', 'path': 'slow', 'timeoutPerCellInSeconds': 1}, {'name': 'f', 'path': 'fast'}]})\n"
                "except RuntimeError as e:\n"
                "    res = e.results\n"
                "(res['f']['exitVal'], 'RunTimeout' in res['s']['exception'] or 'timeout' in res['s']['exception'])")
    assert out["outputs"][-1]["text"] == "('ok', True)", out
    out = k.run("try:\n"
                "    notebookutils.notebook.runMultiple({'timeoutInSeconds': 1, 'activities': ["
                "{'name': 's', 'path': 'slow'}, {'name': 'f', 'path': 'fast', 'dependencies': []}]})\n"
                "except RuntimeError as e:\n"
                "    res = e.results\n"
                "res['f']['exception']")
    assert "timeoutInSeconds (1) ran out" in out["outputs"][-1]["text"], out


def test_a_notebook_that_runs_itself_stops(tmp_path):
    k = _kernel(tmp_path, {"loop": ["notebookutils.notebook.run('loop')"]})
    out = k.run("try:\n    notebookutils.notebook.run('loop')\nexcept Exception as e:\n    msg = str(e)\nmsg")
    assert f"stops at {MAX_RUN_DEPTH}" in out["outputs"][-1]["text"] or "failed" in out["outputs"][-1]["text"], out
    assert any(f"stops at {MAX_RUN_DEPTH}" in (o.get("evalue") or o.get("text") or "") for o in out["outputs"])


@pytest.mark.parametrize("dag, ok", [
    ({"activities": [{"name": "a", "path": "x"}, {"name": "b", "path": "y", "dependencies": ["a"]}]}, True),
    ({"activities": [{"name": "a", "path": "x", "dependencies": ["b"]}]}, False),
])
def test_validate_dag(dag, ok):
    k = Kernel()
    k.namespace["dag"] = dag
    out = k.run("notebookutils.notebook.validateDAG(dag)")
    assert (out["status"] == "ok") is ok
    if ok:
        assert out["outputs"][-1]["text"] == "True"


def test_workspace_argument_says_it_runs_in_fabric(tmp_path):
    k = _kernel(tmp_path, {"child": ["x = 1"]})
    out = k.run("notebookutils.notebook.run('child', 90, {}, 'other-workspace')")
    assert out["status"] == "error" and "leave out workspace" in out["outputs"][-1]["evalue"]


def test_a_retry_after_the_dag_ran_out_of_time_doesnt_run_unlimited(tmp_path):
    import time
    k = _kernel(tmp_path, {"slow": ["import time\nfor _ in range(200):\n    time.sleep(0.05)"]})
    started = time.monotonic()
    out = k.run("try:\n"
                "    notebookutils.notebook.runMultiple({'timeoutInSeconds': '1', 'concurrency': '4', 'activities': ["
                "{'name': 's', 'path': 'slow', 'retry': 2, 'retryIntervalInSeconds': 30}]})\n"
                "except RuntimeError as e:\n"
                "    res = e.results\n"
                "res['s']['exception']")
    assert time.monotonic() - started < 6, out
    assert "timeoutInSeconds (1) ran out" in out["outputs"][-1]["text"], out


def test_run_multiple_refuses_a_notebook_listed_twice():
    k = Kernel()
    out = k.run("notebookutils.notebook.runMultiple(['a', 'a'])")
    assert out["outputs"][-1]["ename"] == "ValueError" and "listed twice" in out["outputs"][-1]["evalue"], out
