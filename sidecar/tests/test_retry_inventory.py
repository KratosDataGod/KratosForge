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
HAND_WRITTEN: dict[str, str] = {}


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


# ---- every request is sent through a retry helper, or says why it's sent once

_VERBS = {"get", "post", "put", "patch", "delete", "head", "request"}
_HELPERS = {"send_with_retry", "send_remote", "_change"}

# "module.py:function" -> why its request isn't sent through send_with_retry or send_remote.
SENT_ONCE: dict[str, str] = {
    "appstudio/providers.py:_send": "a model call the person started and waits on; a failure is shown so they can try again",
    "engines/databricks_auth.py:_m2m_token": "a sign-in; the Databricks client that asked for the token retries its own calls",
    "workloads/deliver.py:post_flow": "a write sent once; the delivery outbox decides whether to send it again",
}


def _a_request(call: ast.Call) -> bool:
    f = call.func
    if not (isinstance(f, ast.Attribute) and f.attr in _VERBS):
        return False
    target = ast.unparse(f.value)
    return target.endswith("session()") or target in {"http", "_http", "self._http"} or \
        (f.attr == "request" and target.endswith("session"))


def _outermost_functions(node: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Functions and methods not inside another function: a request in a nested ``send`` counts as
    its outer function's, which is where it's handed to the retry helper."""
    out: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(child)
        else:
            out += _outermost_functions(child)
    return out


def _helper_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) in _HELPERS


def _handed_names(outer: ast.AST) -> set[str]:
    return {n.id for h in ast.walk(outer) if _helper_call(h) for n in ast.walk(h) if isinstance(n, ast.Name)}


def _handed_to_a_helper(call: ast.Call, outer: ast.AST, parents: dict[ast.AST, ast.AST], handed: set[str]) -> bool:
    """Whether this one request is sent by a retry helper: it sits in a lambda (or nested def) that is
    an argument of send_with_retry, send_remote or _change, or in a nested def whose name is passed
    to one of them in ``outer``."""
    node: ast.AST = call
    while node is not outer and node in parents:
        parent = parents[node]
        if _helper_call(parent) and node is not getattr(parent, "func", None):
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in handed:
            return True
        node = parent
    return False


def unretried_requests(package: Path = PACKAGE) -> set[str]:
    """Functions with an HTTP request that isn't handed to send_with_retry, send_remote or leases' _change,
    each request judged on its own. It knows the names this package gives a session
    (http_session.session(), http, self._http, session): a session under another name isn't seen."""
    found = set()
    for path in sorted(package.rglob("*.py")):
        if path.name == "retrying.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for fn in _outermost_functions(tree):
            requests = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and _a_request(n)]
            handed = _handed_names(fn) if requests else set()
            if any(not _handed_to_a_helper(n, fn, parents, handed) for n in requests):
                found.add(f"{path.relative_to(package).as_posix()}:{fn.name}")
    return found


def test_every_request_retries_or_says_why_it_is_sent_once():
    found = unretried_requests()
    new = sorted(found - set(SENT_ONCE))
    assert not new, (f"{new} send a request with no retry for throttling or a 5xx: send it through "
                     "retrying.send_with_retry, or add it to SENT_ONCE with the reason")
    gone = sorted(set(SENT_ONCE) - found)
    assert not gone, f"{gone} no longer send a request outside a retry helper: take them out of SENT_ONCE"


def test_the_request_check_finds_an_unretried_get(tmp_path):
    (tmp_path / "client.py").write_text(
        "from . import http_session\n"
        "def read(url):\n"
        "    return http_session.session().get(url, timeout=30)\n"
        "def fine(url):\n"
        "    http = http_session.session()\n"
        "    return send_with_retry(lambda: http.get(url, timeout=30), 'GET')\n"
        "def nested(url):\n"
        "    http = http_session.session()\n"
        "    def page():\n"
        "        return http.get(url, timeout=30)\n"
        "    return send_with_retry(page, 'GET')\n"
        "def half(url):\n"
        "    http = http_session.session()\n"
        "    first = send_with_retry(lambda: http.get(url, timeout=30), 'GET')\n"
        "    return first, http.get(url + '/more', timeout=30)\n"
        "def unused_helper(url):\n"
        "    http = http_session.session()\n"
        "    def page():\n"
        "        return http.get(url, timeout=30)\n"
        "    send_with_retry(lambda: None, 'GET')\n"
        "    return page()\n", encoding="utf-8")
    assert unretried_requests(tmp_path) == {"client.py:read", "client.py:half", "client.py:unused_helper"}
