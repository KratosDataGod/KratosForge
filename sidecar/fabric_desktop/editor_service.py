"""Run cells from VS Code (docs/vscode.md): the app's side of paired editor windows.

The person switches "VS Code can run notebooks" on in Settings > Integrations; the engine then
listens on the editor channel (editor_channel.py). A VS Code window asks to pair, the app shows the
request (editor_pending) and the person pairs it or not (editor_decide_pairing). After that, the
window runs cells in the notebook's own session on this PC without asking each time, as the person
does in the app: the same kernel, variables, lakehouse and limits.

Writing to OneLake from such a cell still asks in the app, every time (editor_decide_write): a
table or file write waits up to five minutes for an answer, and one approval covers that table for
the rest of the cell. It is a check on KratosForge's write paths, not a sandbox: the code runs as
the person, as their own cells do.

AI tools are not paired windows: they keep asking before every run (integrations/).
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from typing import Any

from .editor_channel import Channel
from .editor_pairing import Pairings
from .jsonfile import read_json, write_json

METHODS = {"editor_status", "editor_set_enabled", "editor_pending", "editor_decide_pairing", "editor_decide_write",
           "editor_end_pairing"}

WRITE_WAIT_SECONDS = 300
FROM_EDITOR = "VS Code"


def _inside(path: str, folder: str) -> bool:
    """Whether ``path`` is ``folder`` or below it, by text alone (no file system call)."""
    if not isinstance(path, str) or not folder or "\x00" in path:
        return False
    top = os.path.normcase(os.path.abspath(folder)).rstrip("\\/")
    return os.path.normcase(os.path.abspath(path)).startswith(top + os.sep)


def _is_root(folder: str) -> bool:
    """A drive or share root (C:\\, /, \\\\host\\share), which would cover every notebook on it."""
    full = os.path.abspath(folder)
    drive, rest = os.path.splitdrive(full)
    return os.path.dirname(full) == full or rest.strip("\\/") == ""


class _WriteAsk:
    def __init__(self, window: str, notebook: str, target: str):
        self.id = "wq_" + secrets.token_hex(8)
        self.window, self.notebook, self.target = window, notebook, target
        self.at = time.time()
        self.answer: bool | None = None
        self.done = threading.Event()

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "window": self.window, "notebook": self.notebook, "target": self.target, "at": self.at}


class EditorMixin:
    # ---- state

    @property
    def editor_pairings(self) -> Pairings:
        pairings = getattr(self, "_editor_pairings", None)
        if pairings is None:
            pairings = self._editor_pairings = Pairings(self.root / "editor")  # type: ignore[attr-defined]
        return pairings

    def _editor_state(self) -> dict[str, Any]:
        state = getattr(self, "_editor", None)
        if state is None:
            state = self._editor = {"lock": threading.Lock(), "channel": None, "asks": {}}
        return state

    def _editor_settings_path(self) -> Any:
        return self.root / "editor" / "settings.json"  # type: ignore[attr-defined]

    def _editor_allowed(self) -> str:
        """Why the organization keeps this off, or "" when the person decides."""
        from .integrations import policy
        rules = policy.current()
        if rules.get("enabled") is False:
            return "Your organization has turned integrations off on this PC."
        if rules.get("editors") is False:
            return "Your organization doesn't let editors such as VS Code run notebooks on this PC."
        return ""

    def _editor_enabled(self) -> bool:
        saved = read_json(self._editor_settings_path(), {})
        return bool(isinstance(saved, dict) and saved.get("enabled")) and not self._editor_allowed()

    # ---- the app (Settings > Integrations, and the requests it shows)

    def editor_sync(self) -> None:
        """Listen on the editor channel exactly while it's switched on and allowed."""
        state = self._editor_state()
        with state["lock"]:
            on = self._editor_enabled()
            if on and state["channel"] is None:
                channel = Channel(self, self.root)  # type: ignore[attr-defined]
                channel.start()
                state["channel"] = channel
            elif not on and state["channel"] is not None:
                state["channel"].stop()
                state["channel"] = None

    def editor_channel_open(self) -> bool:
        """For each channel call: is it still switched on and allowed? When the organization has
        turned it off since, every pairing ends and the channel stops (in the background, as this
        runs on one of its own threads)."""
        if self._editor_enabled():
            return True
        state = self._editor_state()
        with state["lock"]:
            start, state["stopping"] = not state.get("stopping"), True
        if start:  # once, however many refused calls arrive meanwhile
            threading.Thread(target=self._editor_turned_off, daemon=True, name="editor-off").start()
        return False

    def _editor_turned_off(self) -> None:
        try:
            self.editor_pairings.end_all()
            self._editor_deny_all()
            self.editor_sync()
        finally:
            self._editor_state()["stopping"] = False

    def editor_status(self) -> dict[str, Any]:
        if self._editor_state()["channel"] is not None and not self._editor_enabled():
            self._editor_turned_off()  # the policy changed while it listened
        channel = self._editor_state()["channel"]
        return {"enabled": self._editor_enabled(), "blocked": self._editor_allowed(),
                "listening": channel is not None, "port": channel.port if channel else None,
                "pairings": self.editor_pairings.list()}

    def editor_set_enabled(self, enabled: bool) -> dict[str, Any]:
        if enabled and self._editor_allowed():
            raise PermissionError(self._editor_allowed())
        write_json(self._editor_settings_path(), {"enabled": bool(enabled)})
        if not enabled:
            self.editor_pairings.end_all()  # switching it off ends every pairing
            self._editor_deny_all()
        self.editor_sync()
        return self.editor_status()

    def editor_pending(self) -> dict[str, Any]:
        if self._editor_state()["channel"] is not None and not self._editor_enabled():
            self._editor_turned_off()
        asks = self._editor_state()["asks"]
        return {"enabled": self._editor_enabled(), "pairing": self.editor_pairings.waiting(),
                "writes": [a.view() for a in list(asks.values()) if not a.done.is_set()]}

    def editor_decide_pairing(self, request_id: str, approve: bool, keep: str = "session") -> dict[str, Any]:
        return self.editor_pairings.decide(request_id, bool(approve), keep)

    def editor_decide_write(self, ask_id: str, approve: bool) -> dict[str, Any]:
        ask = self._editor_state()["asks"].get(ask_id)
        if ask is None or ask.done.is_set():
            raise LookupError("That write isn't waiting any more: the cell stopped or the time ran out.")
        ask.answer = bool(approve)
        ask.done.set()
        return {"approved": ask.answer}

    def editor_end_pairing(self, pairing_id: str) -> dict[str, Any]:
        return {"ended": self.editor_pairings.end(pairing_id)}

    def _editor_deny_all(self) -> None:
        for ask in list(self._editor_state()["asks"].values()):
            if not ask.done.is_set():
                ask.answer = False
                ask.done.set()

    # ---- the editor channel's calls (editor_channel.py), from a paired window

    def editor_channel_pair(self, window: str | None, client: str | None) -> dict[str, Any]:
        if not self._editor_enabled():
            raise PermissionError("VS Code can't run notebooks in KratosForge: it's switched off in Settings > Integrations.")
        if not isinstance(window, str) or not window.strip() or _is_root(window):
            raise PermissionError("Pair from a folder with notebooks in it, not a whole drive or no folder: "
                                  "open the notebooks folder or repository in VS Code first.")
        out = self.editor_pairings.request(window, client)
        self._editor_audit("editor_pair_request", {"window": window or ""}, {"result": out})
        return out

    def editor_channel_collect(self, request_id: str) -> dict[str, Any]:
        return self.editor_pairings.collect(request_id)

    def editor_channel_unpair(self, pairing: dict[str, Any]) -> dict[str, Any]:
        self.editor_pairings.end(pairing["id"])
        self._editor_audit("editor_unpair", {"window": pairing["window"]}, {"result": True})
        return {"ended": True}

    def editor_channel_notebook(self, pairing: dict[str, Any], path: str) -> str:
        """The notebook a file is, for a paired window: only a file inside the folder the window
        paired from (checked as text first, as notebook_name_for_path does), and only one the app knows."""
        if not _inside(path, pairing.get("window") or ""):
            raise PermissionError(f"{path} isn't in {pairing.get('window')}, the folder this VS Code window was paired "
                                  "from. Pair the window that has it open.")
        name = self.notebook_name_for_path(path)["name"]  # type: ignore[attr-defined]
        if name is None:
            raise LookupError(f"{path} isn't in KratosForge's notebooks folder or a notebooks repository it has open. "
                              "Open the repository in Source control, or import the notebook.")
        return name

    def editor_channel_run(self, pairing: dict[str, Any], path: str, code: str) -> dict[str, Any]:
        if not self._editor_enabled():
            raise PermissionError("VS Code can't run notebooks in KratosForge: it's switched off in Settings > Integrations.")
        name = self.editor_channel_notebook(pairing, path)
        started = time.monotonic()
        reply: dict[str, Any]
        try:
            check = self._editor_write_check(pairing["window"], name)
            ref = self._editor_lakehouse(name)
            snap = self._start_cell(code, ref, name, write_check=check)  # type: ignore[attr-defined]
            reply = {"result": snap}
            return snap | {"notebook": name}
        except Exception as exc:
            reply = {"error": str(exc)}
            raise
        finally:
            self._editor_audit("editor_run", {"notebook": name, "window": pairing["window"]}, reply,
                               time.monotonic() - started)

    def editor_channel_poll(self, pairing: dict[str, Any], path: str, version: int, wait: float) -> dict[str, Any]:
        return self.poll_cell(version, wait, self.editor_channel_notebook(pairing, path))  # type: ignore[attr-defined]

    def editor_channel_interrupt(self, pairing: dict[str, Any], path: str) -> dict[str, Any]:
        name = self.editor_channel_notebook(pairing, path)
        out = self.interrupt_cell(name)  # type: ignore[attr-defined]
        self._editor_audit("editor_interrupt", {"notebook": name, "window": pairing["window"]}, {"result": out})
        return out

    def editor_channel_variables(self, pairing: dict[str, Any], path: str) -> list[dict[str, Any]]:
        return self.kernel_variables(self.editor_channel_notebook(pairing, path))  # type: ignore[attr-defined]

    # ----

    def _editor_lakehouse(self, name: str) -> dict[str, str] | None:
        try:
            ref = self.notebooks.load(name).get("lakehouse")  # type: ignore[attr-defined]
        except (OSError, KeyError, ValueError):
            return None
        return ref if isinstance(ref, dict) and ref.get("workspace_id") and ref.get("lakehouse_id") else None

    def _editor_write_check(self, window: str, notebook: str) -> Any:
        """The check every OneLake write in this cell passes: the person says yes in the app, once per table."""
        state = self._editor_state()
        approved: set[str] = set()

        def check(name: str, schema: str, table: str) -> str | None:
            target = f"{name}/{table}" if schema == "Files" else ".".join(p for p in (name, schema, table) if p)
            if target in approved:
                return None
            ask = _WriteAsk(window, notebook, target)
            state["asks"][ask.id] = ask
            try:
                answered = ask.done.wait(WRITE_WAIT_SECONDS)
            finally:
                state["asks"].pop(ask.id, None)
            if answered and ask.answer:
                approved.add(target)
                self._editor_audit("editor_write_approved", {"notebook": notebook, "target": target}, {"result": True})
                return None
            self._editor_audit("editor_write_denied", {"notebook": notebook, "target": target}, {"error": "denied"})
            if not answered:
                return (f"Nobody answered in KratosForge within {WRITE_WAIT_SECONDS // 60} minutes, so {target} "
                        "wasn't written. Writes from VS Code ask in KratosForge each time.")
            return f"Writing {target} from VS Code was denied in KratosForge."
        return check

    def _editor_audit(self, action: str, params: dict[str, Any], reply: dict[str, Any], seconds: float = 0.0) -> None:
        from .server import _audit
        _audit(self, action, params, reply, seconds)  # type: ignore[arg-type]
