"""The Databricks Connect process: runs a notebook's cells with ``spark`` on a Databricks cluster.

Started by dbconnect.py as ``python -I dbconnect_worker.py <site-packages>``, where the folder holds
databricks-connect and its own pyspark. The folder is added as a site directory, so its .pth files
run, and its entries go ahead of the engine's own site-packages. ``-I`` keeps this folder, the user's site-packages and
PYTHON* variables off ``sys.path``, so the engine's modules and its local Spark stand-in can't load
here, and databricks-connect's pyspark is the only one. This file imports nothing from KratosForge.

Sign-in and the cluster come from DATABRICKS_* variables the engine sets (a CLI profile name, the
host and auth type, or a service principal), read by the Databricks SDK inside databricks-connect.

Protocol, one JSON object per line. In: {"op": "start"}, {"op": "exec", "id", "code", "preview_rows"},
{"op": "interrupt"}, {"op": "quit"}. Out: {"op": "started", "client", "python"} or {"op": "error"},
then {"op": "result", "id", "status", "outputs"} per exec. A cell runs in a thread so interrupt can
reach ``spark.interruptAll()`` while it runs.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import sys
import threading
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


class Worker:
    def __init__(self) -> None:
        self.spark = None
        self.namespace: dict = {"__name__": "__main__"}
        self.running = False

    def start(self) -> None:
        import databricks.connect as dbc
        from databricks.connect import DatabricksSession
        self.spark = DatabricksSession.builder.getOrCreate()
        shown: list = []
        self.namespace.update({"spark": self.spark, "display": shown.append})
        self._shown = shown
        send({"op": "started", "client": getattr(dbc, "__version__", ""),
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
        if self.spark is not None and self.running:
            with contextlib.suppress(Exception):  # silent-ok: nothing running on the cluster to stop
                self.spark.interruptAll()


def main() -> None:
    # addsitedir, not sys.path.insert: the folder's .pth files must run (setuptools' distutils
    # shim is one, and pyspark 3.5 imports distutils on Python 3.12). Its entries then go first.
    import site
    n = len(sys.path)
    site.addsitedir(sys.argv[1])
    sys.path[:] = sys.path[n:] + sys.path[:n]
    worker = Worker()
    for line in sys.stdin:
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
