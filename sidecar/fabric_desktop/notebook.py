"""Notebooks: a local Python kernel and .ipynb storage.

The kernel keeps one namespace across cells, like Jupyter and Fabric. A cell runs on its own
thread, so the app can show printed output while it runs (``start`` then ``poll``) and Cancel
can stop it without losing the variables earlier cells made (``interrupt``). When a cell's last
statement is an expression its value is shown: data frames as a table, charts as images, objects
with an HTML view as HTML, anything else as its repr. ``display(...)`` shows values mid-cell.

Every cell has what a Fabric notebook has: ``spark`` (see spark_local), ``notebookutils`` and
``mssparkutils`` (see notebookutils_local) and ``display``. ``%%sql`` cells run Spark SQL and keep
a query's result in ``_sqldf``, ``df = %sql SELECT ...`` runs one query inside Python, ``%%html``
shows HTML, and ``%run`` runs another notebook in this one's namespace (line magics: magics.py).
"""

from __future__ import annotations

import ast
import base64
import contextlib
import ctypes
import inspect
import io
import json
import keyword
import os
import re
import sys
import threading
import time
import traceback
import warnings
from pathlib import Path
from typing import Any, Callable

import pyarrow as pa

from . import magics
from .frames import Frames, type_name

# Charts are drawn off screen and shown as images in the cell.
os.environ.setdefault("MPLBACKEND", "Agg")

PREVIEW_ROWS = 1000
_CELL_FILE = "<cell>"
_SECRET_NAME = re.compile(r"(pass(word)?|pwd|secret|token|key|credential)", re.I)
_SQL_IN_PLACE = re.compile(r"\b(MERGE\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM)\b", re.IGNORECASE)


def _no_input(*_args, **_kwargs):
    raise RuntimeError("input() is not supported in notebook cells.")


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def as_table(value: Any) -> pa.Table | None:
    """Arrow view of a data frame-like value, or None if it is not one."""
    module = type(value).__module__
    if isinstance(value, pa.Table):
        return value
    if module.startswith("duckdb.experimental.spark"):
        if not hasattr(value, "relation"):
            return None
        rel = value.relation.limit(PREVIEW_ROWS + 1)
        return (getattr(rel, "to_arrow_table", None) or rel.arrow)()
    if module.startswith("polars"):
        if hasattr(value, "collect"):
            value = value.head(PREVIEW_ROWS + 1).collect()
        return value.to_arrow() if hasattr(value, "to_arrow") else None
    if module.startswith("duckdb") and hasattr(value, "limit"):
        rel = value.limit(PREVIEW_ROWS + 1)
        return (getattr(rel, "to_arrow_table", None) or rel.arrow)()
    if module.startswith("pandas") and hasattr(value, "columns"):
        return pa.Table.from_pandas(value.head(PREVIEW_ROWS + 1), preserve_index=False)
    return None


def _total_rows(value: Any, table: pa.Table) -> int | None:
    """All rows, when that is cheap to know; None means "more than the preview"."""
    if type(value).__module__.startswith("duckdb.experimental.spark"):
        return table.num_rows if table.num_rows <= PREVIEW_ROWS else None
    total = getattr(value, "height", None)
    if total is None and not hasattr(value, "collect"):
        try:
            total = len(value)
        except TypeError:
            total = None
    if total is None:
        return table.num_rows if table.num_rows <= PREVIEW_ROWS else None
    return total


def _png(fig: Any) -> dict[str, Any]:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=110)
    return {"type": "image", "mime": "image/png", "data": base64.b64encode(buf.getvalue()).decode("ascii")}


def display(value: Any) -> dict[str, Any]:
    """The output a value shows as: a table, an image, HTML or text."""
    table = as_table(value)
    if table is not None:
        rows = [[_json_value(v) for v in row.values()] for row in table.slice(0, PREVIEW_ROWS).to_pylist()]
        total = _total_rows(value, table)
        return {"type": "table", "columns": table.schema.names, "types": [type_name(f.type) for f in table.schema], "rows": rows,
                "total_rows": total if total is not None else f"more than {PREVIEW_ROWS}"}
    module = type(value).__module__
    if module.startswith("matplotlib"):
        fig = value if hasattr(value, "savefig") else getattr(value, "figure", None) or getattr(value, "get_figure", lambda: None)()
        if fig is not None and hasattr(fig, "savefig"):
            out = _png(fig)
            with contextlib.suppress(Exception):
                import matplotlib.pyplot as plt
                plt.close(fig)
            return out
    if module.startswith("plotly") and hasattr(value, "to_html"):
        return {"type": "html", "html": value.to_html(include_plotlyjs=True, full_html=True)}
    for method, make in (("_repr_html_", lambda r: {"type": "html", "html": r}),
                         ("_repr_svg_", lambda r: {"type": "image", "mime": "image/svg+xml",
                                                   "data": base64.b64encode(r.encode()).decode("ascii")}),
                         ("_repr_png_", lambda r: {"type": "image", "mime": "image/png",
                                                   "data": base64.b64encode(r).decode("ascii")})):
        fn = getattr(value, method, None)
        if callable(fn) and not isinstance(value, type):
            with contextlib.suppress(Exception):
                rendered = fn()
                if rendered:
                    return make(rendered)
    return {"type": "text", "text": repr(value)}


class _Run:
    """One cell run: its outputs so far, shared between the cell's thread and ``poll``."""

    def __init__(self, count: int):
        self.count = count
        self.lock = threading.Lock()
        self.outputs: list[dict[str, Any]] = []
        self.version = 0
        self.status = "running"
        self.started = time.monotonic()
        self.seconds: float | None = None
        self.thread: threading.Thread | None = None
        self.done = threading.Event()
        self.watches: list[dict[str, Any]] | None = None

    def write(self, text: str) -> None:
        if not text:
            return
        with self.lock:
            if self.outputs and self.outputs[-1]["type"] == "stream":
                self.outputs[-1] = {"type": "stream", "text": self.outputs[-1]["text"] + text}
            else:
                self.outputs.append({"type": "stream", "text": text})
            self.version += 1

    def add(self, output: dict[str, Any]) -> None:
        with self.lock:
            self.outputs.append(output)
            self.version += 1

    def finish(self, status: str) -> None:
        with self.lock:
            self.status = status
            self.seconds = round(time.monotonic() - self.started, 2)
            self.version += 1
        self.done.set()

    def snapshot(self, known_version: int = -1) -> dict[str, Any]:
        with self.lock:
            changed = self.version != known_version
            snap = {"status": self.status, "execution_count": self.count, "version": self.version,
                    "seconds": self.seconds if self.seconds is not None else round(time.monotonic() - self.started, 2),
                    "outputs": list(self.outputs) if changed else None}
            if self.watches is not None:
                snap["watches"] = list(self.watches)
            return snap


class _Router(io.TextIOBase):
    """sys.stdout / sys.stderr: what a running cell's thread writes goes to that cell's output."""

    def __init__(self, fallback: Any):
        self.fallback = fallback
        self.targets: dict[int, _Run] = {}

    def writable(self) -> bool:
        return True

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return "utf-8"

    def write(self, text: str) -> int:
        run = self.targets.get(threading.get_ident())
        if run is not None:
            run.write(text)
        elif self.fallback is not None:
            self.fallback.write(text)
        return len(text)

    def flush(self) -> None:
        if self.fallback is not None and self.targets.get(threading.get_ident()) is None:
            with contextlib.suppress(Exception):
                self.fallback.flush()


def _routers() -> tuple[_Router, _Router]:
    if not isinstance(sys.stdout, _Router):
        sys.stdout = _Router(sys.stdout)
    if not isinstance(sys.stderr, _Router):
        sys.stderr = _Router(sys.stderr)
    return sys.stdout, sys.stderr  # type: ignore[return-value]


class Kernel:
    def __init__(self, token: Callable[[str], str] | None = None,
                 load_notebook: Callable[[str], list[dict[str, Any]]] | None = None,
                 variable_libraries: Callable[[str], dict[str, str] | None] | None = None):
        self.token = token
        self.load_notebook = load_notebook
        self.variable_libraries = variable_libraries
        self.notebook_name = ""
        self._current: _Run | None = None
        self._local = threading.local()
        self._secrets: set[str] = set()
        self.frames = Frames()
        self.restart()

    # ---- namespace

    def _base_namespace(self) -> dict[str, Any]:
        from . import notebookutils_local
        ns: dict[str, Any] = {"__name__": "__main__", "input": _no_input, "display": self._display}
        utils = notebookutils_local.build(notebookutils_local.Host(
            token=self.token, run_notebook=self.run_notebook, notebook_name=lambda: self.notebook_name,
            variables=self.variable_libraries))
        get_secret = utils.credentials.getSecret

        def remembered_secret(*args: Any, **kwargs: Any) -> str:
            value = get_secret(*args, **kwargs)
            self._secrets.add(value)
            return value
        utils.credentials.getSecret = remembered_secret
        sys.modules["notebookutils"] = utils
        sys.modules["mssparkutils"] = utils
        ns.update(notebookutils=utils, mssparkutils=utils)
        try:
            from . import spark_local
            spark_local.install(ns)
        except Exception as exc:  # noqa: BLE001 - plain Python still works without it
            print(f"spark is not available on this PC: {exc}", file=sys.__stderr__)
        return ns

    def restart(self) -> None:
        if self._current is not None and not self._current.done.is_set():
            self.interrupt()
        with contextlib.suppress(Exception):
            from . import spark_local
            spark_local.reset()
        from . import lakehouse
        lakehouse.reads_reset()
        self.namespace: dict[str, Any] = self._base_namespace()
        self.count = 0
        self.frames.clear()

    def _show(self, value: Any) -> dict[str, Any]:
        """``display(value)``; a table also keeps its whole result for the result viewer (frames.py)."""
        out = display(value)
        if out["type"] == "table":
            out["frame"] = self.frames.keep(value)
        return out

    def frame_page(self, frame: str, offset: int = 0, limit: int = 100, sort: int | None = None,
                   desc: bool = False, q: str = "") -> dict[str, Any]:
        return self.frames.page(frame, offset, limit, sort, desc, q)

    def frame_download(self, frame: str, format: str, path: str) -> dict[str, Any]:  # noqa: A002 - RPC name
        return self.frames.download(frame, format, path)

    def _display(self, *values: Any, summary: bool = False, **_kw: Any) -> None:
        run = getattr(self._local, "run", None)
        for value in values:
            out = self._show(value)
            if summary and out["type"] == "table":
                out["summary"] = True
            if run is not None:
                run.add(out)
            else:
                print(out.get("text", out))

    # ---- running a cell

    def start(self, code: str, watches: list[str] | None = None) -> dict[str, Any]:
        """Start ``code`` on its own thread; ``poll`` reports its output as it comes.

        ``watches`` are expressions evaluated after the cell, in its namespace and on its thread
        (so Cancel and the cell time limit cover them); their values come back in ``watches``."""
        if self._current is not None and not self._current.done.is_set():
            raise RuntimeError("A cell is already running. Wait for it, or Cancel it.")
        self.count += 1
        run = _Run(self.count)
        self._current = run
        run.thread = threading.Thread(target=self._execute, args=(code, run, list(watches or [])), daemon=True,
                                      name=f"cell-{self.count}")
        run.thread.start()
        return run.snapshot()

    def poll(self, known_version: int = -1, wait: float = 0.0) -> dict[str, Any]:
        """The running (or last) cell: status and, when they changed since ``known_version``, its outputs.

        ``wait`` seconds is how long to wait for news before answering, so the app can poll slowly."""
        run = self._current
        if run is None:
            return {"status": "idle", "execution_count": self.count, "version": 0, "seconds": 0, "outputs": []}
        deadline = time.monotonic() + min(wait, 2.0)
        while run.version == known_version and not run.done.is_set() and time.monotonic() < deadline:
            run.done.wait(0.05)
        return run.snapshot(known_version)

    def interrupt(self) -> dict[str, Any]:
        """Stop the running cell, keeping the namespace. Native code (a DuckDB query) is asked to stop too."""
        run = self._current
        if run is None or run.done.is_set() or run.thread is None:
            return {"interrupted": False}
        with contextlib.suppress(Exception):
            from . import spark_local
            spark_local.interrupt()
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(run.thread.ident), ctypes.py_object(KeyboardInterrupt))
        run.done.wait(2.0)
        return {"interrupted": True, "stopped": run.done.is_set()}

    def run(self, code: str) -> dict[str, Any]:
        """Run ``code`` to the end and return its result (used by tests and scripts)."""
        self.start(code)
        run = self._current
        assert run is not None
        run.done.wait()
        snap = run.snapshot()
        return {"status": snap["status"], "execution_count": snap["execution_count"], "outputs": snap["outputs"]}

    def _execute(self, code: str, run: _Run, watches: list[str] | None = None) -> None:
        from .notebookutils_local import NotebookExit
        out, err = _routers()
        ident = threading.get_ident()
        out.targets[ident] = err.targets[ident] = run
        self._local.run = run
        status = "error"
        try:
            try:
                status = self._exec(code, self.namespace, run)
                self._flush_figures(run)
            except NotebookExit:
                status = "ok"
            except KeyboardInterrupt:
                status = "cancelled"
                run.add({"type": "text", "text": "Cancelled. Variables are kept."})
            if watches and status != "cancelled":
                # What a watch prints goes nowhere: the cell's output is the cell's.
                out.targets[ident] = err.targets[ident] = _Run(0)
                self._watch(watches, run)
        except KeyboardInterrupt:  # an interrupt that landed while the first one was being handled
            status = "cancelled"
        finally:
            out.targets.pop(ident, None)
            err.targets.pop(ident, None)
            self._local.run = None
            run.finish(status)

    def _watch(self, watches: list[str], run: _Run) -> None:
        """Evaluate each watch expression; Cancel stops the rest, which are reported as skipped."""
        from .redact import secrets
        results: list[dict[str, Any]] = []
        for i, expr in enumerate(watches):
            started = time.monotonic()
            try:
                value = eval(compile(expr, "<watch>", "eval"), self.namespace)  # noqa: S307 - the person's own notebook code
                shown = "hidden" if self._hidden(expr, value) else secrets(_preview(value))
                results.append({"expr": expr, "value": shown, "type": type(value).__name__})
            except KeyboardInterrupt:
                results += [{"expr": e, "skipped": True} for e in watches[i:]]
                break
            except Exception as exc:  # noqa: BLE001 - shown next to the watch
                results.append({"expr": expr, "error": secrets(f"{type(exc).__name__}: {exc}")})
            results[-1]["seconds"] = round(time.monotonic() - started, 3)
        with run.lock:
            run.watches = results

    def _exec(self, code: str, namespace: dict[str, Any], run: _Run | None) -> str:
        """Run one cell's code in ``namespace``; returns "ok" or "error"."""
        from .notebookutils_local import NotebookExit
        cell = magics.cell(code)
        if cell.language == "sql":
            return self._run_sql(cell.body, run, namespace)
        if cell.language == "html":
            self._emit(run, {"type": "html", "html": cell.body})
            return "ok"
        if cell.language == "markdown":
            self._emit(run, {"type": "markdown", "text": cell.body.strip("\n").removeprefix(" ")})
            return "ok"
        if cell.language == "shell":
            return self._run_script(cell, run)
        if cell.language == "configure":
            print("Note: %%configure sets Spark session options in Fabric. On this PC it has nothing to set.")
            return "ok"
        if cell.language != "python":
            self._emit(run, {"type": "error", "ename": "RunsInFabric", "evalue":
                             "Scala, R and .NET cells need Fabric's Spark. Switch Runs on to Fabric to run them.",
                             "traceback": ""})
            return "error"
        code = magics.rewrite(cell.body)
        namespace.setdefault(magics.CALL, self._magic)
        self._patch_pyplot(run)
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=".*non-interactive.*")
                tree = ast.parse(code, _CELL_FILE)
                last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
                exec(compile(tree, _CELL_FILE, "exec"), namespace)
                value = None
                if last is not None:
                    value = eval(compile(ast.Expression(last.value), _CELL_FILE, "eval"), namespace)
                if value is not None:
                    namespace["_"] = value
                    self._emit(run, self._show(value))
        except NotebookExit as done:
            print(f"Notebook exited with value: {done.value}")
            namespace["__fd_exit__"] = done.value
            raise
        except (Exception, SystemExit) as exc:  # a cell must never take the kernel down
            self._emit(run, self._error(exc))
            return "error"
        return "ok"

    def _emit(self, run: _Run | None, output: dict[str, Any]) -> None:
        if run is not None:
            run.add(output)

    def _patch_pyplot(self, run: _Run | None) -> None:
        plt = sys.modules.get("matplotlib.pyplot")
        if plt is not None and run is not None and not getattr(plt.show, "_fabric_desktop", False):
            def show(*_a: Any, **_kw: Any) -> None:
                current = getattr(self._local, "run", None)
                if current is not None:
                    self._flush_figures(current)
            show._fabric_desktop = True  # type: ignore[attr-defined]
            plt.show = show

    @staticmethod
    def _flush_figures(run: _Run) -> None:
        plt = sys.modules.get("matplotlib.pyplot")
        if plt is None:
            return
        for num in plt.get_fignums():
            with contextlib.suppress(Exception):
                run.add(_png(plt.figure(num)))
        plt.close("all")

    def _run_sql(self, query: str, run: _Run | None, namespace: dict[str, Any]) -> str:
        """A %%sql cell: Spark SQL on this PC, through the notebook's spark session.

        It reads lakehouse tables and the session's temp views; CREATE TABLE AS and INSERT INTO
        write lakehouse tables. MERGE, UPDATE and DELETE run in Fabric. A query's result stays in
        ``_sqldf`` as a spark DataFrame, as in Databricks, so the next Python cell can use it."""
        if _SQL_IN_PLACE.search(query):
            text = ("This cell changes tables, so it runs in Fabric once the notebook is published. "
                    "On this PC, a Data mesh run does the same surrogate key MERGE.\n")
            self._emit(run, {"type": "stream", "text": text})
            return "ok"
        try:
            from . import spark_local
            result = spark_local.session().sql(query)
            if result is not None and result.columns:
                if magics.returns_rows(query):  # DDL comes back as an empty frame; that isn't a result
                    namespace["_sqldf"] = result
                self._emit(run, self._show(result))
            return "ok"
        except Exception as exc:  # noqa: BLE001
            self._emit(run, self._error(exc))
            return "error"

    # ---- magics and other notebooks

    def _magic(self, name: str, line: str) -> Any:
        if name == "run":
            target, _, rest = line.strip().partition(" ")
            args = ast.literal_eval(rest.strip()) if rest.strip() else {}
            return self._run_notebook_in(target.strip("\"'"), self.namespace, args)
        if name == "sql":
            return self._sql_value(line)
        if name in ("system", "sx", "sh"):
            from . import shell
            code, lines = shell.system(shell.expand(line, self.namespace),
                                       echo=None if name != "sx" else (lambda _line: None))
            self.namespace["_exit_code"] = code
            return lines if name == "sx" else None
        if name == "fs":
            return _fs_magic(self.namespace["notebookutils"].fs, line)
        if name in ("pip", "conda"):
            print(f"Note: %{name} installs packages in Fabric's session. On this PC the engine's packages "
                  f"are fixed, so this line was skipped.")
            return None
        if name == "time":
            started = time.monotonic()
            value = eval(compile(line, _CELL_FILE, "eval"), self.namespace) if line.strip() else None
            print(f"Wall time: {time.monotonic() - started:.3f} s")
            return value
        raise ValueError(f"%{name} isn't supported on this PC. Supported: %run, %sql, %fs, %sh, !command, "
                         f"%pip (skipped), %time.")

    def _run_script(self, cell: Any, run: _Run | None) -> str:
        """A %sh, %%sh, %%bash, %%cmd or %%powershell cell: the cell runs as a script on this PC.
        A non-zero exit code fails the cell, as a %sh cell does in Databricks."""
        from . import magics, shell
        try:
            code = shell.script(magics.SHELL_HEADERS[cell.header], cell.body.lstrip(" \t"))
            self.namespace["_exit_code"] = code
            if code != 0:
                raise shell.ShellError(f"The script ended with exit code {code}.")
        except (OSError, shell.ShellError) as exc:
            self._emit(run, self._error(exc))
            return "error"
        return "ok"

    @staticmethod
    def _sql_value(query: str) -> Any:
        """``%sql SELECT ...`` inside Python: the query's result as a spark DataFrame."""
        if not query.strip():
            raise ValueError("%sql needs a query after it, like: df = %sql SELECT * FROM sales")
        if _SQL_IN_PLACE.search(query):
            raise ValueError("MERGE, UPDATE and DELETE change rows in place, which runs in Fabric only. "
                             "Put the statement in a %%sql cell; it runs once the notebook is published.")
        from . import spark_local
        return spark_local.session().sql(query)

    def run_notebook(self, name: str, timeout_seconds: int = 90, arguments: dict[str, Any] | None = None) -> Any:
        """notebookutils.notebook.run: the other notebook runs in a fresh namespace; returns its exit value."""
        ns = self._base_namespace()
        ns[magics.CALL] = self._magic
        return self._run_notebook_in(name, ns, arguments or {})

    def _run_notebook_in(self, name: str, namespace: dict[str, Any], arguments: dict[str, Any]) -> Any:
        from .notebookutils_local import NotebookExit
        if self.load_notebook is None:
            raise RuntimeError("Other notebooks can't be opened here.")
        cells = self.load_notebook(name.rsplit("/", 1)[-1])
        has_parameters = any("parameters" in (c.get("meta") or {}).get("tags", []) for c in cells)
        if arguments and not has_parameters:
            namespace.update(arguments)
        run = getattr(self._local, "run", None)
        for cell in cells:
            if cell.get("type") == "markdown" or not cell.get("source", "").strip():
                continue
            try:
                status = self._exec(cell["source"], namespace, run)
            except NotebookExit as done:
                return done.value
            if status != "ok":
                raise RuntimeError(f"Notebook {name!r} failed; its error is shown above.")
            if arguments and "parameters" in (cell.get("meta") or {}).get("tags", []):
                namespace.update(arguments)
        return None

    # ---- proofs (prove_service): the proof runtime is bound where the cells run

    def bind_proof(self, config: dict[str, Any], side: str, root: str, options: Callable[[], dict[str, str]]) -> None:
        from . import lakehouse
        from .proof_runtime import ProofRuntime
        self._proof = ProofRuntime(config, side).bind(root, options)
        lakehouse.proof = self._proof

    def unbind_proof(self) -> None:
        from . import lakehouse
        lakehouse.proof = None
        if getattr(self, "_proof", None) is not None:
            self._proof.restore()

    def finish_proof(self, modules: list[str]) -> dict[str, Any]:
        log = self._proof.finish(modules)
        self._proof = None
        return log

    def reads(self) -> dict[str, Any]:
        """What the cells have read since the kernel started (lakehouse.reads), for replay bundles."""
        from . import lakehouse
        return json.loads(json.dumps(lakehouse.reads))

    def stop(self) -> dict[str, Any]:
        """This kernel runs inside the engine: only ending the engine stops a cell stuck in native code."""
        return {"stopped": False, "in_engine": True}

    # ---- what the Variables pane and autocomplete show

    def variables(self) -> list[dict[str, Any]]:
        skip = set(self._base_namespace_keys())
        rows = []
        for name, value in list(self.namespace.items()):
            if (name.startswith("_") and name != "_sqldf") or name in skip or inspect.ismodule(value) or inspect.isclass(value) \
                    or inspect.isroutine(value):
                continue
            rows.append({"name": name, "type": type(value).__name__, "size": _size(value),
                         "value": "hidden" if self._hidden(name, value) else _preview(value)})
        return sorted(rows, key=lambda r: r["name"].lower())

    @staticmethod
    def _base_namespace_keys() -> tuple[str, ...]:
        return ("input", "display", "notebookutils", "mssparkutils", "spark", "In", "Out")

    def _hidden(self, name: str, value: Any) -> bool:
        return bool(_SECRET_NAME.search(name)) or (isinstance(value, str) and value in self._secrets)

    def complete(self, code: str, cursor: int | None = None) -> dict[str, Any]:
        """Names that can follow the text before ``cursor``: variables, attributes, keywords, builtins."""
        text = code[:len(code) if cursor is None else cursor]
        m = re.search(r"([A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)*)\.?(\w*)$", text)
        if not m:
            return {"from": len(text), "items": []}
        token = m.group(0)
        if "." in token:
            base, _, prefix = token.rpartition(".")
            obj = self._lookup(base)
            if obj is _MISSING:
                return {"from": len(text) - len(prefix), "items": []}
            items = []
            for attr in dir(obj):
                if attr.startswith("_") and not prefix.startswith("_"):
                    continue
                if attr.startswith(prefix):
                    try:
                        static = inspect.getattr_static(obj, attr)
                    except AttributeError:
                        static = None
                    kind = "property" if isinstance(static, property) else \
                        "method" if callable(static) or isinstance(static, (staticmethod, classmethod)) else "variable"
                    items.append({"label": attr, "type": kind})
            return {"from": len(text) - len(prefix), "items": items[:300]}
        prefix = token
        names = {n: "variable" for n in self.namespace if not n.startswith("__")}
        for n, v in self.namespace.items():
            if inspect.ismodule(v):
                names[n] = "namespace"
            elif inspect.isroutine(v) or inspect.isclass(v):
                names[n] = "function" if inspect.isroutine(v) else "class"
        import builtins
        for n in dir(builtins):
            if not n.startswith("_"):
                names.setdefault(n, "function")
        for n in keyword.kwlist:
            names.setdefault(n, "keyword")
        items = [{"label": n, "type": t} for n, t in sorted(names.items()) if n.startswith(prefix) and n != prefix]
        return {"from": len(text) - len(prefix), "items": items[:300]}

    def _lookup(self, dotted: str) -> Any:
        """The object a plain dotted name refers to, without calling anything (properties aren't run)."""
        first, *rest = dotted.split(".")
        if first in self.namespace:
            obj = self.namespace[first]
        else:
            import builtins
            obj = getattr(builtins, first, _MISSING)
        for part in rest:
            if obj is _MISSING:
                break
            try:
                static = inspect.getattr_static(obj, part)
            except AttributeError:
                return _MISSING
            obj = getattr(obj, part) if inspect.ismodule(obj) or not isinstance(static, property) else _MISSING
        return obj

    @staticmethod
    def _error(exc: BaseException) -> dict[str, Any]:
        frames = [f for f in traceback.extract_tb(exc.__traceback__)
                  if f.filename != __file__ and not f.filename.endswith(("notebookutils_local.py",))]
        lines = traceback.format_list(frames) + traceback.format_exception_only(type(exc), exc)
        return {"type": "error", "ename": type(exc).__name__, "evalue": str(exc),
                "traceback": "Traceback (most recent call last):\n" + "".join(lines)}


_MISSING = object()


def _fs_magic(fs: Any, line: str) -> Any:
    """Databricks' ``%fs`` on notebookutils.fs: ``%fs ls Files/raw``, ``head``, ``cp [-r]``,
    ``mv``, ``rm [-r]``, ``mkdirs``, ``put [-f] path "text"``. Same paths and safety rules as fs."""
    import shlex
    words = shlex.split(line, posix=True)
    if not words:
        raise ValueError("%fs needs a command: ls, head, cp, mv, rm, mkdirs or put.")
    command, flags = words[0], {w for w in words[1:] if w in ("-r", "-f")}
    args = [w for w in words[1:] if w not in ("-r", "-f")]
    usage = {"ls": 1, "head": 1, "cp": 2, "mv": 2, "rm": 1, "mkdirs": 1, "put": 2}
    if command not in usage:
        raise ValueError(f"%fs {command} isn't a command here. Use ls, head, cp, mv, rm, mkdirs or put.")
    if len(args) < usage[command] - (command == "ls"):
        raise ValueError(f"%fs {command} needs {usage[command]} path{'s' if usage[command] > 1 else ''}.")
    if command == "ls":
        import polars as pl
        found = fs.ls(args[0] if args else "Files")
        return pl.DataFrame({"name": [f.name for f in found], "path": [f.path for f in found],
                             "size": [f.size for f in found], "isDir": [f.isDir for f in found]},
                            schema={"name": pl.Utf8, "path": pl.Utf8, "size": pl.Int64, "isDir": pl.Boolean})
    if command == "head":
        print(fs.head(args[0], int(args[1]) if len(args) > 1 else 65536))
        return None
    if command == "cp":
        return fs.cp(args[0], args[1], recurse="-r" in flags)
    if command == "mv":
        return fs.mv(args[0], args[1])
    if command == "rm":
        return fs.rm(args[0], recurse="-r" in flags)
    if command == "mkdirs":
        return fs.mkdirs(args[0])
    return fs.put(args[0], args[1], overwrite="-f" in flags)


def _size(value: Any) -> str:
    module = type(value).__module__
    if module.startswith("duckdb.experimental.spark") and hasattr(value, "columns"):
        return f"? × {len(value.columns)}"
    shape = getattr(value, "shape", None)
    if isinstance(shape, tuple) and shape:
        return " × ".join(_count(n) for n in shape)
    if isinstance(value, pa.Table):
        return f"{_count(value.num_rows)} × {value.num_columns}"
    if isinstance(value, (str, bytes, list, tuple, dict, set)):
        return str(len(value))
    return ""


def _count(n: Any) -> str:
    if not isinstance(n, int):
        return str(n)
    for unit, size in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 10_000)):
        if n >= size:
            return f"{n / size:.1f}{unit}".replace(".0", "") if unit != "K" else f"{n // 1000}K"
    return str(n)


def _preview(value: Any) -> str:
    module = type(value).__module__
    if module.startswith(("polars", "pandas", "pyarrow", "duckdb")):
        cols = getattr(value, "columns", None)
        if cols is not None:
            names = list(cols)
            return ", ".join(map(str, names[:6])) + (", ..." if len(names) > 6 else "")
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001  # silent-ok: repr() of a user's object failed; the preview shows ? instead
        return "?"
    return text if len(text) <= 80 else text[:77] + "..."


# Storage: notebooks are standard .ipynb files with the attached lakehouse in metadata.

_SAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


MAX_WATCHES = 10


def check_watches(watches: Any) -> list[str]:
    """Watch expressions as the Variables pane keeps them: at most MAX_WATCHES single expressions
    (not statements) of up to 200 characters. Raises ValueError naming the one that isn't."""
    if not watches:
        return []
    if not isinstance(watches, list) or not all(isinstance(w, str) for w in watches):
        raise ValueError("watches is a list of expressions")
    out = []
    for w in (w.strip() for w in watches):
        if not w or w in out:
            continue
        if len(w) > 200:
            raise ValueError("A watch can be up to 200 characters.")
        try:
            compile(w, "<watch>", "eval")
        except SyntaxError:
            raise ValueError(f"{w} isn't an expression. A watch is something like df.height or len(rows).") from None
        out.append(w)
    if len(out) > MAX_WATCHES:
        raise ValueError(f"A notebook can have up to {MAX_WATCHES} watches.")
    return out


def check_notebook_name(name: str) -> str:
    name = name.strip()
    if not name or _SAFE_NAME.search(name) or name.endswith("."):
        raise ValueError(f"Invalid notebook name {name!r}: avoid < > : \" / \\ | ? * and a trailing dot.")
    return name


COMPUTE = ("default", "local", "fabric")


def _cell_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """A cell's settings as Jupyter and Fabric store them: the parameters tag, hidden input or
    output, and frozen (can't be edited or run)."""
    md: dict[str, Any] = {}
    tags = [t for t in meta.get("tags", []) if isinstance(t, str)]
    if tags:
        md["tags"] = tags
    hidden = {k: True for k in ("source_hidden", "outputs_hidden") if meta.get(k)}
    if hidden:
        md["jupyter"] = hidden
    if meta.get("frozen"):
        md.update(editable=False, deletable=False, run_control={"frozen": True})
    return md


def _cell_meta(md: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if md.get("tags"):
        meta["tags"] = [t for t in md["tags"] if isinstance(t, str)]
    for k in ("source_hidden", "outputs_hidden"):
        if (md.get("jupyter") or {}).get(k):
            meta[k] = True
    if (md.get("run_control") or {}).get("frozen"):
        meta["frozen"] = True
    return meta


# Every cell has a stable id (nbformat 4.5): kept across saves and edits, so a run receipt, a
# comparison or a review can point at a cell after the notebook changes around it.
_CELL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def new_cell_id() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


def assign_cell_ids(cells: list[dict[str, Any]], make: Callable[[int, dict[str, Any]], str] | None = None) -> list[str]:
    """Give every cell a valid, unique ``cell_id`` (in place) and return them in order. A missing,
    invalid or repeated id (a pasted copy) gets a new one; the first holder of an id keeps it."""
    seen: set[str] = set()
    ids = []
    for i, c in enumerate(cells):
        cid = c.get("cell_id")
        if not isinstance(cid, str) or not _CELL_ID.match(cid) or cid in seen:
            cid = (make or (lambda _i, _c: new_cell_id()))(i, c)
            while cid in seen:
                cid = new_cell_id()
            c["cell_id"] = cid
        seen.add(cid)
        ids.append(cid)
    return ids


def _derived_id(i: int, c: dict[str, Any]) -> str:
    """An id for a cell that was never saved (cells added for publishing): the same every time."""
    import hashlib
    return "fd-" + hashlib.sha1(f"{i}:{c.get('type')}:{c.get('source', '')}".encode()).hexdigest()[:12]


# Notebook and cell metadata Fabric Desktop writes. Anything else in a notebook (written by
# Jupyter, Fabric or another tool) is kept as it is when the notebook is saved here.
_OWN_NOTEBOOK_KEYS = ("kernelspec", "language_info")
_OWN_FD_KEYS = ("compute", "workspace", "lakehouse")


def _merge_cell_metadata(ours: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    keep = {k: v for k, v in before.items() if k not in ("tags", "editable", "deletable")}
    if isinstance(keep.get("jupyter"), dict):
        rest = {k: v for k, v in keep["jupyter"].items() if k not in ("source_hidden", "outputs_hidden")}
        keep["jupyter"] = rest
    if isinstance(keep.get("run_control"), dict):
        keep["run_control"] = {k: v for k, v in keep["run_control"].items() if k != "frozen"}
    for k in ("jupyter", "run_control"):
        if k in keep and not keep[k]:
            del keep[k]
    for k, v in ours.items():
        keep[k] = keep[k] | v if isinstance(v, dict) and isinstance(keep.get(k), dict) else v
    return keep


def merge_saved(new: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    """``new`` (from to_ipynb) with the metadata Fabric Desktop doesn't own carried over from the
    notebook as it was on disk: notebook-level keys, other tools' ``fabric_desktop`` keys and
    ``dependencies`` such as a Fabric environment, and each cell's other metadata, by cell id."""
    old_meta = before.get("metadata") if isinstance(before.get("metadata"), dict) else {}
    meta = {k: v for k, v in old_meta.items() if k not in _OWN_NOTEBOOK_KEYS + ("fabric_desktop", "dependencies")}
    old_fd = {k: v for k, v in (old_meta.get("fabric_desktop") or {}).items() if k not in _OWN_FD_KEYS}
    old_deps = {k: v for k, v in (old_meta.get("dependencies") or {}).items() if k != "lakehouse"}
    meta.update(new["metadata"])
    fd = old_fd | new["metadata"].get("fabric_desktop", {})
    if fd:
        meta["fabric_desktop"] = fd
    deps = old_deps | new["metadata"].get("dependencies", {})
    if deps:
        meta["dependencies"] = deps
    old_cells = {c.get("id"): c.get("metadata") or {} for c in before.get("cells", []) if isinstance(c, dict) and c.get("id")}
    for cell in new["cells"]:
        if cell["id"] in old_cells:
            cell["metadata"] = _merge_cell_metadata(cell["metadata"], old_cells[cell["id"]])
    return new | {"metadata": meta}


def to_ipynb(cells: list[dict[str, Any]], lakehouse: dict[str, Any] | None,
             compute: str = "default", workspace: dict[str, Any] | None = None) -> dict[str, Any]:
    nb_cells = []
    ids = assign_cell_ids([dict(c) for c in cells], _derived_id)
    for c, cid in zip(cells, ids):
        cell = {"cell_type": "markdown" if c.get("type") == "markdown" else "code", "id": cid,
                "metadata": _cell_metadata(c.get("meta") or {}), "source": c.get("source", "").splitlines(keepends=True)}
        if cell["cell_type"] == "code":
            cell.update(execution_count=None, outputs=[])
        nb_cells.append(cell)
    metadata: dict[str, Any] = {
        "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
        "language_info": {"name": "python"},
    }
    if compute != "default":
        metadata["fabric_desktop"] = {"compute": compute}
    if workspace and workspace.get("workspace_id"):
        # Where Publish to OneLake (and Fabric runs and schedules) put this notebook.
        metadata.setdefault("fabric_desktop", {})["workspace"] = {
            "workspace_id": workspace["workspace_id"], "workspace_name": workspace.get("workspace_name", "")}
    if lakehouse:
        metadata.setdefault("fabric_desktop", {})["lakehouse"] = lakehouse
        # Fabric reads this to show the default lakehouse when the notebook is published.
        metadata["dependencies"] = {"lakehouse": {
            "default_lakehouse": lakehouse["lakehouse_id"],
            "default_lakehouse_name": lakehouse["name"],
            "default_lakehouse_workspace_id": lakehouse["workspace_id"],
        }}
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": metadata, "cells": nb_cells}


def from_ipynb(nb: dict[str, Any]) -> dict[str, Any]:
    cells = []
    for c in nb.get("cells", []):
        source = c.get("source", "")
        cell = {"type": "markdown" if c.get("cell_type") == "markdown" else "code",
                "source": "".join(source) if isinstance(source, list) else source}
        if isinstance(c.get("id"), str):
            cell["cell_id"] = c["id"]
        meta = _cell_meta(c.get("metadata") or {})
        if meta:
            cell["meta"] = meta
        cells.append(cell)
    # Notebooks saved before cells had ids (or by tools that don't write them) get ids here, the
    # same on every load until the next save writes them.
    assign_cell_ids(cells, _derived_id)
    ours = nb.get("metadata", {}).get("fabric_desktop", {})
    compute = ours.get("compute") if ours.get("compute") in COMPUTE else "default"
    return {"cells": cells, "lakehouse": ours.get("lakehouse"), "compute": compute,
            "workspace": ours.get("workspace")}


class NotebookStore:
    def __init__(self, folder: Path):
        self.folder = folder

    def path(self, name: str) -> Path:
        return self.folder / f"{check_notebook_name(name)}.ipynb"

    def list(self) -> list[dict[str, Any]]:
        if not self.folder.exists():
            return []
        files = sorted(self.folder.glob("*.ipynb"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [{"name": p.stem, "modified": p.stat().st_mtime} for p in files]

    def load(self, name: str) -> dict[str, Any]:
        nb = json.loads(self.path(name).read_text(encoding="utf-8"))
        return {"name": name, **from_ipynb(nb)}

    def save(self, name: str, cells: list[dict[str, Any]], lakehouse: dict[str, Any] | None = None,
             compute: str | None = None, workspace: dict[str, Any] | None = None) -> dict[str, Any]:
        """Save the notebook. ``compute`` is where Run all runs it: "local" (this PC), "fabric",
        or "default" (whatever the environment picked in the top bar runs on). ``workspace`` is
        where it is published ({"workspace_id", "workspace_name"}; {} forgets it). None keeps the saved choice.

        Cells keep their ``cell_id``; a cell without one gets one, returned in ``cell_ids`` so the
        app can keep it. Metadata other tools wrote into the file is kept."""
        path = self.path(name)
        before: dict[str, Any] = {}
        if path.exists():
            try:
                before = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                before = {}
        saved = from_ipynb(before) if before and (compute is None or workspace is None) else {}
        if compute is None:
            compute = saved.get("compute", "default")
        if workspace is None:
            workspace = saved.get("workspace")
        if compute not in COMPUTE:
            raise ValueError(f"compute is one of {', '.join(COMPUTE)}")
        cells = [dict(c) for c in cells]
        ids = assign_cell_ids(cells)
        nb = merge_saved(to_ipynb(cells, lakehouse, compute, workspace), before) if before else \
            to_ipynb(cells, lakehouse, compute, workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".ipynb.tmp")
        tmp.write_text(json.dumps(nb, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        return {"name": name, "cell_ids": ids}

    def extra(self, name: str, key: str) -> Any:
        """A value Fabric Desktop keeps in the notebook's metadata under ``fabric_desktop.<key>``
        that the notebook screen doesn't send (the proof contract). None when it isn't set."""
        nb = json.loads(self.path(name).read_text(encoding="utf-8"))
        return ((nb.get("metadata") or {}).get("fabric_desktop") or {}).get(key)

    def set_extra(self, name: str, key: str, value: Any) -> None:
        """Set (or, with None, remove) ``fabric_desktop.<key>`` in the saved notebook. Saves from the
        notebook screen keep it (merge_saved)."""
        if key in ("compute", "workspace", "lakehouse"):
            raise ValueError(f"{key} is saved with the notebook, not set on its own.")
        path = self.path(name)
        nb = json.loads(path.read_text(encoding="utf-8"))
        fd = nb.setdefault("metadata", {}).setdefault("fabric_desktop", {})
        if value is None:
            fd.pop(key, None)
        else:
            fd[key] = value
        tmp = path.with_suffix(".ipynb.tmp")
        tmp.write_text(json.dumps(nb, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def new(self, prefix: str = "Notebook") -> dict[str, Any]:
        taken = {n["name"] for n in self.list()}
        i = 1
        while f"{prefix} {i}" in taken:
            i += 1
        name = f"{prefix} {i}"
        self.save(name, [{"type": "code", "source": ""}])
        return self.load(name)

    def rename(self, name: str, new_name: str) -> dict[str, Any]:
        target = self.path(new_name)
        if target.exists():
            raise ValueError(f"A notebook named {new_name!r} already exists.")
        self.path(name).rename(target)
        return {"name": new_name}

    def delete(self, name: str) -> None:
        self.path(name).unlink(missing_ok=True)


class NotebookShelf(NotebookStore):
    """The app's own notebooks folder plus the ``notebooks`` folder of each notebooks repository
    opened in Source control. A notebook is still found by its name alone, so names are unique
    across all of them; the app's own folder wins if two places hold the same name.

    ``places`` returns ``[(root, label, folder)]`` for the repositories (root is the repository
    folder, which identifies the place)."""

    def __init__(self, folder: Path, places: Callable[[], list[tuple[str, str, Path]]] = lambda: []):
        super().__init__(folder)
        self._places = places

    def places(self) -> list[dict[str, Any]]:
        home = _resolved(self.folder)
        out, seen = [], {home}
        for root, label, folder in self._places():
            f = _resolved(folder)
            if f in seen or _resolved(Path(root)) == home:
                continue  # "Version my notebooks": that repository is the app's own folder
            seen.add(f)
            out.append({"root": root, "label": label, "folder": folder})
        return out

    def _folders(self) -> list[tuple[dict[str, Any] | None, Path]]:
        return [(None, self.folder)] + [(p, p["folder"]) for p in self.places()]

    def _where(self, name: str) -> tuple[dict[str, Any] | None, Path]:
        file = f"{check_notebook_name(name)}.ipynb"
        for place, folder in self._folders():
            if (folder / file).is_file():
                return place, folder
        return None, self.folder

    def path(self, name: str) -> Path:
        return self._where(name)[1] / f"{check_notebook_name(name)}.ipynb"

    def place_of(self, name: str) -> dict[str, str] | None:
        """The repository holding the notebook ({"root", "label"}), or None for the app's own folder."""
        place = self._where(name)[0]
        return {"root": place["root"], "label": place["label"]} if place else None

    def _place(self, root: str | None) -> tuple[dict[str, Any] | None, Path]:
        if not root:
            return None, self.folder
        for place in self.places():
            if _resolved(Path(place["root"])) == _resolved(Path(root)):
                return place, place["folder"]
        raise ValueError("That repository isn't a notebooks repository opened in Source control.")

    def list(self) -> list[dict[str, Any]]:
        """Every notebook that can be opened by name. A copy shadowed by a same-named notebook in
        an earlier place isn't listed (opening, saving or deleting by that name reaches the other)."""
        out, names = [], set()
        for place, folder in self._folders():
            if not folder.is_dir():
                continue
            for p in folder.glob("*.ipynb"):
                if p.stem in names:
                    continue
                where = {"root": place["root"], "label": place["label"]} if place else None
                out.append({"name": p.stem, "modified": p.stat().st_mtime, "place": where})
                names.add(p.stem)
        return sorted(out, key=lambda n: n["modified"], reverse=True)

    def _taken(self, name: str) -> bool:
        return any((f / f"{check_notebook_name(name)}.ipynb").exists() for _, f in self._folders())

    def new(self, prefix: str = "Notebook", place: str | None = None) -> dict[str, Any]:
        _, folder = self._place(place)
        i = 1
        while self._taken(f"{prefix} {i}"):
            i += 1
        name = f"{prefix} {i}"
        folder.mkdir(parents=True, exist_ok=True)
        NotebookStore(folder).save(name, [{"type": "code", "source": ""}])
        return self.load(name)

    def rename(self, name: str, new_name: str) -> dict[str, Any]:
        if self._taken(new_name):
            raise ValueError(f"A notebook named {new_name!r} already exists.")
        src = self.path(name)
        src.rename(src.with_name(f"{check_notebook_name(new_name)}.ipynb"))
        return {"name": new_name}

    def move(self, name: str, place: str | None) -> dict[str, Any]:
        """Moves the notebook's file to another place (a repository root, or None for the app's
        own folder). The copy is written and checked before the original is removed, and a
        notebook already there is never replaced."""
        src = self.path(name)
        if not src.is_file():
            raise ValueError(f"There's no notebook named {name!r}.")
        target_place, folder = self._place(place)
        dest = folder / src.name
        if _resolved(dest) == _resolved(src):
            return {"name": name, "place": self.place_of(name), "moved": False}
        data = src.read_bytes()
        folder.mkdir(parents=True, exist_ok=True)
        where = target_place["label"] if target_place else "the app's notebooks folder"
        try:
            out = open(dest, "xb")  # refuses when a file of that name is already there
        except FileExistsError:
            raise ValueError(f"A notebook named {name!r} is already in {where}. Nothing was moved.") from None
        try:
            with out:
                out.write(data)
            if dest.read_bytes() != data:
                raise OSError("the copy doesn't match")
        except OSError as e:
            dest.unlink(missing_ok=True)  # never leave a partial copy that blocks a retry
            raise OSError(f"Couldn't copy {name} to {where} ({e}). The notebook is still where it was.") from None
        src.unlink()
        return {"name": name, "place": self.place_of(name), "moved": True}


def _resolved(p: Path) -> str:
    try:
        return os.path.normcase(str(p.resolve()))
    except OSError:
        return os.path.normcase(str(p))
