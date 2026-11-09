"""Fabric and OneLake calls retry and wait only through retrying.py (WR-201, #2323).

A loop that sleeps and reads a response's status code or its Retry-After is a retry or poll written by
hand: it gets the throttling rules (which statuses, which methods, the cap, the floor, the deadline)
right or wrong on its own. This test finds every such loop outside retrying.py. HAND_WRITTEN lists the
ones still to move, each with its reason; a new one fails, and so does an entry that's gone.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"
_STATUSES = {408, 429, 500, 502, 503, 504}

# "module.py:function" -> why it's still written by hand. Take an entry out in the PR that moves it.
HAND_WRITTEN: dict[str, str] = {
    "fabric_api.py:_wait": "Follows a long-running operation, waiting as its Retry-After says until the "
                           "deadline; moves to a poller in retrying.py (WR-201, next PR).",
}


def _sleeps(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) and "sleep" in (getattr(n.func, "attr", None) or getattr(n.func, "id", "")).lower()
               for n in ast.walk(node))


def _reads_a_status(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr == "status_code":
            return True
        if isinstance(n, ast.Constant) and type(n.value) is int and n.value in _STATUSES:
            return True
        if isinstance(n, ast.Call) and (getattr(n.func, "attr", None) or getattr(n.func, "id", "")) == "retry_after":
            return True
    return False


def hand_written_loops(package: Path = PACKAGE) -> set[str]:
    found = set()
    for path in sorted(package.rglob("*.py")):
        if path.name == "retrying.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for loop in ast.walk(fn):
                    if isinstance(loop, (ast.For, ast.AsyncFor, ast.While)) and _sleeps(loop) and _reads_a_status(loop):
                        found.add(f"{path.relative_to(package).as_posix()}:{fn.name}")
    return found


def test_no_new_hand_written_retry_or_poll_loop():
    new = sorted(hand_written_loops() - set(HAND_WRITTEN))
    assert not new, (f"{new} sleep in a loop and read a response's status or Retry-After: use "
                     "retrying.send_with_retry (or add the poller you need to retrying.py)")


def test_the_list_of_hand_written_loops_isnt_stale():
    gone = sorted(set(HAND_WRITTEN) - hand_written_loops())
    assert not gone, f"{gone} no longer retry by hand: take them out of HAND_WRITTEN"


def test_the_check_finds_a_hand_written_retry(tmp_path):
    (tmp_path / "client.py").write_text(
        "import time\n"
        "def get(send):\n"
        "    for _ in range(3):\n"
        "        resp = send()\n"
        "        if resp.status_code != 429:\n"
        "            return resp\n"
        "        time.sleep(1)\n", encoding="utf-8")
    (tmp_path / "fine.py").write_text(
        "import time\n"
        "def wait(done):\n"
        "    while not done():\n"
        "        time.sleep(1)\n", encoding="utf-8")
    assert hand_written_loops(tmp_path) == {"client.py:get"}
