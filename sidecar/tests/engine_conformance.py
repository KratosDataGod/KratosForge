"""The engine contract, checked against what each adapter's own descriptor claims (KUDP-01).

engines/contract.py says what every adapter does; engines/capabilities.py says what each engine claims.
These checks hold the two together, for any adapter, local or remote:

- ``describe()`` is the descriptor the registry lists, and the adapter can open sessions.
- A remote engine opens nothing, and contacts nothing, without an authorization.
- A cell that runs returns a ``CellResult`` the notebook can render: a known status, this engine's
  id, and outputs in the shapes ``notebook.display`` knows. What reaches ``on_output`` is in those
  shapes too.
- A cell that fails on the engine is an error *output*, not an exception, and never repeats a secret.
- A cell the descriptor says the engine can't run is refused (``Unsupported``, or an error result);
  it never comes back ``ok``.
- ``variables``, ``plan`` and ``catalog`` work when claimed and raise ``Unsupported`` with a reason
  when not. ``cancel`` with nothing running returns False, whatever the claim.
- ``close()`` frees the session: ``closed`` is true, later cells raise ``SessionClosed``, and closing
  twice is harmless.
- Two sessions share nothing.

An engine joins by giving a ``Harness``: how to open a session (against a fake for a remote engine)
and a few cells. test_engine_conformance.py runs every check against every registered adapter, and
fails when an engine with an adapter has no harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pytest

from fabric_desktop.engines import registry
from fabric_desktop.engines.authorization import RemoteNotAuthorized
from fabric_desktop.engines.capabilities import EngineDescriptor
from fabric_desktop.engines.contract import CellRequest, CellResult, SessionClosed, Unsupported

STATUSES = ("ok", "error", "cancelled")
# The shapes contract.py names and the keys each needs. A Python cell can also show the notebook's
# rich outputs (html, image, plotly...); those keep their own tests and only need a type here.
OUTPUT_KEYS = {"text": ("text",), "stream": ("text",), "error": ("ename", "evalue"), "table": ("columns", "rows")}
CATALOG_KEYS = ("catalog", "schema", "name", "type")


@dataclass
class Harness:
    engine: str
    adapter: Callable[[], Any]
    # Opens an authorized session; for a remote engine, against a fake of the platform.
    open: Callable[[Any], Any]
    ok_cell: CellRequest
    error_cell: CellRequest
    # Cells the descriptor says the engine doesn't run (a language or a dialect it marks unsupported).
    refused_cells: tuple[CellRequest, ...] = ()
    # A session whose cells fail on the platform, when ``error_cell`` alone can't make the fake fail.
    open_failing: Callable[[Any], Any] | None = None
    # Remote engines: try to open with no authorization, and count what reached the fake platform.
    open_unauthorized: Callable[[Any], Any] | None = None
    contacted: Callable[[], int] | None = None
    # Isolation: leave some state in a session, and say whether a session can see it.
    leave_state: Callable[[Any], None] | None = None
    sees_state: Callable[[Any], bool] | None = None
    # Values that must never appear in an output (tokens the fake hands out).
    secrets: tuple[str, ...] = ()
    _opened: list[Any] = field(default_factory=list)

    @property
    def descriptor(self) -> EngineDescriptor:
        return registry.get(self.engine)

    def session(self, failing: bool = False) -> Any:
        adapter = self.adapter()
        opener = self.open_failing if failing and self.open_failing else self.open
        s = opener(adapter)
        self._opened.append(s)
        return s

    def close_all(self) -> None:
        for s in self._opened:
            try:
                s.close()
            except Exception as exc:  # teardown must close the rest; the checks report failures
                print(f"closing a {self.engine} session failed: {exc!r}")
        self._opened.clear()


def _check_output(engine: str, out: Any, where: str) -> None:
    assert isinstance(out, dict), f"{engine}: {where} output {out!r} is not a dict"
    kind = out.get("type")
    assert isinstance(kind, str) and kind, f"{engine}: {where} output {out!r} has no type"
    missing = [k for k in OUTPUT_KEYS.get(kind, ()) if k not in out]
    assert not missing, f"{engine}: {where} {kind} output lacks {missing}"
    if kind == "table":
        cols, rows = out["columns"], out["rows"]
        assert isinstance(cols, list) and isinstance(rows, list), f"{engine}: table columns and rows are lists"
        for row in rows:
            assert len(row) == len(cols), f"{engine}: a table row has {len(row)} values for {len(cols)} columns"
    if kind == "error":
        assert isinstance(out["ename"], str) and isinstance(out["evalue"], str), f"{engine}: error text is a string"


def _check_result(h: Harness, r: Any, where: str) -> None:
    assert isinstance(r, CellResult), f"{h.engine}: {where} returned {type(r).__name__}, not CellResult"
    assert r.status in STATUSES, f"{h.engine}: {where} status {r.status!r} not in {STATUSES}"
    assert r.engine == h.engine, f"{h.engine}: {where} result names engine {r.engine!r}"
    assert r.seconds >= 0, f"{h.engine}: {where} took negative seconds"
    for out in r.outputs:
        _check_output(h.engine, out, where)
    text = repr(r.as_dict())
    for secret in h.secrets:
        assert secret not in text, f"{h.engine}: {where} repeats a secret in its outputs"


# ---------------------------------------------------------------- the checks


def check_descriptor(h: Harness) -> None:
    d = h.adapter().describe()
    assert d is h.descriptor or d == h.descriptor, f"{h.engine}: describe() differs from the registry's descriptor"
    assert d.adapter, f"{h.engine}: an engine with an adapter must say adapter=True"


def check_authorization(h: Harness) -> None:
    if not h.descriptor.remote:
        return
    assert h.open_unauthorized and h.contacted, f"{h.engine}: a remote harness says how to open without approval"
    before = h.contacted()
    with pytest.raises(RemoteNotAuthorized):
        h.open_unauthorized(h.adapter())
    assert h.contacted() == before, f"{h.engine}: contacted the platform before checking the authorization"


def check_execute(h: Harness) -> None:
    s = h.session()
    seen: list[Any] = []
    r = s.execute(h.ok_cell, seen.append)
    _check_result(h, r, "a cell that runs")
    assert r.status == "ok", f"{h.engine}: the harness's ok cell failed: {r.outputs}"
    for out in seen:
        _check_output(h.engine, out, "streamed")
    assert s.engine == h.engine, f"{h.engine}: session.engine is {s.engine!r}"


def check_errors_are_outputs(h: Harness) -> None:
    s = h.session(failing=True)
    r = s.execute(h.error_cell)
    _check_result(h, r, "a failing cell")
    assert r.status == "error", f"{h.engine}: a failing cell came back {r.status!r}"
    assert r.outputs and r.outputs[-1]["type"] == "error", f"{h.engine}: a failing cell ends with an error output"
    assert r.outputs[-1]["evalue"], f"{h.engine}: an error output says what went wrong"


def check_refused_cells(h: Harness) -> None:
    s = h.session()
    for cell in h.refused_cells:
        d = h.descriptor
        unsupported = not d.language(cell.language).available or (
            cell.language == "sql" and cell.dialect and not d.dialect(cell.dialect).available)
        assert unsupported, f"{h.engine}: harness lists {cell.language}/{cell.dialect} as refused, but it's claimed"
        try:
            r = s.execute(cell)
        except Unsupported as exc:
            assert str(exc), f"{h.engine}: Unsupported says why"
            continue
        _check_result(h, r, f"a {cell.language} cell")
        assert r.status != "ok", f"{h.engine}: ran a {cell.language}/{cell.dialect} cell its descriptor says it can't"


def check_optional_capabilities(h: Harness) -> None:
    s = h.session()
    s.execute(h.ok_cell)
    calls = {"variables": lambda: s.variables(), "plan": lambda: s.plan(_plan_code(h)), "catalog": lambda: s.catalog()}
    problems = []
    for name, call in calls.items():
        claimed = h.descriptor.capability(name).available
        try:
            got = call()
        except Unsupported as exc:
            if claimed:
                problems.append(f"claims {name} but raised Unsupported ({exc})")
            elif not str(exc):
                problems.append(f"Unsupported {name} doesn't say why")
            continue
        if not claimed:
            problems.append(f"marks {name} unsupported but returned {got!r} instead of raising Unsupported")
        elif name == "plan" and not (isinstance(got, str) and got.strip()):
            problems.append(f"claims plan but returned {got!r}")
        elif name == "catalog":
            if not isinstance(got, list):
                problems.append("catalog isn't a list")
            for entry in got if isinstance(got, list) else []:
                missing = [k for k in CATALOG_KEYS if k not in entry]
                if missing:
                    problems.append(f"catalog entry {entry!r} lacks {missing}")
        elif name == "variables" and not (isinstance(got, list) and all("name" in v for v in got)):
            problems.append("variables aren't a list of named entries")
    assert not problems, f"{h.engine}: " + "; ".join(problems)


def _plan_code(h: Harness) -> str:
    return h.ok_cell.code if h.ok_cell.language == "sql" else "SELECT 1"


def check_idle_cancel(h: Harness) -> None:
    s = h.session()
    try:
        assert s.cancel() is False, f"{h.engine}: cancel with nothing running must return False"
    except Unsupported:
        assert not h.descriptor.capability("cancel").available, f"{h.engine}: claims cancel but raised Unsupported"


def check_close(h: Harness) -> None:
    s = h.session()
    assert not s.closed
    s.close()
    assert s.closed, f"{h.engine}: closed is false after close()"
    with pytest.raises(SessionClosed):
        s.execute(h.ok_cell)
    s.close()
    assert s.cancel() is False, f"{h.engine}: cancel on a closed session returns False"


def check_isolation(h: Harness) -> None:
    if h.leave_state is None or h.sees_state is None:
        assert h.descriptor.remote, f"{h.engine}: a local harness shows its sessions share nothing"
        return
    a, b = h.session(), h.session()
    h.leave_state(a)
    assert h.sees_state(a), f"{h.engine}: the harness's state isn't visible even in its own session"
    assert not h.sees_state(b), f"{h.engine}: one session sees another's state"
    a.close()
    c = h.session()
    assert not h.sees_state(c), f"{h.engine}: a new session sees a closed session's state"


CHECKS: dict[str, Callable[[Harness], None]] = {
    "descriptor": check_descriptor,
    "authorization": check_authorization,
    "execute": check_execute,
    "errors": check_errors_are_outputs,
    "refused": check_refused_cells,
    "capabilities": check_optional_capabilities,
    "idle_cancel": check_idle_cancel,
    "close": check_close,
    "isolation": check_isolation,
}
