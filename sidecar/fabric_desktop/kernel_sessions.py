"""Notebook sessions: a kernel per notebook, like a Fabric or Databricks session.

With "Run notebooks in their own process" on (kernel_process), every notebook gets a process of
its own (kernel_proc.ProcessKernel): its own variables, its own ``spark``, and cells in several
notebooks run at the same time. Stop, restart and limits act on that notebook's process only.

With it off, cells run inside the engine, which has one attached lakehouse, one ``spark``
session and one record of what was read, so every notebook shares the one in-engine kernel and
one cell runs at a time, as before. The Sessions list says so.

A notebook can share another notebook's session (``share``), as Fabric's high-concurrency
sessions do: it runs in that notebook's process, with its ``spark``, temp views and settings, one
cell at a time across them, but with variables of its own (notebook.Kernel.as_guest). Stop ends
the session for all of them; Leave gives the notebook a session of its own again.

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


class GuestKernel:
    """A notebook's view of the session it shares (KernelSessions.share): every call goes to the
    host's kernel as that notebook (as_guest), so it reads and runs the notebook's own variables.
    Restart clears only its variables; Stop ends the shared session for every notebook in it.
    Proofs and replays need a session of the notebook's own."""

    def __init__(self, target: Any, notebook: str, host: str):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "guest", notebook)
        object.__setattr__(self, "host", host)

    @property  # type: ignore[misc]
    def __class__(self) -> type:  # isinstance(kernel, ProcessKernel) holds for a guest of one
        return type(self._target)

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._target, name)
        if not callable(value) or name in ("alive",):
            return value

        def as_guest(*args: Any, **kwargs: Any) -> Any:
            with self._target.as_guest(self.guest):
                return value(*args, **kwargs)
        return as_guest

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._target, name, value)  # notebook_name, widget_values: set just before each cell

    def restart(self) -> None:
        self._target.forget_guest(self.guest)

    def close(self) -> None:
        """Leaving: its variables go; the host's process stays."""
        self._target.forget_guest(self.guest)

    def _own_session(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(f"{self.guest} shares {self.host}'s session. Leave the shared session (Sessions) "
                           "to prove or replay it.")

    bind_proof = unbind_proof = finish_proof = _own_session


class Session:
    def __init__(self, notebook: str, kernel: Any):
        self.notebook = notebook
        self.kernel = kernel
        self.created = time.time()
        self.last_used = time.time()

    def touch(self) -> None:
        self.last_used = time.time()

    def running(self) -> bool:
        snap = self.kernel.poll()
        return snap.get("status") == "running" or bool(snap.get("session_running"))  # a guest's cell counts

    def alive(self) -> bool:
        alive = getattr(self.kernel, "alive", None)
        return alive() if alive else True


class KernelSessions:
    """Kernels by notebook name. ``make(process)`` builds one; ``process()`` says which kind."""

    def __init__(self, make: Callable[[bool], Any], process: Callable[[], bool],
                 max_sessions: Callable[[], int] = lambda: DEFAULT_MAX_SESSIONS,
                 idle_minutes: Callable[[], float] = lambda: 0,
                 prepare: Callable[[str, Any], None] = lambda _notebook, _kernel: None):
        self._make = make
        self._process = process
        self._max = max_sessions
        self._idle = idle_minutes
        self._prepare = prepare  # a new notebook kernel gets its environment (environment_service)
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._reaping: dict[str, Session] = {}  # taken out by reap_idle, not closed yet
        self._joined: dict[str, str] = {}  # a notebook sharing another's session -> that notebook (share)
        self.active = SHARED  # the notebook the app has open; callers that name none get its kernel
        self.notes: list[str] = []  # sessions stopped to make room or for being idle, for the app to show
        self.in_process = bool(process())

    # ---- finding a notebook's kernel

    def _key(self, notebook: str | None) -> str:
        name = self.active if notebook is None else notebook
        return name if self.in_process else SHARED

    def host_of(self, notebook: str | None) -> str | None:
        """The notebook whose session ``notebook`` shares, or None."""
        with self._lock:
            return self._joined.get(self.active if notebook is None else notebook)

    def session(self, notebook: str | None = None, create: bool = True) -> Session | None:
        with self._lock:
            key = self._key(notebook)
            host = self._joined.get(key)
            if host is not None:
                found = self.session(host, create)
                if found is None:
                    return None
                found.touch()  # a guest's use keeps the host's session from being reaped as idle
                guest = Session(key, GuestKernel(found.kernel, key, host))
                guest.created, guest.last_used = found.created, found.last_used
                return guest
            found = self._sessions.get(key)
            if found is None and key in self._reaping:
                # reap_idle has it out but hasn't closed it: it comes back, rather than a second
                # session (and process) being made for the notebook.
                found = self._sessions[key] = self._reaping.pop(key)
            if found is not None:
                # Looking it up is using it: reap_idle, under the same lock, won't close a kernel a
                # caller has just been handed (a proof or replay about to start a cell in it).
                found.touch()
            elif create:
                if self.in_process:
                    self._make_room()
                found = self._sessions[key] = Session(key, self._make(self.in_process))
                self._prepare(key, found.kernel)
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
            old = self.kernels() + [s.kernel for s in self._reaping.values()]
            self._sessions, self._reaping, self._joined = {}, {}, {}
            self.in_process = process
            return old

    def rename(self, old: str, new: str) -> None:
        with self._lock:
            self._joined = {new if g == old else g: new if h == old else h for g, h in self._joined.items()}
            if self.in_process and new not in self._sessions:
                # An idle session reap_idle has out moves too, so a lookup by the new name gets it back.
                for sessions in (self._sessions, self._reaping):
                    if old in sessions and new not in sessions:
                        session = sessions.pop(old)
                        session.notebook = new
                        sessions[new] = session
            if self.active == old:
                self.active = new

    def stop(self, notebook: str) -> dict[str, Any]:
        """End a notebook's session: its process ends and its variables are cleared. A shared
        session ends for every notebook in it."""
        with self._lock:
            key = self._key(notebook)
            session = self._sessions.get(self._joined.get(key, key))
        if session is None:
            return {"stopped": False}
        # The session stays, so the cell Stop ended still reports why; the next cell starts a
        # fresh process. The in-engine kernel can't be stopped: the app ends the engine instead.
        return session.kernel.stop()

    def forget(self, notebook: str) -> None:
        """A deleted notebook: its process ends and its session goes. Notebooks that shared it get
        sessions of their own; a guest that is deleted leaves its host's session."""
        if not self.in_process:
            return
        if notebook in self._joined:
            self.leave(notebook)
            return
        with self._lock:
            for guest in [g for g, h in self._joined.items() if h == notebook]:
                del self._joined[guest]
            session = self._sessions.pop(notebook, None) or self._reaping.pop(notebook, None)
        if session is not None:
            session.kernel.close()

    # ---- shared sessions (Fabric's high-concurrency mode)

    def share(self, notebook: str, host: str) -> dict[str, Any]:
        """``notebook`` joins ``host``'s session: its own session ends (its variables are cleared) and
        its cells run in the host's process from then on, with variables of their own."""
        if not self.in_process:
            raise RuntimeError("Sharing a session needs notebooks in their own process. Turn on Run notebooks in "
                               "their own process in Settings > Preview features.")
        with self._lock:
            host = self._joined.get(host, host)  # a guest's host: one session, never a chain
            if not notebook or not host or notebook == host:
                raise ValueError("Pick another notebook to share this notebook's session with.")
            if any(h == notebook for h in self._joined.values()):
                raise RuntimeError(f"Other notebooks share {notebook}'s session. They leave it first.")
            own = self._sessions.get(notebook) or self._reaping.get(notebook)
            if own is not None and own.running():
                raise RuntimeError(f"A cell is running in {notebook}. Share its session when it has finished.")
            before = self._joined.get(notebook)
        if before is not None and before != host:
            self.leave(notebook)
        with self._lock:
            own = self._sessions.pop(notebook, None) or self._reaping.pop(notebook, None)
            self._joined[notebook] = host
        if own is not None:
            own.kernel.close()
        return {"notebook": notebook, "host": host, "shared_with": self.guests(host)}

    def leave(self, notebook: str) -> dict[str, Any]:
        """``notebook`` leaves the shared session: its variables there go, and its next cell starts a
        session of its own."""
        with self._lock:
            host = self._joined.pop(notebook, None)
            found = self._sessions.get(host) if host is not None else None
        if found is not None:
            found.kernel.forget_guest(notebook)
        return {"notebook": notebook, "left": host}

    def joined(self) -> dict[str, str]:
        """Each notebook that shares another's session, and that notebook."""
        with self._lock:
            return dict(self._joined)

    def guests(self, host: str) -> list[str]:
        with self._lock:
            return sorted(g for g, h in self._joined.items() if h == host)

    def _in_use(self) -> set[str]:
        """The open notebook's session: its own, or the one it shares."""
        return {self.active, self._joined.get(self.active, self.active)}

    def _make_room(self) -> None:
        cap = max(1, int(self._max() or DEFAULT_MAX_SESSIONS))
        live = [s for s in self._sessions.values() if s.alive()]
        # Never the open notebook's: a proof or replay of another notebook mustn't clear its variables.
        idle = sorted((s for s in live if not s.running() and s.notebook not in self._in_use()), key=lambda s: s.last_used)
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
                     if s.notebook not in self._in_use() and s.last_used < cutoff and s.alive() and not s.running()]
            for s in stale:
                self._reaping[s.notebook] = self._sessions.pop(s.notebook)
        for s in stale:
            running = s.running()  # a cell started from a kernel a caller already held
            with self._lock:
                if self._reaping.get(s.notebook) is not s:
                    continue  # looked up meanwhile: back in use
                del self._reaping[s.notebook]
                if running:
                    self._sessions[s.notebook] = s
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
                "active": (s.notebook in self._in_use()) if self.in_process else True,
                "shared_with": self.guests(s.notebook) if s.notebook else [],
            })
        return sorted(rows, key=lambda r: -r["last_used"])
