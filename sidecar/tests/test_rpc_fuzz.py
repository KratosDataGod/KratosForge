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
