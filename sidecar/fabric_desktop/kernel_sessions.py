"""Notebook sessions: a kernel per notebook, like a Fabric or Databricks session.

With "Run notebooks in their own process" on (kernel_process), every notebook gets a process of
its own (kernel_proc.ProcessKernel): its own variables, its own ``spark``, and cells in several
notebooks run at the same time. Stop, restart and limits act on that notebook's process only.

With it off, cells run inside the engine, which has one attached lakehouse, one ``spark``
session and one record of what was read, so every notebook shares the one in-engine kernel and
one cell runs at a time, as before. The Sessions list says so.

Sessions that sit idle can be stopped after ``kernel_idle_minutes`` (Settings; 0 keeps them),
and at most ``kernel_max_sessions`` processes are kept: opening one more stops the least
recently used idle one. A running session is never stopped to make room.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .kernel_proc import ProcessKernel, memory_used

SHARED = ""  # the key of the one in-engine kernel every notebook shares
DEFAULT_MAX_SESSIONS = 5


class Session:
    def __init__(self, notebook: str, kernel: Any):
        self.notebook = notebook
        self.kernel = kernel
        self.created = time.time()
        self.last_used = time.time()

    def touch(self) -> None:
        self.last_used = time.time()

    def running(self) -> bool:
        return self.kernel.poll().get("status") == "running"

    def alive(self) -> bool:
        alive = getattr(self.kernel, "alive", None)
        return alive() if alive else True


class KernelSessions:
    """Kernels by notebook name. ``make(process)`` builds one; ``process()`` says which kind."""

    def __init__(self, make: Callable[[bool], Any], process: Callable[[], bool],
                 max_sessions: Callable[[], int] = lambda: DEFAULT_MAX_SESSIONS,
                 idle_minutes: Callable[[], float] = lambda: 0):
        self._make = make
        self._process = process
        self._max = max_sessions
        self._idle = idle_minutes
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self.active = SHARED  # the notebook the app has open; callers that name none get its kernel
        self.notes: list[str] = []  # sessions stopped to make room or for being idle, for the app to show
        self.in_process = bool(process())

    # ---- finding a notebook's kernel

    def _key(self, notebook: str | None) -> str:
        name = self.active if notebook is None else notebook
        return name if self.in_process else SHARED

    def session(self, notebook: str | None = None, create: bool = True) -> Session | None:
        with self._lock:
            key = self._key(notebook)
            found = self._sessions.get(key)
            if found is not None:
                # Looking it up is using it: reap_idle, under the same lock, won't close a kernel a
                # caller has just been handed (a proof or replay about to start a cell in it).
                found.touch()
            elif create:
                if self.in_process:
                    self._make_room()
                found = self._sessions[key] = Session(key, self._make(self.in_process))
            return found

    def kernel(self, notebook: str | None = None) -> Any:
        return self.session(notebook).kernel  # type: ignore[union-attr]

    def put(self, notebook: str | None, kernel: Any) -> None:
        """Use ``kernel`` for a notebook (tests, and a kernel the caller built itself)."""
        with self._lock:
            self.in_process = isinstance(kernel, ProcessKernel)
            self._sessions[self._key(notebook)] = Session(self._key(notebook), kernel)

    def kernels(self) -> list[Any]:
        with self._lock:
            return [s.kernel for s in self._sessions.values()]

    def any_running(self, other_than: str | None = None) -> bool:
        with self._lock:
            sessions = list(self._sessions.values())
        skip = None if other_than is None else self._key(other_than)
        return any(s.running() for s in sessions if s.notebook != skip)

    # ---- changes

    def switch(self, process: bool) -> list[Any]:
        """Settings changed in-engine <-> own process: every session starts over. Returns the old
        kernels for the caller to close (only once nothing is running)."""
        with self._lock:
            old = self.kernels()
            self._sessions = {}
            self.in_process = process
            return old

    def rename(self, old: str, new: str) -> None:
        with self._lock:
            if self.in_process and old in self._sessions and new not in self._sessions:
                session = self._sessions.pop(old)
                session.notebook = new
                self._sessions[new] = session
            if self.active == old:
                self.active = new

    def stop(self, notebook: str) -> dict[str, Any]:
        """End a notebook's session: its process ends and its variables are cleared."""
        with self._lock:
            session = self._sessions.get(self._key(notebook))
        if session is None:
            return {"stopped": False}
        # The session stays, so the cell Stop ended still reports why; the next cell starts a
        # fresh process. The in-engine kernel can't be stopped: the app ends the engine instead.
        return session.kernel.stop()

    def forget(self, notebook: str) -> None:
        """A deleted notebook: its process ends and its session goes."""
        if not self.in_process:
            return
        with self._lock:
            session = self._sessions.pop(notebook, None)
        if session is not None:
            session.kernel.close()

    def _make_room(self) -> None:
        cap = max(1, int(self._max() or DEFAULT_MAX_SESSIONS))
        live = [s for s in self._sessions.values() if s.alive()]
        idle = sorted((s for s in live if not s.running()), key=lambda s: s.last_used)
        while len(live) >= cap and idle:
            oldest = idle.pop(0)
            oldest.kernel.close()
            del self._sessions[oldest.notebook]
            live.remove(oldest)
            self.notes.append(f"Stopped the session for {oldest.notebook} to make room: at most {cap} notebooks "
                              "keep a session (Settings). Its variables were cleared.")

    def reap_idle(self) -> None:
        """Stop process sessions idle for longer than the Settings allow."""
        minutes = float(self._idle() or 0)
        if not self.in_process or minutes <= 0:
            return
        cutoff = time.time() - minutes * 60
        with self._lock:
            # Taken out under the lock, so a cell can't start in a session that is about to close.
            stale = [s for s in self._sessions.values()
                     if s.notebook != self.active and s.last_used < cutoff and s.alive() and not s.running()]
            for s in stale:
                del self._sessions[s.notebook]
        for s in stale:
            if s.running():  # a cell started in it from a kernel a caller already held: keep it
                with self._lock:
                    self._sessions.setdefault(s.notebook, s)
                continue
            s.kernel.close()
            self.notes.append(f"Stopped the session for {s.notebook}: idle for more than {minutes:g} minutes. "
                              "Its variables were cleared.")

    def take_notes(self) -> list[str]:
        notes, self.notes = self.notes, []
        return notes

    # ---- the Sessions list

    def describe(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._sessions.values())
        rows = []
        for s in sessions:
            k = s.kernel
            process = isinstance(k, ProcessKernel)
            alive = s.alive()
            if process and not s.notebook and not alive:
                continue  # made for a caller that named no notebook, and never started
            snap = k.poll() if alive or not process else {"status": "idle"}
            pid = k.pid if process and alive else None
            rows.append({
                "notebook": s.notebook or None,  # None: the one in-engine kernel every notebook shares
                "process": process,
                "status": "running" if snap.get("status") == "running" else
                          "idle" if alive else "stopped" if getattr(k, "count", 0) else "ready",
                "pid": pid,
                "memory_bytes": memory_used(pid) if pid else None,
                "cells_run": getattr(k, "count", 0),
                "started": s.created,
                "last_used": s.last_used,
                "active": (s.notebook == self.active) if self.in_process else True,
            })
        return sorted(rows, key=lambda r: -r["last_used"])
