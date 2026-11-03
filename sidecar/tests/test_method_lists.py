"""PB-098: the engine's lists of method names agree with each other.

The engine's methods are named in several places, and each check that they agree is listed here:

- ``server.METHODS``: what the shell may call over RPC. Each name is a method of ``Service``
  (server.handle calls ``getattr(service, method)``): checked below.
- ``server.QUICK``: the calls with their own lane. The same as ``QUICK`` in
  app/src-tauri/src/watchdog.rs and part of METHODS: test_server_lanes.py.
- ``audit.READ_ONLY``: the calls that aren't audited. Part of METHODS: test_audit.py.
- ``REMOVED`` in test_rpc_surface.py: names taken out of METHODS, refused over RPC and kept on
  ``Service`` for in-process callers. METHODS, and through it QUICK and READ_ONLY, can't name them.
- The capability registry (integrations/): what the MCP server and the local API offer. Each
  ``Service`` method a capability's handler calls exists, and a capability that says it is
  read-only doesn't call one the RPC audits as a change: checked below.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

from fabric_desktop import audit, server
from fabric_desktop.integrations import capabilities
from fabric_desktop.integrations.registry import Registry
from fabric_desktop.service import Service

INTEGRATIONS = Path(capabilities.__file__).parent

# Read-only capabilities that call a method the RPC audits, and why the call doesn't change anything.
READ_ONLY_CALLS_AUDITED = {
    ("fabric_desktop_verify_proof_table", "prove_verify"):
        "always passes save=False; only save=True writes a receipt, which is why the RPC audits it",
}


def _service_calls(tree: ast.AST, names: tuple[str, ...] = ("svc",)) -> list[str]:
    """The methods called on a variable named one of ``names``: ``svc.x(...)`` gives "x"."""
    return [n.func.attr for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name) and n.func.value.id in names]


def _core_capabilities() -> list:
    reg = Registry()
    capabilities.register_core(reg)
    return reg.all()


def test_every_rpc_method_is_a_service_method():
    missing = sorted(m for m in server.METHODS if not callable(getattr(Service, m, None)))
    assert not missing, f"in server.METHODS but not a Service method, so the call would fail: {missing}"


def test_every_service_method_the_integrations_call_exists():
    missing = sorted({f"{path.name}: {name}" for path in INTEGRATIONS.glob("*.py")
                      for name in _service_calls(ast.parse(path.read_text(encoding="utf-8")), ("svc", "service"))
                      if not hasattr(Service, name)})
    assert not missing, f"the integrations call Service methods that don't exist: {missing}"


def test_a_read_only_capability_calls_no_method_the_rpc_audits_as_a_change():
    """Only the handler's own calls are seen, not those of helpers it calls."""
    found = set()
    for cap in _core_capabilities():
        if not cap.read_only:
            continue
        source = textwrap.dedent(inspect.getsource(cap.handler))
        found |= {(cap.id, name) for name in _service_calls(ast.parse(source))
                  if name in server.METHODS and audit.audited(name)}
    unexplained = sorted(found - set(READ_ONLY_CALLS_AUDITED))
    assert not unexplained, ("a read-only capability calls a method the RPC audits as a change: mark the capability "
                             f"read_only=False, or list the call above with why it changes nothing: {unexplained}")
    stale = sorted(set(READ_ONLY_CALLS_AUDITED) - found)
    assert not stale, f"listed above but no longer called that way; remove them: {stale}"
