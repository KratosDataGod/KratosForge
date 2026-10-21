"""The contract every execution engine adapter meets, so the notebook never needs to know which one runs.

An adapter is the engine's factory: ``describe()`` says what it can do (capabilities.py) and
``open_session()`` starts a session. A session runs cells, can be cancelled, and is closed when
the notebook switches engine or closes. A session owns everything it opened (connections,
processes, temporary files) and frees all of it in ``close()``; two sessions never share state.

Methods a capability marks unsupported raise ``Unsupported``: callers check the descriptor first,
and an adapter never pretends.

Outputs use the shapes the notebook already renders (``notebook.display``): ``{"type": "text"}``,
``{"type": "table", "columns", "rows", ...}``, ``{"type": "error", "ename", "evalue"}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .capabilities import EngineDescriptor

OutputSink = Callable[[dict[str, Any]], None]
# Rows a SQL engine returns to the notebook; the rest is counted, not sent.
PREVIEW_ROWS = 1000


class Unsupported(RuntimeError):
    """The engine doesn't do this; its descriptor says so."""


class SessionClosed(RuntimeError):
    """The session was closed (engine switched, notebook closed or Stop)."""


@dataclass(frozen=True)
class SessionConfig:
    notebook: str
    # Read-only data the session may open (a DuckDB or SQLite file). Never written in this version.
    database: str = ""
    # Settings the engine understands (catalog, schema, warehouse id...). No secrets: credentials come
    # from the connection profile's references when a remote session opens.
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CellRequest:
    code: str
    language: str = "python"
    dialect: str | None = None
    preview_rows: int = PREVIEW_ROWS


@dataclass
class CellResult:
    status: str                      # ok | error | cancelled
    outputs: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0
    rows: int | None = None          # rows the statement returned (all of them, not just the preview)
    engine: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "outputs": self.outputs, "seconds": round(self.seconds, 4),
                "rows": self.rows, "engine": self.engine}


class EngineSession(Protocol):
    engine: str
    notebook: str

    def execute(self, request: CellRequest, on_output: OutputSink | None = None) -> CellResult: ...
    def cancel(self) -> bool: ...
    def variables(self) -> list[dict[str, Any]]: ...
    def plan(self, code: str, dialect: str | None = None) -> str: ...
    def catalog(self) -> list[dict[str, Any]]: ...
    def close(self) -> None: ...
    @property
    def closed(self) -> bool: ...


class EngineAdapter(Protocol):
    def describe(self) -> EngineDescriptor: ...
    def open_session(self, config: SessionConfig, authorization: Any = None) -> EngineSession: ...
