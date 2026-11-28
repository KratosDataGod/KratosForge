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

import dis
import inspect
import queue
import sys
import threading
import time
import types
from typing import Any, Callable

ACTIONS = ("continue", "step", "next", "out")
# A generator or coroutine "returns" each time it yields or awaits, not only when it ends.
SUSPENDS = inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR | inspect.CO_ITERABLE_COROUTINE
# The instructions a frame really returns with; any other "return" event of a generator or
# coroutine is it suspending at a yield or an await (YIELD_VALUE on 3.12, the RESUME after it on 3.13).
_RETURNS = frozenset(dis.opmap[name] for name in ("RETURN_VALUE", "RETURN_CONST") if name in dis.opmap)
CELL_FILE = "<cell>"  # the file name cells are compiled with (notebook._CELL_FILE)
MAX_BREAKPOINTS = 100
MAX_LOCALS = 200
MAX_SKIPPED = 5  # names of stepped-over functions reported with a pause
EVAL_SECONDS = 10.0
MAX_EVALS = 20  # evaluations a pause keeps, the latest ones
MAX_EXPRESSION = 2000  # characters in an expression to evaluate (the panel's box takes 500)
# Describing a frame's variables runs their own code (repr, len): past this, the rest are listed by
# name and type only, so a frame full of slow values still pauses quickly.
DESCRIBE_SECONDS = 3.0
# Before 3.13 (PEP 667), a function's f_locals is a snapshot that each read refreshes from the frame and
# that Python writes back to it as the trace hook returns: see Debugger._scope.
_SNAPSHOT_LOCALS = sys.version_info < (3, 13)
_UNSET = object()


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
        self.asked = list(lines)  # the breakpoints as set, before add() moves them onto code
        self.lines = set(lines)
        self.unplaced: list[int] = []  # breakpoints with no code at or after them in the cell
        self._code_lines: set[int] = set()
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
        self._raising: types.FrameType | None = None  # the frame an exception is leaving, if any
        self._tail: types.CodeType | None = None  # the cell's last expression (add)
        self._top_line: int | None = None  # the line the cell's own frame last ran
        self._from: tuple[types.FrameType, int] | None = None  # where the last step or next started
        self._scopes: dict[types.FrameType, dict[str, Any]] = {}  # a cell or class body's own namespace
        self.stopping = False  # Stop was asked for: its interrupt ends an expression instead of being its answer

    # ---- set up by the kernel, on the cell's thread

    def add(self, code: types.CodeType, tail: bool = False) -> None:
        """Trace ``code`` and every function, class body and comprehension compiled with it.
        ``tail``: the cell's last expression, compiled apart from the rest so its value shows. When it
        shares a line with the statement before it (``y = 2; y``), that line already ran its line
        event, so it isn't a second place to stop: Continue from it never stops on it again."""
        if tail:
            self._tail, self._top_line = code, None
        todo = [code]
        while todo:
            c = todo.pop()
            if c not in self.codes:
                self.codes.add(c)
                self._code_lines.update(n for _, _, n in c.co_lines() if n)
                todo += [k for k in c.co_consts if isinstance(k, types.CodeType)]
        # A breakpoint on a line with no code (blank, a comment, ``else:``) pauses at the next line that has some.
        placed = {n: min((m for m in self._code_lines if m >= n), default=None) for n in self.asked}
        self.lines = {m for m in placed.values() if m is not None}
        self.unplaced = [n for n, m in placed.items() if m is None]

    def trace(self, frame: types.FrameType, event: str, arg: Any) -> Any:
        """sys.settrace on the cell's thread: only this cell's frames get the line tracer."""
        if frame.f_code not in self.codes:
            if event == "call" and self.mode == "step" and frame.f_back is not None and frame.f_back.f_code in self.codes:
                self._note_skipped(frame)
            return None
        if event == "call" and not frame.f_code.co_flags & inspect.CO_OPTIMIZED:
            # The cell's or a class body's own namespace, read before anything runs in it. Later, on 3.12,
            # reading f_locals while an inlined comprehension runs there lets the trace hook write None (or
            # the outer value) over the comprehension's variable as it returns, changing what the cell computes.
            self._scopes[frame] = frame.f_globals if frame.f_code.co_name == "<module>" else frame.f_locals
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
        if event == "exception":
            self._raising = frame  # unwinding: its return event isn't a step's place to stop
        elif event == "line":
            self._raising = None  # caught, and running on
            if frame.f_code is self._tail:
                if frame.f_lineno == self._top_line:
                    return self._line  # the line the cell's body ended on, already run (add)
            elif frame.f_code.co_name == "<module>":
                self._top_line = frame.f_lineno
        elif event == "return":
            self._scopes.pop(frame, None)
            if self._from is not None and self._from[0] is frame:
                self._from = None
        if event == "line" and self._stops(frame):
            self._pause(frame, "breakpoint" if frame.f_lineno in self.lines and self.mode == "continue" else "step")
        elif event == "return" and self._stops_on_return(frame):
            self._pause(frame, "step")
        return self._line

    def _stops_on_return(self, frame: types.FrameType) -> bool:
        """A function that a step came back into returns without running another line, as in
        ``return n * fact(n - 1)``: the step stops as it leaves, so the step never runs on to the end.
        The cell's own frame is left alone, so a step past the cell's last line ends the cell. So is a
        generator or coroutine: its return event is mostly a yield or an await giving way to the event
        loop, and a pause there would show it at a line it hasn't left; the step stops at the next line."""
        if (self.mode == "continue" or frame is self._raising or frame.f_code.co_name in ("<module>", "<cell>")
                or _suspends(frame)):
            return False
        return self._depth(frame) < self.depth

    def _depth(self, frame: types.FrameType | None) -> int:
        n = 0
        while frame is not None:
            n += frame.f_code in self.codes
            frame = frame.f_back
        return n

    def _stops(self, frame: types.FrameType) -> bool:
        if frame.f_lineno in self.lines:
            return True
        if self.mode in ("step", "next") and self._from == (frame, frame.f_lineno):
            return False  # a comprehension or a one-line loop going round: still the line the step started on
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
        for name, value in list(self._scope(frame).items()):
            if name.startswith("__") or (at_top and name in self.skip):
                continue
            if at_top and (isinstance(value, (types.ModuleType, type)) or callable(value)):
                continue
            shown.append((name, value))
        shown.sort(key=lambda nv: nv[0].lower())
        deadline = time.monotonic() + DESCRIBE_SECONDS
        rows = [self._row(name, value) if time.monotonic() < deadline else _unread(name, value)
                for name, value in shown[:MAX_LOCALS]]
        return rows, len(shown)

    def _evaluate(self, frame: types.FrameType, expr: str, assigned: dict[str, Any] | None = None) -> dict[str, Any]:
        frame_had = dict(frame.f_locals) if assigned is not None else {}  # the frame's own values, without the overlay
        scope = self._scope(frame, assigned)
        before = dict(scope) if assigned is not None else {}
        try:
            value = eval(compile(expr, "<debug>", "eval"), frame.f_globals, scope)  # noqa: S307 - the person's own notebook code
        except BaseException as exc:  # noqa: BLE001 - exit(), KeyboardInterrupt and the like too: shown next to the expression
            if self.stopping:
                raise  # Stop's interrupt, landing in an expression that ran on: it ends the cell
            return {"expr": expr, "error": self.redact(f"{type(exc).__name__}: {exc}")}
        finally:
            if assigned is not None:  # what the expression assigned itself, (n := 5), kept on top of the frame
                wrote = {k: v for k, v in scope.items() if k not in before or before[k] is not v}
                frame_has = frame.f_locals  # refreshed (the same dict as scope): a nonlocal a called function changed
                for name in [k for k in assigned if frame_has.get(k, _UNSET) is not frame_had.get(k, _UNSET)]:
                    del assigned[name]  # the frame's newer value wins over an earlier expression's
                assigned.update(wrote)
                self._scope(frame, assigned)
        return {"expr": expr, **self._row(expr, value)}

    def _scope(self, frame: types.FrameType, assigned: dict[str, Any] | None = None) -> dict[str, Any]:
        """The names to show and evaluate in: a function's locals, or a cell's or class body's own namespace
        (without the variables of an inlined comprehension running in it, see trace).
        ``assigned``: before 3.13, what this pause's expressions assigned in a function's snapshot of its
        locals. Each read of f_locals refreshes the snapshot from the frame, which would undo them before
        Python writes the snapshot back as the pause ends, so they are put back after each read."""
        if frame.f_code.co_flags & inspect.CO_OPTIMIZED:
            scope = frame.f_locals
            if assigned:
                scope.update(assigned)
            return scope
        return self._scopes.get(frame, frame.f_globals)

    def _row(self, name: str, value: Any) -> dict[str, Any]:
        row = self.describe(name, value)
        row["value"] = self.redact(row["value"])
        return row

    def _pause(self, frame: types.FrameType, reason: str) -> None:
        variables, total = self._variables(frame)
        assigned: dict[str, Any] | None = {} if _SNAPSHOT_LOCALS and frame.f_code.co_flags & inspect.CO_OPTIMIZED else None
        self.paused = {"line": frame.f_lineno, "reason": reason, "stack": self._stack(frame),
                       "variables": variables,
                       "watches": [self._evaluate(frame, w, assigned) for w in self.watches], "evals": []}
        if total > len(variables):
            self.paused["variables_total"] = total
        if self.skipped:
            self.paused["skipped"] = list(self.skipped)
            self.skipped = []
        self.on_change(self.paused)
        try:
            while True:
                if self.stopping:
                    raise Stopped()
                try:
                    action, arg = self.commands.get(timeout=0.1)  # short waits, so an interrupt still lands
                except queue.Empty:
                    continue
                if action == "stop":
                    raise Stopped()
                if action == "eval":
                    expr, slot = arg
                    result = self._evaluate(frame, expr, assigned)
                    self.paused["evals"] = (self.paused["evals"] + [result])[-MAX_EVALS:]
                    slot["result"] = result
                    slot["done"].set()
                    self.on_change(self.paused)
                    continue
                self.mode = action
                self.depth = self._depth(frame)
                self._from = (frame, frame.f_lineno)
                return
        finally:
            self.paused = None
            self.on_change(None)
            self.resumed.set()

    def pause_here(self, frame: types.FrameType) -> bool:
        """``breakpoint()`` in the cell's own code: a pause on its line. False when it isn't the cell's code
        or the cell is already paused (a breakpoint() in an expression being evaluated)."""
        if frame.f_code not in self.codes or self.paused is not None:
            return False
        self.skipped = []  # the hook breakpoint() called isn't a step over
        self._pause(frame, "breakpoint")
        return True

    def lost(self) -> bool:
        """Python's trace hook on this thread is no longer this debugger's: a RecursionError turned it off,
        or the cell set its own with sys.settrace."""
        return sys.gettrace() != self.trace

    # ---- commands, from the engine's request threads

    def command(self, action: str, expression: str | None = None) -> dict[str, Any]:
        if self.paused is None:
            raise RuntimeError("The cell isn't paused.")
        if not isinstance(action, str):
            raise ValueError("A debugger action is text: continue, step, next, out or eval.")
        if action == "eval":
            if not isinstance(expression, str) or not expression.strip():
                raise ValueError("Type an expression to evaluate.")
            if len(expression) > MAX_EXPRESSION:
                raise ValueError(f"An expression to evaluate is at most {MAX_EXPRESSION} characters.")
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
        self.stopping = True
        if self.paused is None:
            return False
        self.commands.put(("stop", None))
        return True


def _unread(name: str, value: Any) -> dict[str, Any]:
    """A variable left unread because describing the ones before it took DESCRIBE_SECONDS."""
    return {"name": name, "type": type(value).__name__, "size": "", "value": "?", "memory": None, "expandable": False}


def _suspends(frame: types.FrameType) -> bool:
    """A generator's or coroutine's "return" event that is it yielding or awaiting, not ending."""
    if not frame.f_code.co_flags & SUSPENDS:
        return False
    code = frame.f_code.co_code
    return not (0 <= frame.f_lasti < len(code) and code[frame.f_lasti] in _RETURNS)
