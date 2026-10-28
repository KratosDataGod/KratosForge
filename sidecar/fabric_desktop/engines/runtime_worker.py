"""The process a separate-runtime engine runs a notebook's cells in (engines/runtimes.py).

Started as ``python -I runtime_worker.py <site-packages> <kind>``, where the folder holds the
runtime's packages (databricks-connect and its own pyspark, for kind ``databricks-connect``). The
folder is added as a site directory, so its .pth files run, and its entries go ahead of the
engine's own site-packages. ``-I`` keeps this file's folder, the user's site-packages and PYTHON*
variables off ``sys.path``, so the engine's modules and its local Spark stand-in can't load here.
This file imports nothing from KratosForge; each kind's start (``STARTS``) is the only part that
differs between runtimes.

Sign-in comes from variables the engine sets (for Databricks Connect, DATABRICKS_*: a CLI profile
name, the host and auth type, or a service principal), read by the platform's SDK. Once the session
has signed in, the kind's secret variables are removed from os.environ, so cells can't read them.

Protocol, one JSON object per line. In: {"op": "start"}, {"op": "exec", "id", "code", "preview_rows"},
{"op": "interrupt"}, {"op": "quit"}. Out: {"op": "started", "client", "python"} or {"op": "error"},
then {"op": "result", "id", "status", "outputs"} per exec. A cell runs in a thread so interrupt can
reach the session (``spark.interruptAll()``) while it runs.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import sys
import threading
import time
import traceback

_OUT = sys.stdout
_SEND = threading.Lock()


def send(message: dict) -> None:
    with _SEND:
        _OUT.write(json.dumps(message, default=str) + "\n")
        _OUT.flush()


def _preview(df, rows: int) -> dict:
    """The first ``rows`` rows of a DataFrame. Counting all of them would run another Spark job."""
    pdf = df.limit(rows).toPandas()
    data = pdf.astype(object).where(pdf.notna(), None).values.tolist()
    return {"type": "frame", "columns": [str(c) for c in pdf.columns], "rows": data}


def _is_frame(value) -> bool:
    return hasattr(value, "toPandas") and hasattr(value, "limit") and hasattr(value, "schema")


def _protocol_lines(stream):
    """Lines from the engine. On Windows a read left waiting on a pipe blocks every other call on
    that pipe (GetFileType, fstat, isatty) until a line comes, so a cell whose imports check stdin
    (pandas does) would hang while the main loop waits. There the loop asks PeekNamedPipe what has
    arrived and reads only that, so no read is ever left waiting."""
    if sys.platform != "win32":
        yield from stream
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes
    peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
    peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    peek.restype = wintypes.BOOL
    fd = stream.fileno()
    handle = msvcrt.get_osfhandle(fd)
    ready = wintypes.DWORD()
    pending = b""
    while True:
        if not peek(handle, None, 0, None, ctypes.byref(ready), None):
            return  # the engine closed its end
        if not ready.value:
            time.sleep(0.02)
            continue
        pending += os.read(fd, ready.value)
        *lines, pending = pending.split(b"\n")
        for line in lines:
            yield line.decode("utf-8", "replace")


def _version(distribution: str) -> str:
    try:
        import importlib.metadata
        return importlib.metadata.version(distribution)
    except Exception:  # noqa: BLE001  # silent-ok: the version only labels the session
        return ""


def _start_databricks_connect():
    """``spark`` as a Databricks session; interrupt stops its Spark jobs on the cluster."""
    import databricks.connect as dbc
    from databricks.connect import DatabricksSession
    spark = DatabricksSession.builder.getOrCreate()
    # databricks.connect has no __version__; the installed distribution says
    client = _version("databricks-connect") or getattr(dbc, "__version__", "")
    return {"spark": spark}, spark.interruptAll, client


# Each kind: how its session starts (names for the cells, how to interrupt, the client version), and
# the variables holding a secret, removed once the session has signed in.
STARTS = {
    "databricks-connect": (_start_databricks_connect,
                           ("DATABRICKS_CLIENT_SECRET", "DATABRICKS_TOKEN", "DATABRICKS_PASSWORD")),
}


class Worker:
    def __init__(self, kind: str = "databricks-connect") -> None:
        self.kind = kind
        self.namespace: dict = {"__name__": "__main__"}
        self.running = False
        self._interrupt = None

    def start(self) -> None:
        if self.kind not in STARTS:
            raise ValueError(f"No runtime of kind {self.kind!r} in this worker.")
        begin, secrets = STARTS[self.kind]
        names, self._interrupt, client = begin()
        # The SDK has read its settings; cells needn't see a secret in os.environ.
        for name in secrets:
            os.environ.pop(name, None)
        shown: list = []
        self.namespace.update({**names, "display": shown.append})
        # stdin carries the engine's messages; a cell's input() gets end-of-file instead of eating them.
        sys.stdin = io.StringIO("")
        self._shown = shown
        send({"op": "started", "client": client,
              "python": "%d.%d" % sys.version_info[:2]})

    def run(self, cid, code: str, rows: int) -> None:
        self.running = True
        self._shown.clear()
        buf = io.StringIO()
        outputs: list = []
        status = "ok"
        try:
            tree = ast.parse(code, "<cell>", "exec")
            last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                exec(compile(tree, "<cell>", "exec"), self.namespace)  # noqa: S102 - the person's own cell
                value = eval(compile(ast.Expression(last.value), "<cell>", "eval"), self.namespace) if last else None
            if buf.getvalue():
                outputs.append({"type": "stream", "text": buf.getvalue()})
            for item in [*self._shown, *([value] if value is not None else [])]:
                outputs.append(_preview(item, rows) if _is_frame(item) else {"type": "text", "text": repr(item)})
        except BaseException as exc:  # noqa: BLE001 - every failure is the cell's error, sent back
            if buf.getvalue():
                outputs.append({"type": "stream", "text": buf.getvalue()})
            status = "error"
            outputs.append({"type": "error", "ename": type(exc).__name__, "evalue": str(exc),
                            "traceback": traceback.format_exception_only(type(exc), exc)})
        finally:
            self.running = False
        send({"op": "result", "id": cid, "status": status, "outputs": outputs})

    def interrupt(self) -> None:
        if self._interrupt is not None and self.running:
            with contextlib.suppress(Exception):  # silent-ok: nothing running remotely to stop
                self._interrupt()


def main() -> None:
    # addsitedir, not sys.path.insert: the folder's .pth files must run (setuptools' distutils
    # shim is one, and pyspark 3.5 imports distutils on Python 3.12). Its entries then go first.
    import site
    n = len(sys.path)
    site.addsitedir(sys.argv[1])
    sys.path[:] = sys.path[n:] + sys.path[:n]
    worker = Worker(sys.argv[2] if len(sys.argv) > 2 else "databricks-connect")
    for line in _protocol_lines(sys.stdin):
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        op = msg.get("op")
        if op == "start":
            try:
                worker.start()
            except BaseException as exc:  # noqa: BLE001 - the engine shows why the session couldn't start
                send({"op": "error", "ename": type(exc).__name__, "evalue": str(exc)})
        elif op == "exec":
            threading.Thread(target=worker.run, args=(msg.get("id"), str(msg.get("code") or ""),
                                                      int(msg.get("preview_rows") or 100)), daemon=True).start()
        elif op == "interrupt":
            worker.interrupt()
        elif op == "quit":
            break


if __name__ == "__main__":
    main()
