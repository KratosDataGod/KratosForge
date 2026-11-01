"""Which engine each notebook runs on, and its session (ADR-002, ADR-003).

A notebook has at most one active engine session. Changing a notebook's engine never carries
state across: the old engine's variables and tables stay in the old session, which is closed
(``decision="close"``) or, for a local engine only, kept in the background (``decision="keep"``)
so switching back finds it. Without a decision, ``switch`` raises ``SwitchNeeded`` with what would
be lost and kept, for the app to ask. Cells and outputs belong to the notebook and are always kept.

Remote engines open only with a ``RemoteAuthorization`` (authorization.py). When a cell can't run
on the chosen engine, ``execute`` says so and stops: it never moves the cell to another engine.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import redact
from . import authorization as auth
from .capabilities import check_cell
from .contract import CellRequest, CellResult, EngineAdapter, EngineSession, OutputSink, SessionConfig, Unsupported
from .usage import UsageRecord

DECISIONS = ("close", "keep")
_log = logging.getLogger(__name__)


def _close(session: EngineSession, engine: str) -> None:
    """Close a session the notebook has already let go of. A failure is logged, never raised: the
    switch or close it belongs to has already happened, so an error would say otherwise."""
    try:
        session.close()
    except Exception as exc:  # noqa: BLE001 - logged; the caller's step already succeeded
        _log.warning("Closing a %s session failed (%s): %s", engine, type(exc).__name__,
                     redact.secrets(str(exc))[:300])


class SwitchNeeded(RuntimeError):
    """The notebook already has a session on another engine; the caller must decide what happens to it."""

    def __init__(self, transition: dict[str, Any]):
        super().__init__(transition["message"])
        self.transition = transition


@dataclass
class _Notebook:
    engine: str
    sessions: dict[str, EngineSession] = field(default_factory=dict)   # engine id -> session (kept ones too)


class SessionManager:
    def __init__(self, adapter_for: Callable[[str], EngineAdapter], on_usage: Callable[[list[UsageRecord]], None] | None = None):
        self._adapter_for = adapter_for
        self._on_usage = on_usage
        self._lock = threading.Lock()
        self._notebooks: dict[str, _Notebook] = {}

    def engine_of(self, notebook: str) -> str | None:
        nb = self._notebooks.get(notebook)
        return nb.engine if nb else None

    def session(self, notebook: str) -> EngineSession | None:
        nb = self._notebooks.get(notebook)
        return nb.sessions.get(nb.engine) if nb else None

    def transition(self, notebook: str, engine: str) -> dict[str, Any]:
        """What switching ``notebook`` to ``engine`` would do, without doing it."""
        nb = self._notebooks.get(notebook)
        current = nb.engine if nb else None
        if current is None or current == engine:
            return {"needed": False, "from": current, "to": engine, "keeps": ["cells", "outputs"], "loses": [],
                    "decisions": [], "message": ""}
        old = self._adapter_for(current).describe()
        new = self._adapter_for(engine).describe()
        decisions = ["close"] if old.remote else list(DECISIONS)
        return {"needed": True, "from": current, "to": engine, "keeps": ["cells", "outputs"],
                "loses": [f"variables and tables in the {old.label} session"],
                "decisions": decisions,
                "message": f"Switching from {old.label} to {new.label} starts a new session. Cells and outputs "
                           f"stay; what the {old.label} session holds isn't carried over."}

    def switch(self, notebook: str, engine: str, decision: str | None = None, config: SessionConfig | None = None,
               target: str = "", authorization: Any = None) -> EngineSession:
        """Make ``engine`` the notebook's engine, opening its session (or reusing a kept one)."""
        new = self._adapter_for(engine)
        desc = new.describe()
        auth.require(desc, target, authorization)
        with self._lock:
            nb = self._notebooks.get(notebook)
            t = self.transition(notebook, engine)
            if t["needed"]:
                if decision is None:
                    raise SwitchNeeded(t)
                if decision not in t["decisions"]:
                    raise ValueError(f"{decision!r} isn't possible here. Choose one of: {', '.join(t['decisions'])}.")
            session = nb.sessions.get(engine) if nb else None
            if session is None or session.closed:
                # Opened before the old session closes: if it can't open, the notebook keeps the
                # engine and session it had, never an engine with no session.
                session = new.open_session(config or SessionConfig(notebook), authorization)
            if nb is None:
                nb = self._notebooks[notebook] = _Notebook(engine)
            old = nb.sessions.pop(nb.engine) if t["needed"] and decision == "close" else None
            nb.engine = engine
            nb.sessions[engine] = session
            if old is not None:
                _close(old, t["from"])
            return session

    def check(self, notebook: str, request: CellRequest) -> dict[str, Any]:
        engine = self.engine_of(notebook)
        if engine is None:
            return {"ok": False, "level": "unsupported", "reason": "Pick an engine for this notebook first."}
        return check_cell(self._adapter_for(engine).describe(), request.language, request.dialect)

    def execute(self, notebook: str, request: CellRequest, on_output: OutputSink | None = None) -> CellResult:
        verdict = self.check(notebook, request)
        if not verdict["ok"]:
            raise Unsupported(verdict["reason"])
        session = self.session(notebook)
        if session is None:
            raise Unsupported("This notebook's engine has no open session. Pick its engine again.")
        result = session.execute(request, on_output)
        if self._on_usage:
            self._on_usage([UsageRecord(result.engine, "seconds", result.seconds, "measured", "engine session")])
        return result

    def cancel(self, notebook: str) -> bool:
        session = self.session(notebook)
        return bool(session and session.cancel())

    def close(self, notebook: str) -> None:
        """Close every session of ``notebook``, kept ones included."""
        with self._lock:
            nb = self._notebooks.pop(notebook, None)
        for engine, session in (nb.sessions.items() if nb else ()):
            _close(session, engine)

    def close_all(self) -> None:
        for notebook in list(self._notebooks):
            self.close(notebook)

    def open_sessions(self) -> list[dict[str, Any]]:
        return [{"notebook": name, "engine": eid, "active": eid == nb.engine}
                for name, nb in self._notebooks.items() for eid, s in nb.sessions.items() if not s.closed]
