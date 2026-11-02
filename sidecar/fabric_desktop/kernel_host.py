"""The notebook kernel in a process of its own (``python -m fabric_desktop.kernel_host``).

The engine starts one of these per kernel (kernel_proc.ProcessKernel) and talks to it in JSON
lines over stdin and stdout. It hosts the same Kernel class the engine runs in-process, so cells
behave the same; what changes is that a stuck, crashed or stopped notebook ends only this process.

Requests from the engine: ``{"id", "op", "args"}``, answered with ``{"id", "result"}`` or
``{"id", "error": {"type", "message"}}``. Each request is handled on its own thread, so Cancel
reaches a busy kernel.

The kernel asks the engine for what only the engine has (a token from the person's sign-in,
another notebook's cells, a variable library, whether a table is managed by the metadata
framework) with ``{"id", "call", "args"}``, answered with ``{"id", "result"}`` or ``{"id", "error"}``.

The protocol has stdout to itself: anything else written to file descriptor 1 (print from a
background thread, a native library) goes to stderr, which the engine logs.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any


class Channel:
    """Requests in, answers and calls out, over the two file objects the engine holds."""

    def __init__(self, inp: Any, out: Any):
        self.inp, self.out = inp, out
        self.lock = threading.Lock()
        self.waiting: dict[str, dict[str, Any]] = {}
        self.ids = itertools.count(1)

    def send(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, default=str)
        with self.lock:
            self.out.write(line + "\n")
            self.out.flush()

    def call(self, name: str, *args: Any, timeout: float = 600.0) -> Any:
        """Ask the engine for something and wait for its answer."""
        cid = f"c{next(self.ids)}"
        slot: dict[str, Any] = {"done": threading.Event()}
        self.waiting[cid] = slot
        self.send({"id": cid, "call": name, "args": list(args)})
        if not slot["done"].wait(timeout):
            self.waiting.pop(cid, None)
            raise TimeoutError(f"The app didn't answer {name} in time.")
        if "error" in slot:
            raise RuntimeError(slot["error"].get("message", "The app couldn't answer."))
        return slot.get("result")

    def answer(self, message: dict[str, Any]) -> None:
        slot = self.waiting.pop(message["id"], None)
        if slot is not None:
            slot.update({k: v for k, v in message.items() if k in ("result", "error")})
            slot["done"].set()


class Host:
    """The kernel and what the engine can ask of it."""

    def __init__(self, channel: Channel):
        from . import lakehouse
        from .notebook import Kernel
        self.channel = channel
        self.lakehouse = lakehouse
        self.kernel = Kernel(token=lambda audience: channel.call("token", audience),
                             load_notebook=lambda name: channel.call("load_notebook", name),
                             variable_libraries=lambda name: channel.call("variable_libraries", name))
        self.kernel.engine_call = lambda name, *args: channel.call(name, *args)  # %pip install
        self.proof: Any = None

    # ---- requests

    def start(self, code: str, notebook_name: str = "", watches: list[str] | None = None,
              widget_values: dict[str, str] | None = None, resources_dir: str = "",
              onelake_writes: str = "local_first", breakpoints: list[int] | None = None, step: bool = False) -> dict[str, Any]:
        self.kernel.check_free()  # a shared session's running cell keeps its notebook's name and widgets
        self.kernel.notebook_name = notebook_name
        self.kernel.widget_values = dict(widget_values or {})
        self.kernel.resources_dir = resources_dir
        self.kernel.onelake_writes = onelake_writes
        return self.kernel.start(code, watches, breakpoints, step)

    def debug(self, action: str, expression: str | None = None) -> dict[str, Any]:
        return self.kernel.debug(action, expression)

    def poll(self, version: int = -1, wait: float = 0.0) -> dict[str, Any]:
        return self.kernel.poll(version, wait)

    def interrupt(self) -> dict[str, Any]:
        return self.kernel.interrupt()

    def variables(self) -> list[dict[str, Any]]:
        return self.kernel.variables()

    def complete(self, code: str, cursor: int | None = None) -> dict[str, Any]:
        return self.kernel.complete(code, cursor)

    def inspect(self, code: str, cursor: int | None = None, call: bool = False) -> dict[str, Any] | None:
        return self.kernel.inspect(code, cursor, call)

    def variable_detail(self, name: str) -> dict[str, Any]:
        return self.kernel.variable_detail(name)

    def attach(self, context: dict[str, Any] | None, guard: bool = False) -> bool:
        """The engine's attached lakehouse, with tokens and the managed-table check asked of the engine."""
        lh = self.lakehouse
        if context is None:
            lh.attach(None)
        else:
            schemas = bool(context.get("schemas_enabled", True))
            lh.attach(lh.Attached(
                workspace_id=context["workspace_id"], lakehouse_id=context["lakehouse_id"], name=context["name"],
                workspace_name=context.get("workspace_name") or "",
                storage_token=lambda: self.channel.call("token", "storage"),
                work_dir=Path(context["work_dir"]),
                history_file=Path(context["history_file"]) if context.get("history_file") else None,
                schemas_enabled=lambda: schemas))
        lh.write_guard = (lambda name, schema, table: self.channel.call("write_guard", name, schema, table)) if guard else None
        return True

    def bind_proof(self, config: dict[str, Any], side: str, root: str) -> bool:
        from .proof_runtime import ProofRuntime
        options = lambda: self.channel.call("proof_options")  # noqa: E731 - fresh tokens on each use
        self.proof = ProofRuntime(config, side).bind(root, options)
        self.lakehouse.proof = self.proof
        return True

    def unbind_proof(self) -> bool:
        self.lakehouse.proof = None
        if self.proof is not None:
            self.proof.restore()
        return True

    def finish_proof(self, modules: list[str]) -> dict[str, Any]:
        log = self.proof.finish(modules)
        self.proof = None
        return log

    def proof_log(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.proof.log)) if self.proof is not None else {}

    def reads(self) -> dict[str, Any]:
        return self.kernel.reads()

    def frame_page(self, **args: Any) -> dict[str, Any]:
        return self.kernel.frame_page(**args)

    def frame_chart(self, **args: Any) -> dict[str, Any]:
        return self.kernel.frame_chart(**args)

    def frame_profile(self, **args: Any) -> dict[str, Any]:
        return self.kernel.frame_profile(**args)

    def frame_wrangle(self, **args: Any) -> dict[str, Any]:
        return self.kernel.frame_wrangle(**args)

    def frame_query_profile(self, **args: Any) -> dict[str, Any]:
        return self.kernel.frame_query_profile(**args)

    def ipywidget_event(self, **args: Any) -> dict[str, Any]:
        return self.kernel.ipywidget_event(**args)

    def frame_download(self, **args: Any) -> dict[str, Any]:
        return self.kernel.frame_download(**args)

    def forget_guest(self, notebook: str) -> dict[str, Any]:
        return self.kernel.forget_guest(notebook)

    def ping(self) -> dict[str, Any]:
        return {"pid": os.getpid(), "python": sys.version.split()[0]}

    OPS = ("start", "poll", "interrupt", "debug", "variables", "variable_detail", "complete", "inspect", "attach", "bind_proof", "unbind_proof",
           "finish_proof", "proof_log", "reads", "frame_page", "frame_profile", "frame_chart", "frame_wrangle", "frame_query_profile", "ipywidget_event", "frame_download", "forget_guest", "ping")

    def handle(self, message: dict[str, Any]) -> None:
        mid = message.get("id")
        try:
            op = message.get("op")
            if op not in self.OPS:
                raise ValueError(f"Unknown request {op!r}")
            args = dict(message.get("args") or {})
            # A notebook sharing this session (kernel_sessions.share) asks with its name: what the
            # request reads or runs is that notebook's own variables.
            with self.kernel.as_guest(args.pop("guest", None)):
                result = getattr(self, op)(**args)
            self.channel.send({"id": mid, "result": result})
        except BaseException as exc:  # noqa: BLE001 - every request gets an answer
            self.channel.send({"id": mid, "error": {"type": type(exc).__name__, "message": str(exc)}})
            if not isinstance(exc, Exception):
                print(traceback.format_exc(), file=sys.stderr)


def main() -> None:
    from . import net_trust
    net_trust.use_system_store()  # a notebook's own HTTPS too (net_trust.py)
    # Side by side's core cap, before any library starts a thread pool (cpu_cap.py).
    from . import cpu_cap
    cpu_cap.from_env()
    # A notebook environment's packages come after the engine's, so they add to it and never
    # replace what the engine ships (environments.py).
    for folder in filter(None, os.environ.get("FABRIC_DESKTOP_LIBRARY_PATHS", "").split(os.pathsep)):
        if folder not in sys.path:
            sys.path.append(folder)
    # Keep the protocol's pipes for ourselves; give everything else stderr and an empty stdin.
    proto_in = os.fdopen(os.dup(0), "r", encoding="utf-8")
    proto_out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    sys.stdout = os.fdopen(1, "w", encoding="utf-8", errors="replace", buffering=1)
    sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115 - for the life of the process
    channel = Channel(proto_in, proto_out)
    host = Host(channel)
    channel.send({"id": "ready", "result": host.ping()})
    for line in proto_in:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            print(f"kernel_host: not JSON: {line[:200]}", file=sys.stderr)
            continue
        if "op" in message:
            threading.Thread(target=host.handle, args=(message,), daemon=True, name=f"request-{message.get('op')}").start()
        else:
            channel.answer(message)
    # The engine closed our stdin: it is gone or wants us gone.
    os._exit(0)


if __name__ == "__main__":
    main()
