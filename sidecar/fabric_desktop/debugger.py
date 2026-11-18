"""Breakpoints and stepping in a notebook cell (Prove stage 5, item 44).

A cell started with breakpoints (or with ``step`` to pause at its first line) runs under a trace
function on its own thread. Only code compiled from that cell is traced: functions and classes it
defines, wherever they are called from in it. Library code, other notebooks run with ``%run`` and
threads the cell starts run at full speed, and a breakpoint never stops them.

When the cell reaches a breakpoint or finishes a step it pauses: the cell's thread waits for the
next command, and ``poll`` reports the paused line, the call stack, the frame's variables (names,
types, sizes and short previews, with secrets hidden as in the Variables pane) and the watches
evaluated in that frame. Commands: continue, step (into calls), next (over calls), out (to the
caller) and eval (an expression in the paused frame, on the cell's thread). Cancel ends a paused
cell like a running one, keeping variables.

Evaluating an expression runs the person's own code, like the cell itself.

Step into never goes into code that isn't this cell's: a library function, or a function another
cell defined, runs as one step. The pause after it names what was stepped over (``skipped``), so
the step isn't a silent "over". A frame with more than MAX_LOCALS variables shows the first
MAX_LOCALS by name and says how many there are (``variables_total``).
"""

from __future__ import annotations

import queue
import threading
import types
from typing import Any, Callable

ACTIONS = ("continue", "step", "next", "out")
CELL_FILE = "<cell>"  # the file name cells are compiled with (notebook._CELL_FILE)
MAX_BREAKPOINTS = 100
MAX_LOCALS = 200
MAX_SKIPPED = 5  # names of stepped-over functions reported with a pause
EVAL_SECONDS = 10.0


def check_breakpoints(lines: Any) -> list[int]:
    """Breakpoint line numbers from the app: positive whole numbers, at most MAX_BREAKPOINTS."""
    if lines is None:
        return []
    if not isinstance(lines, list) or len(lines) > MAX_BREAKPOINTS:
        raise ValueError(f"Breakpoints must be a list of at most {MAX_BREAKPOINTS} line numbers.")
    out = []
    for n in lines:
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise ValueError("A breakpoint must be a line number, 1 or more.")
        out.append(n)
    return sorted(set(out))


class Stopped(KeyboardInterrupt):
    """Cancel while paused: ends the cell the way Cancel ends a running one."""


class Debugger:
    def __init__(self, lines: list[int], step: bool, describe: Callable[[str, Any], dict[str, Any]],
                 redact: Callable[[str], str] = lambda text: text, watches: list[str] | None = None, on_change: Callable[[dict[str, Any] | None], None] = lambda p: None):
        self.lines = set(lines)
        self.mode = "step" if step else "continue"
        self.depth = 0  # call depth (in this cell's code) where the last pause was
        self.codes: set[types.CodeType] = set()
        self.describe = describe  # (name, value) -> a Variables pane row, secrets hidden
        self.redact = redact  # secrets out of error text
        self.watches = list(watches or [])
        self.on_change = on_change  # the run's paused state changed (a dict while paused, None after)
        self.commands: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.paused: dict[str, Any] | None = None
        self.resumed = threading.Event()  # set when the cell's thread leaves a pause
        self.skip: set[str] = set()  # names not shown as variables (the kernel's own)
        self.skipped: list[str] = []  # functions Step into didn't enter since the last pause

    # ---- set up by the kernel, on the cell's thread

    def add(self, code: types.CodeType) -> None:
        """Trace ``code`` and every function, class body and comprehension compiled with it."""
        todo = [code]
        while todo:
            c = todo.pop()
            if c not in self.codes:
                self.codes.add(c)
                todo += [k for k in c.co_consts if isinstance(k, types.CodeType)]

    def trace(self, frame: types.FrameType, event: str, arg: Any) -> Any:
        """sys.settrace on the cell's thread: only this cell's frames get the line tracer."""
        if frame.f_code not in self.codes:
            if event == "call" and self.mode == "step" and frame.f_back is not None and frame.f_back.f_code in self.codes:
                self._note_skipped(frame)
            return None
        return self._line

    def _note_skipped(self, frame: types.FrameType) -> None:
        """Step into reached a call it won't enter: remember its name for the next pause."""
        code = frame.f_code
        if code.co_filename == CELL_FILE:
            name = f"{code.co_name} (defined in another cell)"
        else:
            module = frame.f_globals.get("__name__") if isinstance(frame.f_globals, dict) else None
            name = f"{module}.{getattr(code, 'co_qualname', code.co_name)}" if isinstance(module, str) and module else code.co_name
        if name not in self.skipped and len(self.skipped) < MAX_SKIPPED:
            self.skipped.append(name)

    # ---- the trace

    def _line(self, frame: types.FrameType, event: str, arg: Any) -> Any:
        if event == "line" and self._stops(frame):
            self._pause(frame, "breakpoint" if frame.f_lineno in self.lines and self.mode == "continue" else "step")
        return self._line

    def _depth(self, frame: types.FrameType | None) -> int:
        n = 0
        while frame is not None:
            n += frame.f_code in self.codes
            frame = frame.f_back
        return n

    def _stops(self, frame: types.FrameType) -> bool:
        if frame.f_lineno in self.lines:
            return True
        if self.mode == "step":
            return True
        if self.mode == "next":
            return self._depth(frame) <= self.depth
        if self.mode == "out":
            return self._depth(frame) < self.depth
        return False

    def _stack(self, frame: types.FrameType | None) -> list[dict[str, Any]]:
        out = []
        while frame is not None:
            if frame.f_code in self.codes:
                name = frame.f_code.co_name
                out.append({"function": "(cell)" if name in ("<module>", "<cell>") else name, "line": frame.f_lineno})
            frame = frame.f_back
        return out[::-1]

    def _variables(self, frame: types.FrameType) -> tuple[list[dict[str, Any]], int]:
        """The frame's variables, the first MAX_LOCALS by name, and how many there are in all.
        Only the ones shown are described, so a frame full of large values stays quick to pause in."""
        at_top = frame.f_code.co_name == "<module>"
        shown = []
        for name, value in list(frame.f_locals.items()):
            if name.startswith("__") or (at_top and name in self.skip):
                continue
            if at_top and (isinstance(value, (types.ModuleType, type)) or callable(value)):
                continue
            shown.append((name, value))
        shown.sort(key=lambda nv: nv[0].lower())
        return [self._row(name, value) for name, value in shown[:MAX_LOCALS]], len(shown)

    def _evaluate(self, frame: types.FrameType, expr: str) -> dict[str, Any]:
        try:
            value = eval(compile(expr, "<debug>", "eval"), frame.f_globals, frame.f_locals)  # noqa: S307 - the person's own notebook code
        except Exception as exc:  # noqa: BLE001 - shown next to the expression
            return {"expr": expr, "error": self.redact(f"{type(exc).__name__}: {exc}")}
        return {"expr": expr, **self._row(expr, value)}

    def _row(self, name: str, value: Any) -> dict[str, Any]:
        row = self.describe(name, value)
        row["value"] = self.redact(row["value"])
        return row

    def _pause(self, frame: types.FrameType, reason: str) -> None:
        variables, total = self._variables(frame)
        self.paused = {"line": frame.f_lineno, "reason": reason, "stack": self._stack(frame),
                       "variables": variables,
                       "watches": [self._evaluate(frame, w) for w in self.watches], "evals": []}
        if total > len(variables):
            self.paused["variables_total"] = total
        if self.skipped:
            self.paused["skipped"] = list(self.skipped)
            self.skipped = []
        self.on_change(self.paused)
        try:
            while True:
                try:
                    action, arg = self.commands.get(timeout=0.1)  # short waits, so an interrupt still lands
                except queue.Empty:
                    continue
                if action == "stop":
                    raise Stopped()
                if action == "eval":
                    expr, slot = arg
                    result = self._evaluate(frame, expr)
                    self.paused["evals"] = (self.paused["evals"] + [result])[-20:]
                    slot["result"] = result
                    slot["done"].set()
                    self.on_change(self.paused)
                    continue
                self.mode = action
                self.depth = self._depth(frame)
                return
        finally:
            self.paused = None
            self.on_change(None)
            self.resumed.set()

    # ---- commands, from the engine's request threads

    def command(self, action: str, expression: str | None = None) -> dict[str, Any]:
        if self.paused is None:
            raise RuntimeError("The cell isn't paused.")
        if action == "eval":
            if not isinstance(expression, str) or not expression.strip():
                raise ValueError("Type an expression to evaluate.")
            compile(expression, "<debug>", "eval")  # a syntax error is the caller's to see now
            slot: dict[str, Any] = {"done": threading.Event()}
            self.commands.put(("eval", (expression, slot)))
            if not slot["done"].wait(EVAL_SECONDS):
                return {"pending": True}
            return slot["result"]
        if action not in ACTIONS:
            raise ValueError(f"Unknown debugger action {action!r}")
        self.resumed.clear()
        self.commands.put((action, None))
        self.resumed.wait(2.0)  # so the next poll doesn't report the pause just left
        return {"resumed": action}

    def stop(self) -> bool:
        """Cancel while paused; False when the cell isn't paused (interrupt it the usual way)."""
        if self.paused is None:
            return False
        self.commands.put(("stop", None))
        return True
