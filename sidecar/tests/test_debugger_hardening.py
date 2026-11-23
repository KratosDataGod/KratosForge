"""Notebook debugger hardening (#2362 follow-up): stepping past code that isn't the cell's says so by
name, a kernel restart while paused leaves nothing behind, and large frames and values stay quick
to pause in, with the cap reported."""

from __future__ import annotations

import time

import pytest

from fabric_desktop import debugger, notebook
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel, _preview


@pytest.fixture(params=["engine", "process"])
def kernel(request):
    k = Kernel() if request.param == "engine" else ProcessKernel()
    yield k
    if request.param == "process":
        k.close()


def wait(k, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = k.poll(-1, 0.3)
        if snap.get("paused") or snap["status"] != "running":
            return snap
    raise AssertionError("the cell neither paused nor finished")


# ---------- step into: library code and other cells' functions are stepped over by name ----------

def test_step_into_library_code_is_stepped_over_and_named(kernel):
    kernel.start("import json\ntext = json.dumps([1, 2])\nn = len(text)", step=True)
    assert wait(kernel)["paused"]["line"] == 1
    kernel.debug("step")
    p = wait(kernel)["paused"]
    assert p["line"] == 2 and "skipped" not in p  # import runs no Python frame of json's the cell calls
    kernel.debug("step")
    p = wait(kernel)["paused"]
    assert p["line"] == 3
    assert p["skipped"] == ["json.dumps"]  # never entered, and said so
    assert kernel.debug("eval", "n if 'n' in dir() else 0")  # the pause stays as it was
    assert wait(kernel)["paused"]["skipped"] == ["json.dumps"]
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"


def test_step_into_a_function_from_another_cell_says_where_it_is(kernel):
    kernel.start("def helper(v):\n    w = v + 1\n    return w")
    assert wait(kernel)["status"] == "ok"
    kernel.start("a = helper(1)\nb = a", step=True)
    assert wait(kernel)["paused"]["line"] == 1
    kernel.debug("step")
    p = wait(kernel)["paused"]
    assert p["line"] == 2
    assert p["skipped"] == ["helper (defined in another cell)"]
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"


def test_step_over_and_continue_name_nothing(kernel):
    kernel.start("import json\ntext = json.dumps([1])\nn = 1", step=True)
    assert wait(kernel)["paused"]["line"] == 1
    kernel.debug("next")
    assert wait(kernel)["paused"]["line"] == 2
    kernel.debug("next")
    p = wait(kernel)["paused"]
    assert p["line"] == 3 and "skipped" not in p  # Step over means to skip calls: nothing to tell
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"


def test_skipped_names_are_capped():
    d = debugger.Debugger([], True, lambda n, v: {"name": n, "value": ""})
    cell = compile("x = 1", notebook._CELL_FILE, "exec")
    d.add(cell)

    class Frame:
        def __init__(self, name, back):
            self.f_code = compile("pass", "lib.py", "exec").replace(co_name=name, co_qualname=name)
            self.f_back = back
            self.f_globals = {"__name__": "lib"}

    class CellFrame:
        f_code = cell
    for i in range(20):
        d.trace(Frame(f"f{i}", CellFrame()), "call", None)
    assert d.skipped == [f"lib.f{i}" for i in range(debugger.MAX_SKIPPED)]


def test_the_debugger_knows_the_cells_file_name():
    assert debugger.CELL_FILE == notebook._CELL_FILE


# ---------- a kernel restart while paused ----------

def test_a_restart_while_paused_leaves_no_stuck_state(kernel):
    kernel.start("a = 1\nb = 2\nc = 3", breakpoints=[2])
    assert wait(kernel)["paused"]["line"] == 2
    kernel.restart()
    with pytest.raises(RuntimeError):
        kernel.debug("continue")  # nothing is paused any more
    snap = kernel.poll(-1, 0.3)
    assert snap.get("paused") is None and snap["status"] != "running"
    # The new kernel is clean (the stopped cell's line 3 never ran into it) and runs untraced.
    kernel.start("import sys\nprint(sys.gettrace() is None, 'a' in dir(), 'c' in dir())")
    snap = wait(kernel)
    assert snap["status"] == "ok", snap
    assert snap["outputs"][-1]["text"].strip() == "True False False"
    # And the debugger works again after it.
    kernel.start("x = 1\ny = 2", breakpoints=[2])
    assert wait(kernel)["paused"]["line"] == 2
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"


def test_the_engine_kernel_thread_of_a_paused_cell_ends_on_restart():
    k = Kernel()
    k.start("a = 1\nb = 2", breakpoints=[2])
    assert wait(k)["paused"]["line"] == 2
    run = k._current
    k.restart()
    assert run.done.wait(5) and not run.thread.is_alive()
    assert run.debugger.paused is None


# ---------- large frames and values ----------

def test_a_frame_with_many_variables_shows_the_first_and_says_how_many(kernel):
    code = "\n".join(f"v{i:04d} = {i}" for i in range(debugger.MAX_LOCALS + 50)) + "\nlast = 0"
    kernel.start(code, breakpoints=[debugger.MAX_LOCALS + 51])
    p = wait(kernel)["paused"]
    assert len(p["variables"]) == debugger.MAX_LOCALS
    names = [v["name"] for v in p["variables"]]
    assert names == sorted(names, key=str.lower)  # by name, so the cap is predictable
    assert p["variables_total"] >= debugger.MAX_LOCALS + 50  # the v0000.. names and the kernel's _i history
    assert "v0100" in names and "v0249" not in names
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"


def test_a_small_frame_has_no_total():
    k = Kernel()
    k.start("a = 1\nb = 2", breakpoints=[2])
    p = wait(k)["paused"]
    assert "variables_total" not in p
    k.debug("continue")
    wait(k)


class _Counted:
    """An item that counts how often its text is read."""
    reads = 0

    def __init__(self, n):
        self.n = n

    def __repr__(self):
        type(self).reads += 1
        return repr(self.n)


def test_previews_of_large_values_read_only_what_they_show():
    # Counted, not timed: each preview reads at most the first _PREVIEW_ITEMS items, never the whole value
    # (a wall-clock limit failed on busy CI runners with nothing changed).
    big = [_Counted(i) for i in range(20_000)]
    _Counted.reads = 0
    for _ in range(20):
        text = _preview(big)
    assert _Counted.reads <= 20 * notebook._PREVIEW_ITEMS, _Counted.reads  # not 20 reprs of every item
    assert text.startswith("[0, 1, 2, ") and text.endswith("...]") and len(text) <= 80
    assert _preview(list(range(3_000_000))) == text
    assert _preview(tuple(range(500))).endswith("...)")
    assert _preview({i: str(i) for i in range(500)}).startswith("{0: '0', 1: '1'")
    assert _preview(set(range(500))).endswith("...}")
    long_text = "x" * 5_000_000
    assert _preview(long_text) == repr("x" * 100)[:77] + "..."
    # Small values read as before.
    assert _preview([1, 2, 3]) == "[1, 2, 3]"
    assert _preview("abc") == "'abc'"
    assert _preview(list(range(100))) == repr(list(range(100)))[:77] + "..."


def test_a_preview_of_a_container_whose_items_fail_to_repr_is_a_question_mark():
    class Bad:
        def __repr__(self):
            raise RuntimeError("no")
    assert _preview([Bad()] * 200) == "?"


def test_pausing_next_to_a_large_list_stays_quick(kernel):
    # Counted, not timed: each pause reads at most the first _PREVIEW_ITEMS items of the list, never all
    # 5 million (a wall-clock limit failed on busy CI runners with nothing changed).
    kernel.start("class Item:\n    reads = 0\n    def __repr__(self):\n        Item.reads += 1\n        return '7'\n"
                 "big = [Item()] * 5_000_000\nx = 1\ny = 2", breakpoints=[7])
    p = wait(kernel)["paused"]
    for line in (8,):
        kernel.debug("next")
        p = wait(kernel)["paused"]
        assert p["line"] == line
    reads = int(kernel.debug("eval", "Item.reads")["value"])
    assert reads <= 2 * notebook._PREVIEW_ITEMS, reads  # two pauses, each reading only what it shows
    row = next(v for v in p["variables"] if v["name"] == "big")
    assert row["size"] == "5000000" and row["value"].endswith("...]")
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"
