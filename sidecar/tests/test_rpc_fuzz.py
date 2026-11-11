"""#1173: every RPC method refuses malformed input with a typed error, before it does anything.

Each name in ``server.METHODS`` is called through ``server.handle`` with params that aren't an
object, an unknown parameter, and each parameter given a JSON type its annotation doesn't allow.
Every reply must be a ``TypeError`` naming the method, with no traceback and no path from this
machine, and nothing on disk may change. Network calls are refused for the whole test.
"""

from __future__ import annotations

import inspect
import json
import socket
from pathlib import Path

import pytest

from fabric_desktop import rpc_params, server
from fabric_desktop.service import Service

WRONG = (7, "text", True, [1], {"a": 1}, None)  # tried in turn: the first one a parameter doesn't allow


@pytest.fixture
def svc(tmp_path, monkeypatch):
    for var in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(var, str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")

    def refuse(*args, **kwargs):
        raise OSError("no network in this test")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    return Service(client_id="x", root=tmp_path / "app")


def _files(root: Path) -> dict[str, int]:
    """Every file and when it changed, except the audit log, which records refused calls too."""
    return {str(p.relative_to(root)): p.stat().st_mtime_ns for p in root.rglob("*")
            if p.is_file() and "audit" not in p.relative_to(root).parts}


def _cases(svc, name):
    """(label, params) pairs that are each wrong in one way."""
    yield "params a list", [1]
    yield "params null", None
    allowed, required, extra = rpc_params._rules(getattr(svc, name).__func__)
    if not extra:
        yield "unknown parameter", {"zz_not_a_parameter": 1}
    if required:
        yield "nothing given", {}
    for key, kinds in allowed.items():
        if kinds is None:
            continue
        for wrong in (v for v in WRONG if not rpc_params._fits(v, kinds)):
            yield f"{key} as {rpc_params._json_name(wrong)}", {k: "x" for k in required} | {key: wrong}
            break


def test_every_rpc_method_refuses_malformed_params_before_doing_anything(svc, tmp_path):
    before = _files(tmp_path)
    bad = []
    for name in sorted(server.METHODS):
        for label, params in _cases(svc, name):
            reply = server.handle(svc, json.dumps({"id": 1, "method": name, "params": params}))
            text = json.dumps(reply)
            if (set(reply) != {"id", "error"} or not reply["error"].startswith(f"TypeError: {name}: ")
                    or "Traceback" in text or str(tmp_path) in text):
                bad.append(f"{name} ({label}): {text[:200]}")
    assert not bad, "\n".join(bad)
    assert _files(tmp_path) == before, "a refused call changed a file"


def test_a_wrong_type_never_reaches_the_method(svc, monkeypatch):
    """A list for a bool used to turn the setting on: Python took the list as true."""
    called = []

    def set_enabled(self, enabled: bool) -> None:
        called.append(enabled)
    monkeypatch.setattr(Service, "editor_set_enabled", set_enabled)
    reply = server.handle(svc, json.dumps({"id": 3, "method": "editor_set_enabled", "params": {"enabled": [1]}}))
    assert reply == {"id": 3, "error": "TypeError: editor_set_enabled: enabled must be true or false, not a list"}
    assert not called


@pytest.mark.parametrize("value, kinds, fits", [
    (1, (float,), True), (True, (int,), False), (True, (bool,), True), (None, (str, type(None)), True),
    (None, (str,), False), ({"a": 1}, (dict,), True), ([1], (dict,), False), ("1", (int,), False)])
def test_json_types_match_python_annotations(value, kinds, fits):
    assert rpc_params._fits(value, kinds) is fits


@pytest.mark.parametrize("line, error", [
    ("[1]", "invalid request: expected an object with id, method and params"),
    ("5", "invalid request: expected an object with id, method and params"),
    ('"list_workspaces"', "invalid request: expected an object with id, method and params"),
    ('{"id": 2, "method": ["list_workspaces"]}', "unknown method ['list_workspaces']"),
    ('{"id": 2, "method": null}', "unknown method None"),
])
def test_a_request_that_isnt_an_object_still_gets_an_answer(svc, line, error):
    """It used to raise inside handle, so the shell waited for a reply that never came."""
    reply = server.handle(svc, line)
    assert reply["error"] == error


def test_an_error_quoting_a_huge_input_is_cut_short(svc):
    reply = server.handle(svc, json.dumps({"id": 4, "method": "A" * 200_000}))
    assert len(reply["error"]) < server.ERROR_CHARS + 100 and reply["error"].endswith("more characters)")


def test_every_rpc_method_has_a_signature_the_check_can_read():
    """A method whose annotations can't be resolved would skip the check silently."""
    unreadable = []
    for name in sorted(server.METHODS):
        try:
            rpc_params._rules(inspect.unwrap(getattr(Service, name)))
        except (TypeError, ValueError, NameError) as exc:
            unreadable.append(f"{name}: {exc}")
    assert not unreadable, unreadable


# The reading loop: a line that can't be read gets an error reply, and the engine reads on.
ANSWERED = b'{"id": "last", "method": "app_settings", "params": {}}\n'


def _serve(svc, data: bytes) -> list[dict]:
    import io
    out = io.StringIO()
    server.serve(svc, stdin=server._rpc_stdin(io.TextIOWrapper(io.BytesIO(data))), stdout=out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


@pytest.mark.parametrize("line, error", [
    (b"[" * 100_000, "invalid JSON: it's nested too deeply to read"),
    (b'{"id": 1, "method": "app_settings", "params": ' + b"[" * 100_000 + b"}", "invalid JSON: it's nested too deeply to read"),
    (b'{"id": ' + b"9" * 5000 + b', "method": "app_settings"}', "invalid JSON: Exceeds the limit"),
    (b'{"id": 1, "method": "\xff\xfe"}', "unknown method '\ufffd\ufffd'"),
    (b'{"id": 1, "method": ["app_settings"]}', "unknown method ['app_settings']"),
], ids=["deep", "deep params", "huge number", "not utf-8", "method a list"])
def test_a_line_that_cant_be_read_is_answered_and_the_engine_reads_on(svc, line, error):
    """Each of these used to end serve(), and the engine with it, with no reply."""
    first, last = _serve(svc, line + b"\n" + ANSWERED)
    assert first["error"].startswith(error)
    assert last["id"] == "last" and "result" in last


def _junk(r) -> object:
    """A random JSON value: deep, wide, huge, odd numbers and text."""
    kind = r.randrange(9)
    if kind == 0:
        return [_junk(r)] if r.random() < 0.6 else []
    if kind == 1:
        return {r.choice(["id", "method", "params", "", "x" * r.randrange(300), "\u0000"]): _junk(r) for _ in range(r.randrange(4))}
    if kind == 2:
        return r.choice([0, -1, 2**63, -(2**80), 10**300, 1e308, 5e-324])
    if kind == 3:
        return "".join(chr(r.choice([r.randrange(32, 127), r.randrange(0x80, 0xD7FF), r.randrange(0xE000, 0x10FFFF)]))
                       for _ in range(r.randrange(40)))
    if kind == 4:
        return "x" * r.choice([0, 1, 5000, 300_000])
    return r.choice([None, True, False, "", "app_settings", "list_workspaces", "__init__", "_home"])


def _line(r) -> bytes:
    shape = r.randrange(7)
    if shape == 0:  # raw bytes, not UTF-8 or not JSON
        return bytes(r.randrange(256) for _ in range(r.randrange(1, 200))).replace(b"\n", b" ").replace(b"\r", b" ")
    if shape == 1:  # deep
        depth = r.choice([50, 900, 5000, 60_000])
        return b"[" * depth + b"]" * r.choice([0, depth])
    if shape == 2:  # cut short
        whole = json.dumps({"id": r.randrange(9), "method": "app_settings", "params": _junk(r)}).encode()
        return whole[: r.randrange(1, max(2, len(whole)))]
    if shape == 3:  # NaN, Infinity and huge numbers where the id goes
        return r.choice([b'{"id": NaN, "method": "x"}', b'{"id": -Infinity, "method": 1}', b'{"id": 1e999, "method": []}',
                         b'{"id": ' + b"1" * r.randrange(4000, 6000) + b"}"])
    # a well-formed request with junk in it; only names that can't run, or app_settings (refused odd params)
    req = {"id": _junk(r), "method": r.choice(["app_settings", "nope", "__class__", "_home", "", "x" * 10_000]),
           "params": _junk(r)}
    if r.random() < 0.3:
        req.pop(r.choice(list(req)))
    return json.dumps(req).encode()


def test_random_lines_each_get_one_plain_reply_and_the_engine_stays_usable(svc, tmp_path):
    """Seeded, bounded fuzz of the reading loop: every non-blank line gets exactly one reply of strict
    JSON, with no traceback or path from this machine, and a good request after them all is answered."""
    import random
    r = random.Random(20261111)
    lines = [_line(r) for _ in range(300)]
    replies = _serve(svc, b"\n".join(lines) + b"\n" + ANSWERED)
    sent = [line for line in lines if line.decode("utf-8", "replace").strip()]
    assert len(replies) == len(sent) + 1
    for reply in replies:
        text = json.dumps(reply, allow_nan=False)
        assert "id" in reply and ("result" in reply or "error" in reply)
        assert "Traceback" not in text and str(tmp_path) not in text
    assert replies[-1]["id"] == "last" and "result" in replies[-1]
