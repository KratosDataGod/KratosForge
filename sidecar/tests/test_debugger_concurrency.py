"""Notebook debugger (#2362 hardening, round 5): cells that start threads, run asyncio, hand their
own functions to library code or use magics pause only in the cell's own code, on the right line,
never hang, and leave no trace hook for the next cell."""

from __future__ import annotations

import time

import pytest

from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel

OTHER = [{"type": "code", "source": "def helper():\n    h = 1\n    return h\nran_other = helper()"}]


def _load(name):
    return OTHER


@pytest.fixture(params=["engine", "process"])
def kernel(request):
    k = Kernel(load_notebook=_load) if request.param == "engine" else ProcessKernel(load_notebook=_load)
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


def stack(p):
    return [(f["function"], f["line"]) for f in p["stack"]]


def variables(p):
    return {v["name"]: v["value"] for v in p["variables"]}


def text(snap):
    return "".join(o.get("text", "") for o in snap["outputs"] if o.get("type") in ("text", "stream"))


def finished(k, timeout=20):
    snap = wait(k, timeout)
    assert not snap.get("paused"), snap
    assert snap["status"] == "ok", snap
    return snap


def untraced(k):
    """The next plain cell runs with no trace hook on its thread and none set for new threads."""
    k.start("import sys, threading\nprint(sys.gettrace() is None, threading.gettrace() is None)")
    assert text(finished(k)).strip() == "True True"


# ---------- 1. threads the cell starts run at full speed ----------

THREAD = ("import threading\n"
          "seen = []\n"
          "def work(n):\n"
          "    seen.append(n)\n"  # 4: a breakpoint here never stops the worker thread
          "t = threading.Thread(target=work, args=(1,))\n"
          "t.start()\n"
          "t.join(10)\n"
          "alive = t.is_alive()\n"  # 8
          "print(seen, alive)")


def test_a_thread_running_a_cell_function_never_pauses(kernel):
    kernel.start(THREAD, breakpoints=[4, 8])
    p = wait(kernel)["paused"]
    assert p["line"] == 8 and stack(p) == [("(cell)", 8)]  # the worker ran line 4 without stopping
    assert variables(p)["seen"] == "[1]"
    kernel.debug("continue")
    assert text(finished(kernel)).strip() == "[1] False"
    untraced(kernel)


def test_the_cell_calling_the_same_function_pauses_in_it_while_its_thread_does_not(kernel):
    kernel.start(THREAD + "\nwork(2)", breakpoints=[4])
    p = wait(kernel)["paused"]
    assert stack(p) == [("(cell)", 10), ("work", 4)]
    assert variables(p)["n"] == "2"
    kernel.debug("continue")
    finished(kernel)
    untraced(kernel)


def test_a_thread_pool_map_over_a_cell_function_never_pauses(kernel):
    code = ("from concurrent.futures import ThreadPoolExecutor\n"
            "def square(n):\n"
            "    return n * n\n"  # 3
            "with ThreadPoolExecutor(4) as pool:\n"
            "    out = list(pool.map(square, range(20)))\n"
            "total = sum(out)\n"  # 6
            "print(total)")
    kernel.start(code, breakpoints=[3, 6])
    p = wait(kernel)["paused"]
    assert stack(p) == [("(cell)", 6)] and len(variables(p)["out"]) > 2  # no worker stopped at line 3
    kernel.debug("next")
    assert wait(kernel)["paused"]["line"] == 7
    kernel.debug("continue")
    assert text(finished(kernel)).strip() == "2470"
    untraced(kernel)


def test_stepping_over_a_join_while_the_thread_runs_cell_code(kernel):
    code = ("import threading, time\n"
            "def work():\n"
            "    time.sleep(0.2)\n"
            "    return 1\n"  # 4
            "t = threading.Thread(target=work)\n"
            "t.start()\n"  # 6
            "t.join(10)\n"
            "done = not t.is_alive()")
    kernel.start(code, breakpoints=[4, 6])
    assert wait(kernel)["paused"]["line"] == 6
    kernel.debug("step")  # start() is library code: one step, and the worker isn't entered
    p = wait(kernel)["paused"]
    assert p["line"] == 7 and stack(p) == [("(cell)", 7)]
    kernel.debug("next")
    assert wait(kernel)["paused"]["line"] == 8
    kernel.debug("next")
    finished(kernel)
    untraced(kernel)


# ---------- 2. asyncio ----------

ASYNC = ("import asyncio\n"
         "async def fetch(n):\n"
         "    await asyncio.sleep(0)\n"  # 3
         "    return n * 2\n"  # 4
         "async def main():\n"
         "    a = await fetch(1)\n"  # 6
         "    b = await fetch(2)\n"  # 7
         "    return a + b\n"  # 8
         "result = asyncio.run(main())\n"  # 9
         "print(result)")  # 10


def test_a_breakpoint_in_a_coroutine_pauses_with_its_stack(kernel):
    kernel.start(ASYNC, breakpoints=[4])
    p = wait(kernel)["paused"]
    assert p["line"] == 4 and stack(p) == [("(cell)", 9), ("main", 6), ("fetch", 4)]
    assert variables(p)["n"] == "1"
    kernel.debug("continue")
    p = wait(kernel)["paused"]
    assert stack(p) == [("(cell)", 9), ("main", 7), ("fetch", 4)]
    kernel.debug("continue")
    assert text(finished(kernel)).strip() == "6"
    untraced(kernel)


def test_next_steps_over_awaits_line_by_line(kernel):
    kernel.start(ASYNC, breakpoints=[6])
    assert wait(kernel)["paused"]["line"] == 6
    stacks = []
    for _ in range(3):
        kernel.debug("next")
        stacks.append(stack(wait(kernel)["paused"]))
    assert stacks == [[("(cell)", 9), ("main", 7)], [("(cell)", 9), ("main", 8)], [("(cell)", 10)]]
    kernel.debug("next")
    assert text(finished(kernel)).strip() == "6"
    untraced(kernel)


def test_step_into_runs_through_awaits_without_stopping_where_a_coroutine_gives_way(kernel):
    """An await that gives way to the event loop is a "return" to the trace; Step stops at the
    coroutine's next line, not at its caller still waiting on the same await."""
    kernel.start(ASYNC, breakpoints=[6])
    assert wait(kernel)["paused"]["line"] == 6
    stacks = []
    for _ in range(7):
        kernel.debug("step")
        stacks.append(stack(wait(kernel)["paused"]))
    assert stacks == [
        [("(cell)", 9), ("main", 6), ("fetch", 3)],
        [("(cell)", 9), ("main", 6), ("fetch", 4)],
        [("(cell)", 9), ("main", 7)],
        [("(cell)", 9), ("main", 7), ("fetch", 3)],
        [("(cell)", 9), ("main", 7), ("fetch", 4)],
        [("(cell)", 9), ("main", 8)],
        [("(cell)", 10)],
    ]
    kernel.debug("step")
    assert text(finished(kernel)).strip() == "6"
    untraced(kernel)


def test_step_out_of_a_coroutine_runs_it_to_its_end(kernel):
    kernel.start(ASYNC, breakpoints=[3])
    assert stack(wait(kernel)["paused"]) == [("(cell)", 9), ("main", 6), ("fetch", 3)]
    kernel.debug("out")  # past the await's sleep, not stopped by it
    p = wait(kernel)["paused"]
    assert stack(p) == [("(cell)", 9), ("main", 7)]
    assert variables(p)["a"] == "2"
    kernel.debug("out")  # the breakpoint in the second call stops it first
    assert stack(wait(kernel)["paused"]) == [("(cell)", 9), ("main", 7), ("fetch", 3)]
    kernel.debug("out")
    assert stack(wait(kernel)["paused"]) == [("(cell)", 9), ("main", 8)]
    kernel.debug("out")
    assert stack(wait(kernel)["paused"]) == [("(cell)", 10)]
    kernel.debug("continue")
    finished(kernel)
    untraced(kernel)


def test_step_out_to_a_coroutine_with_no_line_left_stops_as_it_returns(kernel):
    """Only a coroutine's suspensions are passed over: when Step out comes back into one whose last
    line is the call (return helper()), the step stops as that coroutine really returns, as it does
    for a plain function, not back in the cell."""
    code = ("import asyncio\n"
            "def helper():\n"
            "    return 41\n"  # 3
            "async def main():\n"
            "    await asyncio.sleep(0)\n"  # 5
            "    return helper() + 1\n"  # 6
            "result = asyncio.run(main())\n"  # 7
            "print(result)")
    kernel.start(code, breakpoints=[3])
    assert stack(wait(kernel)["paused"]) == [("(cell)", 7), ("main", 6), ("helper", 3)]
    kernel.debug("out")
    assert stack(wait(kernel)["paused"]) == [("(cell)", 7), ("main", 6)]
    kernel.debug("continue")
    assert text(finished(kernel)).strip() == "42"
    untraced(kernel)


def test_a_coroutine_running_a_cell_function_in_a_thread_never_pauses_there(kernel):
    code = ("import asyncio\n"
            "def blocking():\n"
            "    return 41\n"  # 3
            "async def main():\n"
            "    v = await asyncio.to_thread(blocking)\n"
            "    return v + 1\n"  # 6
            "print(asyncio.run(main()))")
    kernel.start(code, breakpoints=[3, 6])
    p = wait(kernel)["paused"]
    assert stack(p) == [("(cell)", 7), ("main", 6)] and variables(p)["v"] == "41"
    kernel.debug("continue")
    assert text(finished(kernel)).strip() == "42"
    untraced(kernel)


def test_top_level_await_is_a_syntax_error_as_without_the_debugger(kernel):
    """Cells don't run top-level ``await`` (use asyncio.run); under the debugger it fails the same way."""
    kernel.start("import asyncio\nasync def f():\n    return 3\nx = await f()", breakpoints=[3])
    snap = wait(kernel)
    assert snap["status"] == "error" and not snap.get("paused")
    assert any(o.get("ename") == "SyntaxError" for o in snap["outputs"]), snap
    untraced(kernel)


# ---------- 3. cell functions called back from library code ----------
# They are the cell's own code, so they pause wherever they run on the cell's thread, with only the
# cell's frames on the stack. Next over the library call runs every callback as one step.

GEN = ("import heapq\n"
       "def gen(n):\n"
       "    for i in range(n):\n"
       "        yield i * 10\n"  # 4
       "def key(v):\n"
       "    return -v\n"  # 6
       "xs = list(gen(3))\n"  # 7
       "ys = sorted(xs, key=key)\n"  # 8
       "zs = heapq.nsmallest(2, xs, key=key)\n"  # 9: heapq is Python code, between the cell and key()
       "print(xs, ys, zs)")


def test_a_generator_consumed_by_list_pauses_at_each_yield(kernel):
    kernel.start(GEN, breakpoints=[4])
    values = []
    for _ in range(3):
        p = wait(kernel)["paused"]
        assert stack(p) == [("(cell)", 7), ("gen", 4)]
        values.append(variables(p)["i"])
        kernel.debug("continue")
    assert values == ["0", "1", "2"]
    assert text(finished(kernel)).strip() == "[0, 10, 20] [20, 10, 0] [20, 10]"
    untraced(kernel)


def test_a_key_function_pauses_when_called_back_from_library_code(kernel):
    kernel.start(GEN, breakpoints=[6])
    stacks = []
    for _ in range(6):
        stacks.append(stack(wait(kernel)["paused"]))
        kernel.debug("continue")
    assert stacks == [[("(cell)", 8), ("key", 6)]] * 3 + [[("(cell)", 9), ("key", 6)]] * 3
    finished(kernel)
    untraced(kernel)


def test_next_runs_library_callbacks_as_one_step_and_out_leaves_a_generator(kernel):
    kernel.start(GEN, breakpoints=[7])
    assert wait(kernel)["paused"]["line"] == 7
    for line in (8, 9, 10):
        kernel.debug("next")
        assert stack(wait(kernel)["paused"]) == [("(cell)", line)]
    kernel.debug("continue")
    finished(kernel)
    kernel.start(GEN, breakpoints=[3])
    assert stack(wait(kernel)["paused"]) == [("(cell)", 7), ("gen", 3)]
    kernel.debug("out")  # breakpoint 3 is reached again on the next value
    assert stack(wait(kernel)["paused"]) == [("(cell)", 7), ("gen", 3)]
    kernel.debug("continue")
    for _ in range(2):  # and twice more: the last ends the loop
        assert stack(wait(kernel)["paused"]) == [("(cell)", 7), ("gen", 3)]
        kernel.debug("continue")
    finished(kernel)
    untraced(kernel)


def test_step_into_a_library_call_stops_in_the_cell_functions_it_calls(kernel):
    kernel.start(GEN, breakpoints=[9])
    assert wait(kernel)["paused"]["line"] == 9
    kernel.debug("step")
    p = wait(kernel)["paused"]
    assert stack(p) == [("(cell)", 9), ("key", 6)]
    assert "heapq.nsmallest" in p.get("skipped", []), p
    kernel.debug("continue")
    finished(kernel)
    untraced(kernel)


def test_an_async_generator_pauses_inside_it_with_its_consumer_on_the_stack(kernel):
    code = ("import asyncio\n"
            "async def agen():\n"
            "    for i in range(2):\n"
            "        await asyncio.sleep(0)\n"
            "        yield i\n"  # 5
            "async def main():\n"
            "    return [x async for x in agen()]\n"  # 7
            "print(asyncio.run(main()))")
    kernel.start(code, breakpoints=[4])
    assert stack(wait(kernel)["paused"]) == [("(cell)", 8), ("main", 7), ("agen", 4)]
    kernel.debug("step")  # the await gives way to the loop; Step lands on the next line of agen
    assert stack(wait(kernel)["paused"]) == [("(cell)", 8), ("main", 7), ("agen", 5)]
    kernel.debug("continue")
    assert stack(wait(kernel)["paused"]) == [("(cell)", 8), ("main", 7), ("agen", 4)]
    kernel.debug("continue")
    assert text(finished(kernel)).strip() == "[0, 1]"
    untraced(kernel)


# ---------- 4. magics ----------

@pytest.mark.parametrize("header", ["%%time", "%%capture cap"])
def test_python_cell_magics_keep_their_line_numbers(kernel, header):
    kernel.start(f"{header}\na = 1\nb = 2\nc = 3", breakpoints=[3])
    p = wait(kernel)["paused"]
    assert stack(p) == [("(cell)", 3)] and variables(p)["a"] == "1"
    kernel.debug("next")
    assert wait(kernel)["paused"]["line"] == 4
    kernel.debug("next")
    finished(kernel)
    untraced(kernel)


@pytest.mark.parametrize("code", ["%%timeit -n1 -r1\na = 1\nb = 2", "%%html\n<b>x</b>\n<i>y</i>",
                                  "%%markdown\n# x\ny", "%%writefile -x f.txt\nx"])
def test_cells_that_dont_run_as_the_cells_own_python_never_pause(kernel, code):
    kernel.start(code, breakpoints=[1, 2, 3])
    snap = wait(kernel)
    assert not snap.get("paused") and snap["status"] in ("ok", "error"), snap
    untraced(kernel)


def test_a_time_line_magic_pauses_on_its_line_and_in_cell_functions_it_runs(kernel):
    code = "def f():\n    v = 1\n    return v\n%time r = f()\nw = r"
    kernel.start(code, breakpoints=[2, 4])
    assert stack(wait(kernel)["paused"]) == [("(cell)", 4)]
    kernel.debug("continue")
    p = wait(kernel)["paused"]
    assert stack(p) == [("(cell)", 4), ("f", 2)]
    kernel.debug("out")
    assert stack(wait(kernel)["paused"]) == [("(cell)", 5)]
    kernel.debug("continue")
    assert "Wall time" in text(finished(kernel))
    untraced(kernel)


def test_a_run_magic_never_pauses_in_the_other_notebook(kernel):
    kernel.start("a = 1\n%run other\nz = ran_other", breakpoints=[1, 2])
    assert wait(kernel)["paused"]["line"] == 1
    kernel.debug("step")
    assert wait(kernel)["paused"]["line"] == 2
    kernel.debug("step")  # the other notebook runs as one step, though its cell has a line 2 too
    p = wait(kernel)["paused"]
    assert stack(p) == [("(cell)", 3)]
    kernel.debug("step")
    finished(kernel)
    # Its function, called from this cell, is another cell's code: Step steps over it.
    kernel.start("y = helper()\nq = y", breakpoints=[1])
    assert wait(kernel)["paused"]["line"] == 1
    kernel.debug("step")
    p = wait(kernel)["paused"]
    assert stack(p) == [("(cell)", 2)] and p.get("skipped") == ["helper (defined in another cell)"], p
    kernel.debug("continue")
    finished(kernel)
    untraced(kernel)
