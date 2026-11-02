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
import builtins
import contextlib
import ctypes
import hashlib
import inspect
import io
import itertools
import json
import keyword
import logging
import math
import os
import re
import sys
import tempfile
import threading
import time
import traceback
import types
import uuid
import warnings
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Iterator, cast

import pyarrow as pa

from . import chart_output, notebook_cells
from . import notebook_resources
from . import magics
from . import history
from . import write_queue
from .frames import Frames, pandas_table, type_name
from .redact import describe

# Charts are drawn off screen and shown as images in the cell.
os.environ.setdefault("MPLBACKEND", "Agg")

_log = logging.getLogger(__name__)

PREVIEW_ROWS = 1000
# What one cell run keeps and polls: printed text past STREAM_CHARS goes to a file the user can open,
# and rich outputs (tables, charts, HTML) past RICH_CHARS are dropped, each with one line saying so.
STREAM_CHARS = 1_000_000
RICH_CHARS = 16_000_000
_CELL_FILE = "<cell>"
# Proxy and certificate settings the engine's own HTTP calls (with your Fabric token) read.
_ENV_NETWORK = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
                "SSL_CERT_FILE", "SSL_CERT_DIR"}
_SECRET_NAME = re.compile(r"(pass(word)?|pwd|secret|token|key|credential)", re.I)
_SQL_IN_PLACE = re.compile(r"\b(MERGE\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM)\b", re.IGNORECASE)


def _change_runs_here(query: str) -> bool:
    from . import spark_local
    return spark_local.change_runs_here(query)
# The file stops at SPILL_CHARS. Files go when the kernel restarts or the clear_output() that hides
# them runs, and the engine sweeps ones left by a closed session when it starts (sweep_spills).
SPILL_CHARS = 100_000_000
SPILL_PREFIX = "kratosforge-output-"
# A notebook's own process (kernel_proc) writes its files in a folder of its own, named here, so the
# engine can delete them when Stop or Restart ends the process.
SPILL_DIR_ENV = "FABRIC_DESKTOP_OUTPUT_DIR"
SPILL_DIR_PREFIX = "kratosforge-kernel-"
_spills: set[Path] = set()
_spills_lock = threading.Lock()


def _drop_spill(path: Path, handle: Any = None) -> None:
    if handle is not None:
        with contextlib.suppress(OSError):
            handle.close()
    with _spills_lock:
        _spills.discard(path)
    with contextlib.suppress(OSError):
        path.unlink()


def remove_spills() -> None:
    """Delete the output files this process's cells wrote (a kernel restart)."""
    with _spills_lock:
        paths = list(_spills)
    for path in paths:
        _drop_spill(path)


def sweep_spills(idle_seconds: float = 3600) -> None:
    """At engine start: delete output files from earlier sessions, leaving any written to lately."""
    cutoff = time.time() - idle_seconds
    temp = Path(tempfile.gettempdir())
    for path in [*temp.glob(f"{SPILL_PREFIX}*.txt"), *temp.glob(f"{SPILL_DIR_PREFIX}*/{SPILL_PREFIX}*.txt")]:
        with contextlib.suppress(OSError):
            if path.stat().st_mtime < cutoff:
                path.unlink()
    for folder in temp.glob(f"{SPILL_DIR_PREFIX}*"):
        with contextlib.suppress(OSError):
            if folder.is_dir() and folder.stat().st_mtime < cutoff:
                folder.rmdir()  # only once empty


def clear_spill_dir(folder: Path) -> None:
    """Delete the output files a notebook process wrote, once it has ended."""
    for path in folder.glob(f"{SPILL_PREFIX}*.txt"):
        with contextlib.suppress(OSError):
            path.unlink()


def _no_input(*_args, **_kwargs):
    raise RuntimeError("input() is not supported in notebook cells.")


def _json_value(value: Any) -> Any:
    """A preview cell: JSON (and the shell) has no NaN or infinity, so those show as text, as SQL results do."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
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
        return pandas_table(value.head(PREVIEW_ROWS + 1))
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
        shown = table.slice(0, PREVIEW_ROWS)
        # Column by column, not to_pylist: its dicts keep one of two columns that share a name.
        columns = [[_json_value(v) for v in column.to_pylist()] for column in shown.columns]
        rows = [list(row) for row in zip(*columns)] if columns else \
            [[] for _ in range(min(table.num_rows, PREVIEW_ROWS))]   # slicing a table with no columns says 1000
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
    if chart_output.is_bokeh(value):
        return chart_output.bokeh_html(value)
    if chart_output.is_altair(value):
        return chart_output.altair_output(value)
    if chart_output.is_plotly(value):
        return chart_output.plotly_output(value)
    if module.startswith("bokeh") and hasattr(value, "document"):  # a plot or layout: the page Bokeh's show() would open
        with contextlib.suppress(Exception):
            from bokeh.embed import file_html
            from bokeh.resources import INLINE
            return {"type": "html", "html": file_html(value, INLINE)}
    if not isinstance(value, type):
        bundle = _mime_bundle(value)
        if bundle:
            shown = _from_bundle(bundle)
            if shown is not None:
                return shown
    return {"type": "text", "text": repr(value)}


# What a value's rich views become, best first. HTML only ever reaches the app's sandboxed frame;
# Markdown is drawn by the app's escaping Markdown renderer; JSON as a tree of text.
_IMAGE_MIMES = ("image/png", "image/jpeg", "image/gif", "image/webp")
_REPR_METHODS = (("text/html", "_repr_html_"), ("image/svg+xml", "_repr_svg_"), ("image/png", "_repr_png_"),
                 ("image/jpeg", "_repr_jpeg_"), ("text/markdown", "_repr_markdown_"),
                 ("application/json", "_repr_json_"), ("text/latex", "_repr_latex_"))


def _mime_bundle(value: Any) -> dict[str, Any]:
    """The value's views by mime type: ``_repr_mimebundle_`` (Altair, IPython objects) first, then
    each ``_repr_*_`` method."""
    bundle: dict[str, Any] = {}
    fn = getattr(value, "_repr_mimebundle_", None)
    if callable(fn):
        try:
            got = fn(include=None, exclude=None)
        except TypeError:
            got = fn()
        except Exception as exc:  # noqa: BLE001 - a broken view falls back to the others
            _log.info("_repr_mimebundle_ failed: %s", describe(exc))
            got = None
        if isinstance(got, tuple):
            got = got[0]
        if isinstance(got, dict):
            bundle.update({k: v for k, v in got.items() if v is not None})
    for mime, method in _REPR_METHODS:
        fn = getattr(value, method, None)
        if mime in bundle or not callable(fn):
            continue
        try:
            rendered = fn()
        except Exception as exc:  # noqa: BLE001 - as above
            _log.info("%s failed: %s", method, describe(exc))
            continue
        if isinstance(rendered, tuple):
            rendered = rendered[0]
        if rendered is not None and rendered != "":
            bundle[mime] = rendered
    return bundle


def _b64(data: Any) -> str:
    if isinstance(data, str):  # already base64, as Jupyter bundles carry images
        return data.replace("\n", "")
    return base64.b64encode(data).decode("ascii")


def _from_bundle(bundle: dict[str, Any]) -> dict[str, Any] | None:
    if bundle.get("text/html"):
        return {"type": "html", "html": str(bundle["text/html"])}
    if bundle.get("image/svg+xml"):
        svg = bundle["image/svg+xml"]
        return {"type": "image", "mime": "image/svg+xml",
                "data": base64.b64encode(svg.encode() if isinstance(svg, str) else svg).decode("ascii")}
    for mime in _IMAGE_MIMES:
        if bundle.get(mime):
            return {"type": "image", "mime": mime, "data": _b64(bundle[mime])}
    if bundle.get("text/markdown"):
        return {"type": "markdown", "text": str(bundle["text/markdown"])}
    if "application/json" in bundle:
        data = bundle["application/json"]
        try:
            return {"type": "json", "data": json.loads(json.dumps(data, default=str))}
        except (TypeError, ValueError):
            return {"type": "text", "text": str(data)}
    if bundle.get("text/latex"):
        return {"type": "text", "text": str(bundle["text/latex"])}
    if bundle.get("text/plain"):
        return {"type": "text", "text": str(bundle["text/plain"])}
    return None


class _Html:
    def __init__(self, html: str):
        self.html = html

    def _repr_html_(self) -> str:
        return self.html


class _Run:
    """One cell run: its outputs so far, shared between the cell's thread and ``poll``."""

    def __init__(self, count: int):
        self.count = count
        self.lock = threading.Lock()
        self._outputs: list[dict[str, Any]] = []
        self._tail: list[str] = []  # text printed since, joined onto the last stream output when read
        self.version = 0
        self.status = "running"
        self.started = time.monotonic()
        self.seconds: float | None = None
        self.thread: threading.Thread | None = None
        self.guest: str | None = None  # the notebook whose cell this is, in a shared session (Kernel.as_guest)
        self.notebook = ""  # its name, for the other notebooks in the session
        self.done = threading.Event()
        # Cancel only interrupts the cell's thread once it is inside _execute's handlers (``armed``).
        # A Cancel before that sets ``cancel_requested``, and the thread stops itself when it arms.
        self.cancel_lock = threading.Lock()
        self.armed = False
        self.cancel_requested = False
        self.watches: list[dict[str, Any]] | None = None
        self._reset()
        self.debugger: Any = None  # debugger.Debugger when the cell runs with breakpoints
        self.paused: dict[str, Any] | None = None  # where a debugged cell is paused
        self.onelake = write_queue.Stats()  # its OneLake writes: how long it waited on them, what they wrote

    def set_paused(self, paused: dict[str, Any] | None) -> None:
        with self.lock:
            self.paused = json.loads(json.dumps(paused, default=str)) if paused is not None else None
            self.version += 1

    def _reset(self) -> None:
        spill = getattr(self, "spill", None)
        if spill is not None:
            _drop_spill(spill, self.spill_file)
        self._outputs, self._tail, self.clear_pending = [], [], False
        self.stream_chars = self.rich_chars = self.spill_chars = 0
        self.spill: Path | None = None
        self.spill_file: Any = None
        self.overflowed = self.rich_dropped = False

    @property
    def outputs(self) -> list[dict[str, Any]]:
        with self.lock:
            self._join()
            return self._outputs

    def _join(self) -> None:
        """Join the printed text kept in pieces; one join instead of copying the text on every print."""
        if self._tail:
            self._outputs[-1] = {"type": "stream", "text": self._outputs[-1]["text"] + "".join(self._tail)}
            self._tail = []

    def _append(self, output: dict[str, Any]) -> None:
        self._join()
        self._outputs.append(output)

    def _note(self, text: str) -> None:
        self._append({"type": "text", "text": text})

    def write(self, text: str) -> None:
        if not text:
            return
        with self.lock:
            if self.clear_pending:
                self._reset()
            if self.overflowed:
                self._spill(text)
                return
            room = STREAM_CHARS - self.stream_chars
            if len(text) > room:
                self._overflow(text, room)
            else:
                self._stream(text)
                self.stream_chars += len(text)
            self.version += 1

    def _stream(self, text: str) -> None:
        if self._outputs and self._outputs[-1]["type"] == "stream":
            self._tail.append(text)
        else:
            self._outputs.append({"type": "stream", "text": text})

    def _overflow(self, text: str, room: int) -> None:
        """The first write past STREAM_CHARS: keep what fits, put all printed text in a file."""
        if room:
            self._stream(text[:room])
        self.stream_chars, self.overflowed = STREAM_CHARS, True
        self._join()
        shown = "".join(o["text"] for o in self._outputs if o["type"] == "stream")
        try:
            folder = os.environ.get(SPILL_DIR_ENV) or None
            if folder:  # another engine's start-up sweep may have removed it while it was empty
                os.makedirs(folder, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=SPILL_PREFIX, suffix=".txt", dir=folder)
            self.spill, self.spill_file = Path(name), os.fdopen(fd, "w", encoding="utf-8", errors="replace")
            with _spills_lock:
                _spills.add(self.spill)
            where = f" All of it, up to {SPILL_CHARS // 1_000_000} million characters, is in {name}."
        except OSError:
            where = " The rest wasn't kept."
        self._note(f"Output truncated: this cell printed more than {STREAM_CHARS:,} characters.{where}")
        self._spill(shown[:len(shown) - room] + text)

    def _spill(self, text: str) -> None:
        """Printed text after the cap goes to the file, until it holds SPILL_CHARS."""
        if self.spill_file is None:
            return
        room = SPILL_CHARS - self.spill_chars
        try:
            self.spill_file.write(text[:room])
            self.spill_chars += min(len(text), room)
            if len(text) > room:
                self.spill_file.write(f"\n[KratosForge stopped keeping this cell's output at {SPILL_CHARS:,} characters.]\n")
                self.spill_file.close()
                self.spill_file = None
        except OSError:
            with contextlib.suppress(OSError):
                self.spill_file.close()
            self.spill_file = None

    def add(self, output: dict[str, Any]) -> None:
        size = len(json.dumps(output, default=str))
        with self.lock:
            if self.clear_pending:
                self._reset()
            if output.get("type") == "error":  # an error always shows, whatever came before it
                self._append(output)
            elif self.rich_chars + size > RICH_CHARS:
                if self.rich_dropped:
                    return
                self.rich_dropped = True
                self._note(f"Output truncated: this cell's tables, charts and HTML passed {RICH_CHARS // 1_000_000} MB, "
                           "so later ones aren't shown.")
            else:
                self.rich_chars += size
                self._append(output)
            self.version += 1

    def replace(self, display_id: str, output: dict[str, Any]) -> None:
        """update_display: every output shown with ``display_id`` becomes ``output``, in place. An
        update that would take the cell past RICH_CHARS shows a line saying so instead."""
        with self.lock:
            self._join()
            hits = [i for i, o in enumerate(self._outputs) if o.get("display_id") == display_id]
            if not hits:
                return
            self.rich_chars, output = _replace_capped(self._outputs, hits, output, self.rich_chars)
            for i in hits:
                self._outputs[i] = output
            self.version += 1

    def clear(self, wait: bool = False) -> None:
        """IPython's clear_output: now, or (``wait``) when the next output arrives, so a loop that
        redraws doesn't flicker."""
        with self.lock:
            if wait:
                self.clear_pending = True
            else:
                self._reset()
                self.version += 1

    def finish(self, status: str) -> None:
        with self.lock:
            if self.spill_file is not None:
                with contextlib.suppress(OSError):
                    self.spill_file.close()
                self.spill_file = None
            self.status = status
            self.seconds = round(time.monotonic() - self.started, 2)
            self.version += 1
        self.done.set()

    def snapshot(self, known_version: int = -1) -> dict[str, Any]:
        progress = _query_progress(self.thread.ident if self.thread else None) if self.status == "running" else None
        with self.lock:
            changed = self.version != known_version
            snap = {"status": self.status, "execution_count": self.count, "version": self.version,
                    "seconds": self.seconds if self.seconds is not None else round(time.monotonic() - self.started, 2),
                    "outputs": None}
            if changed:
                self._join()
                snap["outputs"] = list(self._outputs)
            if self.watches is not None:
                snap["watches"] = list(self.watches)
            if progress is not None:
                snap["progress"] = progress
            if self.debugger is not None:
                snap["paused"] = self.paused
            if (onelake := self.onelake.as_dict()) is not None:
                snap["onelake"] = onelake
            return snap


class _OutputSink:
    """Where a block inside ``with out:`` writes: the Output widget (widgets.py), not the cell."""

    def __init__(self, out: Any):
        self.out = out

    def write(self, text: str) -> None:
        self.out._write(text)

    def add(self, output: dict[str, Any]) -> None:
        self.out._add(output)

    def replace(self, display_id: str, output: dict[str, Any]) -> None:
        self.out._replace(display_id, output)

    def clear(self, wait: bool = False) -> None:
        self.out.clear_output(wait)


def _replace_capped(outputs: list[dict[str, Any]], hits: list[int], output: dict[str, Any],
                    rich: int) -> tuple[int, dict[str, Any]]:
    """The rich count after outputs[hits] become ``output``, and what they become: ``output``, or a
    line saying the update passed RICH_CHARS (it keeps the id, so a smaller update still lands)."""
    old = sum(len(json.dumps(outputs[i], default=str)) for i in hits)
    size = len(json.dumps(output, default=str))
    if rich - old + size * len(hits) > RICH_CHARS:
        output = {"type": "text", "display_id": output.get("display_id"),
                  "text": f"Output truncated: this update would take the output's tables, charts and HTML past "
                          f"{RICH_CHARS // 1_000_000} MB, so it isn't shown."}
        size = len(json.dumps(output, default=str))
    return rich - old + size * len(hits), output


WIDGET_SECONDS = 60
MAX_DISPLAY_IDS = 100  # display ids whose outputs an update can still reach
WIDGET_GONE = "This control is no longer held by the kernel (it restarted). Run the cell again."


def _query_progress(thread: int | None) -> float | None:
    """The running ``spark`` query's progress, when the cell's own thread is the one waiting on it:
    a query a widget handler or a thread the cell started doesn't show under the cell. Without
    importing the Spark API for a cell that never used it."""
    module = sys.modules.get("fabric_desktop.spark_local")
    if module is None or thread is None:
        return None
    # The cell's thread is waiting on the query when the Spark API, or the table output that runs a
    # DataFrame shown as the cell's result or a %%sql cell (as_table), is on its stack.
    frame = sys._current_frames().get(thread)
    while frame is not None:
        name = str(frame.f_globals.get("__name__", ""))
        if name.startswith(("duckdb", "fabric_desktop.spark")) or (name == __name__ and frame.f_code.co_name == "as_table"):
            return module.progress()
        frame = frame.f_back
    return None


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


def _to_running_cell(output: dict[str, Any]) -> bool:
    """Adds an output to the cell running on this thread; False when none is."""
    run = _routers()[0].targets.get(threading.get_ident())
    if run is None:
        return False
    run.add(output)
    return True


def _cell_running() -> bool:
    return bool(_routers()[0].targets)


chart_output.install(_to_running_cell, _cell_running)  # bokeh's show() draws in the cell


class Kernel:
    def __init__(self, token: Callable[[str], str] | None = None,
                 load_notebook: Callable[[str], list[dict[str, Any]]] | None = None,
                 variable_libraries: Callable[[str], dict[str, str] | None] | None = None):
        self.token = token
        self.load_notebook = load_notebook
        # Asks the engine for something (name, *args), when the kernel runs in its own process: %pip.
        self.engine_call: Callable[..., Any] | None = None
        self.variable_libraries = variable_libraries
        self.notebook_name = ""
        # The Widgets bar's values for the open notebook; each cell run starts with them (start).
        self.widget_values: dict[str, str] = {}
        self.resources_dir = ""  # the running notebook's resources folder, "" for none (notebook_resources)
        # "local_first": a cell's lakehouse writes upload in the background and the cell waits for
        # them at its end (write_queue); "direct": each write_table returns once it is in OneLake.
        self.onelake_writes = "local_first"
        # Set before each cell (service._start_cell); asked before every write the cell makes, by
        # the cell's thread (lakehouse.cell_write_checks). None for the app's own cells.
        self.write_check: Any = None
        self._writes: Any = None
        self._current: _Run | None = None
        self._widget_lock = threading.Lock()
        self._widget_thread: threading.Thread | None = None  # a handler stuck in native code outlives its event
        self._local = threading.local()
        self._display_sinks: OrderedDict[str, list[Any]] = OrderedDict()  # display id -> where it was shown
        self._secrets: set[str] = set()
        self._tasks: list[str] = []
        self.task_values: dict[str, dict[str, Any]] = {}
        self.frames = Frames()
        self._guests: dict[str, dict[str, Any]] = {}  # each guest notebook's variables (as_guest)
        self._last_runs: dict[str | None, _Run] = {}  # each notebook's last cell in a shared session, for poll
        self.restart()

    # ---- namespace

    # A shared session (kernel_sessions.share): other notebooks run in this kernel, with this
    # spark, its temp views and settings, but each with variables of its own, as in Fabric's
    # high-concurrency sessions. A request for a guest runs under as_guest(name), and the cell it
    # starts keeps that name, so ``namespace`` is the guest's own.

    @property
    def namespace(self) -> dict[str, Any]:
        guest = getattr(self._local, "guest", None)
        if not guest:
            return self._namespace
        found = self._guests.get(guest)
        if found is None:
            # Building it points sys.modules' notebookutils at the guest's copy; both copies talk
            # to this kernel, so an import in either notebook gets the same behaviour.
            found = self._guests[guest] = self._base_namespace()
        return found

    @namespace.setter
    def namespace(self, value: dict[str, Any]) -> None:
        self._namespace = value

    @contextlib.contextmanager
    def as_guest(self, notebook: str | None) -> Iterator[None]:
        """Requests in this block act for ``notebook``, a guest in this shared session (None: the host)."""
        before = getattr(self._local, "guest", None)
        self._local.guest = notebook or None
        try:
            yield
        finally:
            self._local.guest = before

    def forget_guest(self, notebook: str) -> dict[str, Any]:
        """A guest left the shared session, or restarted: its variables go; the session stays."""
        self._last_runs.pop(notebook, None)
        return {"forgotten": self._guests.pop(notebook, None) is not None}

    def _base_namespace(self) -> dict[str, Any]:
        from . import dbutils_local, notebookutils_local
        ns: dict[str, Any] = {"__name__": "__main__", "input": _no_input, "display": self._display,
                              "displayHTML": self._display_html, "In": [""], "Out": {}}
        from . import ipython_display
        ipython_display.install(ipython_display.build(self._display, self._clear_output))
        from . import widgets as ipywidgets_local
        ipywidgets_local.install(self._display, self._capture_output)
        host = notebookutils_local.Host(
            token=self.token, run_notebook=self.run_notebook, notebook_name=lambda: self.notebook_name,
            variables=self.variable_libraries, display=self._display, tasks=self._tasks,
            task_values=self.task_values, resources=lambda: self.resources_dir)
        utils = notebookutils_local.build(host)
        get_secret = utils.credentials.getSecret

        def remembered_secret(*args: Any, **kwargs: Any) -> str:
            value = get_secret(*args, **kwargs)
            self._secrets.add(value)
            return value
        utils.credentials.getSecret = remembered_secret

        def restart_python() -> None:
            from .dbutils_local import RestartPython
            raise RestartPython()
        # Fabric's session.restartPython and stop: on this PC both restart Python after the cell.
        vars(utils).update(session=types.SimpleNamespace(
            restartPython=restart_python, stop=restart_python,
            help=lambda *_a: print("notebookutils.session on this PC: restartPython, stop (both restart Python)")))
        sys.modules["notebookutils"] = utils
        sys.modules["mssparkutils"] = utils
        dbutils = dbutils_local.build(host, utils)
        _databricks_runtime(dbutils)
        ns.update(notebookutils=utils, mssparkutils=utils, dbutils=dbutils)
        try:
            from . import spark_local
            spark_local.install(ns)
        except Exception as exc:  # noqa: BLE001 - plain Python still works without it
            print(f"spark is not available on this PC: {exc}", file=sys.__stderr__)
        return ns

    def restart(self) -> None:
        if self._current is not None and not self._current.done.is_set():
            self.interrupt()
        self._widget_thread = None  # a stuck handler keeps the old namespace; the new one is free
        if hasattr(self, "_namespace"):  # not the first start: another kernel in this process may own files
            remove_spills()
        self._reset()

    def _handler_alive(self) -> bool:
        return self._widget_thread is not None and self._widget_thread.is_alive()

    def _reset(self) -> None:
        """A fresh namespace and spark session; restart() once nothing is running."""
        with contextlib.suppress(Exception):
            from . import spark_local
            spark_local.reset()
        from . import lakehouse
        lakehouse.reads_reset()
        self.task_values.clear()
        self._guests.clear()  # a restart of the session clears every notebook's variables in it
        self._last_runs.clear()
        self.namespace = self._base_namespace()
        self.count = 0
        self.frames.clear()

    def _show(self, value: Any) -> dict[str, Any]:
        """``display(value)``; a table also keeps its whole result for the result viewer (frames.py)."""
        from . import widgets
        if isinstance(value, widgets.Widget):
            return {"type": "widget", "model": value.model_id, "view": widgets.view(value)}
        out = display(value)
        if out["type"] == "table":
            out["frame"] = self.frames.keep(value)
            if type(value).__module__.startswith("duckdb.experimental.spark"):
                out["spark"] = True  # it has a query to profile (spark_local.query_profile)
        return out

    # ---- ipywidgets (widgets.py): a control changed in the app

    @contextlib.contextmanager
    def _capture_output(self, out: Any) -> Iterator[None]:
        """``with out:`` for an Output widget: this thread's prints, displays and figures go to it."""
        stdout, stderr = _routers()
        ident = threading.get_ident()
        sink = _OutputSink(out)
        saved = (stdout.targets.get(ident), stderr.targets.get(ident), getattr(self._local, "run", None))
        stdout.targets[ident] = stderr.targets[ident] = sink  # type: ignore[assignment]
        self._local.run = sink
        try:
            yield
        finally:
            self._flush_figures(sink)  # type: ignore[arg-type]
            for router, before in ((stdout, saved[0]), (stderr, saved[1])):
                if before is None:
                    router.targets.pop(ident, None)
                else:
                    router.targets[ident] = before
            self._local.run = saved[2]

    def ipywidget_event(self, model: str, event: dict[str, Any], root: str | None = None) -> dict[str, Any]:
        """Apply a control's change and run its handlers, as a cell runs: on its own thread, with
        what they print and display coming back as ``outputs``, for at most ``WIDGET_SECONDS``."""
        from . import widgets
        if self._current is not None and not self._current.done.is_set():
            raise RuntimeError("A cell is running. The control works again when it finishes.")
        if self._handler_alive() or not self._widget_lock.acquire(blocking=False):
            raise RuntimeError("The last change is still running. Try again when it finishes, or restart the kernel.")
        try:
            run = _Run(0)
            status = {"value": "ok"}

            def work() -> None:
                stdout, stderr = _routers()
                ident = threading.get_ident()
                stdout.targets[ident] = stderr.targets[ident] = run
                self._local.run = run
                try:
                    self._patch_pyplot(run)
                    widgets.handle(model, event)
                    self._flush_figures(run)
                except widgets.WidgetGone:  # not any LookupError: a handler's own KeyError is its error
                    status["value"] = "gone"
                except KeyboardInterrupt:
                    status["value"] = "cancelled"
                    run.add({"type": "text", "text": f"Stopped: the handler ran longer than {WIDGET_SECONDS} seconds."})
                except Exception as exc:  # noqa: BLE001 - the handler's error shows under the control
                    status["value"] = "error"
                    run.add(self._error(exc))
                finally:
                    stdout.targets.pop(ident, None)
                    stderr.targets.pop(ident, None)
                    self._local.run = None
            thread = threading.Thread(target=work, daemon=True, name="widget-event")
            self._widget_thread = thread
            thread.start()
            thread.join(WIDGET_SECONDS)
            if thread.is_alive():
                ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(cast(int, thread.ident)), ctypes.py_object(KeyboardInterrupt))
                thread.join(2.0)
                if thread.is_alive():  # stuck in native code (a sleep, a query): stop waiting for it
                    status["value"] = "cancelled"
                    run.add({"type": "text", "text": f"Stopped waiting: the handler ran longer than {WIDGET_SECONDS} "
                             "seconds. It ends when the native call it's in returns; until then cells and controls "
                             "wait, or restart the kernel."})
            if status["value"] == "gone":
                return {"gone": True, "message": WIDGET_GONE}
            w = widgets.registry.get(root or model)
            return {"status": status["value"], "outputs": run.snapshot()["outputs"],
                    "view": widgets.view(w) if w is not None else None}
        finally:
            self._widget_lock.release()

    def frame_page(self, frame: str, offset: int = 0, limit: int = 100, sort: int | None = None,
                   desc: bool = False, q: str = "") -> dict[str, Any]:
        return self.frames.page(frame, offset, limit, sort, desc, q)

    def frame_chart(self, frame: str, **spec: Any) -> dict[str, Any]:
        return self.frames.chart(frame, **spec)

    def frame_profile(self, frame: str) -> dict[str, Any]:
        return self.frames.profile(frame)

    def frame_wrangle(self, frame: str, steps: list[dict[str, Any]], name: str = "df") -> dict[str, Any]:
        return self.frames.wrangle(frame, steps, name)

    def frame_query_profile(self, frame: str) -> dict[str, Any]:
        return self.frames.query_profile(frame)

    def frame_download(self, frame: str, format: str, path: str) -> dict[str, Any]:  # noqa: A002 - RPC name
        return self.frames.download(frame, format, path)

    def _display_html(self, html: str | bytes) -> None:
        """displayHTML(html), as Fabric and Databricks have: shown in the cell's sandboxed frame."""
        if isinstance(html, (bytes, bytearray)):  # read from a file in binary, as open(..., 'rb')
            html = bytes(html).decode("utf-8", errors="replace")
        self._display(_Html(str(html)))

    def _clear_output(self, wait: bool = False) -> None:
        run = getattr(self._local, "run", None)
        if run is not None:
            run.clear(wait)

    def _keep_display_sink(self, did: str, sink: Any) -> None:
        """Where a display id was shown, so an update from elsewhere (outside ``with out:``) finds it."""
        # A finished cell's outputs are what the app already has: only live places are kept.
        sinks = [x for x in self._display_sinks.get(did, []) if getattr(x, "status", "running") == "running"]
        if all(x is not sink for x in sinks):
            sinks.append(sink)
        self._display_sinks[did] = sinks
        self._display_sinks.move_to_end(did)
        while len(self._display_sinks) > MAX_DISPLAY_IDS:
            self._display_sinks.popitem(last=False)

    def _display(self, *values: Any, summary: bool = False, raw: bool = False, display_id: Any = None,
                 update: bool = False, **_kw: Any) -> Any:
        """display(), as IPython's: with ``display_id`` it returns a handle, and ``update=True``
        (update_display, handle.update) redraws the outputs shown with that id in place, wherever
        they were shown in this cell or in an Output widget. A display a finished cell showed keeps
        what it showed."""
        from .ipython_display import DisplayHandle
        did = uuid.uuid4().hex if display_id is True else (None if display_id in (None, False) else str(display_id))
        if update and did is None:
            raise TypeError("update_display needs a display_id.")
        run = getattr(self._local, "run", None)
        for value in values:
            if raw and isinstance(value, dict):  # display(bundle, raw=True): a mime bundle, as IPython reads it
                out = _from_bundle(value) or {"type": "text", "text": str(value.get("text/plain", ""))}
            else:
                out = self._show(value)
            if summary and out["type"] == "table":
                out["summary"] = True
            if did is not None:
                out = {**out, "display_id": did}
            if run is None:
                print(out.get("text", out))
            elif update and did is not None:
                for sink in {id(x): x for x in [run, *self._display_sinks.get(did, [])]}.values():
                    sink.replace(did, out)
            else:
                run.add(out)
                if did is not None:
                    self._keep_display_sink(did, run)
        return DisplayHandle(did, self._display) if did is not None and not update else None

    # ---- running a cell

    def check_free(self) -> None:
        """Refuse a cell while one runs: in a shared session, say whose it is."""
        run = self._current
        if run is not None and not run.done.is_set():
            if run.guest != getattr(self._local, "guest", None):
                raise RuntimeError(f"{run.notebook or 'Another notebook'} is running a cell in this shared session. "
                                   "Its notebooks run one cell at a time; wait for it, or Cancel it there.")
            raise RuntimeError("A cell is already running. Wait for it, or Cancel it.")

    def start(self, code: str, watches: list[str] | None = None, breakpoints: list[int] | None = None,
              step: bool = False) -> dict[str, Any]:
        """Start ``code`` on its own thread; ``poll`` reports its output as it comes.

        ``watches`` are expressions evaluated after the cell, in its namespace and on its thread
        (so Cancel and the cell time limit cover them); their values come back in ``watches``.
        ``breakpoints`` (line numbers in the cell) or ``step`` (pause at the first line) run the
        cell under the debugger (debugger.py): ``poll`` reports ``paused`` and ``debug`` resumes."""
        self.check_free()
        if self._widget_lock.locked() or self._handler_alive():
            raise RuntimeError("A widget's handler is still running. Run the cell when it finishes, or restart the kernel.")
        widgets = getattr(self.namespace.get("dbutils"), "widgets", None)
        if widgets is not None:
            widgets.set_in_app = dict(self.widget_values)
        from .debugger import Debugger, check_breakpoints
        lines = check_breakpoints(breakpoints)
        self.count += 1
        run = _Run(self.count)
        run.guest = getattr(self._local, "guest", None)
        run.notebook = self.notebook_name
        if lines or step:
            from .redact import secrets
            run.debugger = Debugger(lines, step, self._frame_row, redact=secrets, watches=watches,
                                    on_change=run.set_paused)
            run.debugger.skip = set(self._base_namespace_keys())
        self._current = self._last_runs[run.guest] = run
        run.thread = threading.Thread(target=self._execute, args=(code, run, list(watches or [])), daemon=True,
                                      name=f"cell-{self.count}")
        if self.write_check is not None:
            from . import lakehouse
            lakehouse.cell_write_checks[run.thread] = self.write_check
        run.thread.start()
        return run.snapshot()

    def poll(self, known_version: int = -1, wait: float = 0.0) -> dict[str, Any]:
        """The running (or last) cell: status and, when they changed since ``known_version``, its outputs.

        ``wait`` seconds is how long to wait for news before answering, so the app can poll slowly."""
        run = self._current
        guest = getattr(self._local, "guest", None)
        if run is None or run.guest != guest:
            # Another notebook's cell, in a shared session, isn't this notebook's to show: it sees its
            # own last cell, and the session busy all the same (kernel_sessions keeps a busy session).
            busy = {"session_running": True} if run is not None and not run.done.is_set() else {}
            mine = self._last_runs.get(guest)
            if mine is not None:
                return mine.snapshot(known_version) | busy
            return {"status": "idle", "execution_count": self.count, "version": 0, "seconds": 0, "outputs": []} | busy
        deadline = time.monotonic() + min(wait, 2.0)
        while run.version == known_version and not run.done.is_set() and time.monotonic() < deadline:
            run.done.wait(0.05)
        return run.snapshot(known_version)

    def interrupt(self) -> dict[str, Any]:
        """Stop the running cell, keeping the namespace. Native code (a DuckDB query) is asked to stop too."""
        run = self._current
        if run is None or run.done.is_set() or run.thread is None or run.guest != getattr(self._local, "guest", None):
            return {"interrupted": False}
        # Paused: the cell's thread ends it itself. If it doesn't (busy evaluating an expression),
        # interrupt it the usual way.
        if run.debugger is not None and run.debugger.stop() and run.done.wait(0.5):
            return {"interrupted": True, "stopped": True}
        with contextlib.suppress(Exception):
            from . import spark_local
            spark_local.interrupt()
        # A cell that was just started may not be in _execute yet: an interrupt raised there (in the
        # thread's start-up, or before the handlers) would end the thread without finishing the run.
        # Such a cell stops itself as soon as it gets there.
        # The interrupt is sent under the lock, so the cell can't disarm between the check and the send.
        with run.cancel_lock:
            run.cancel_requested = True
            if run.armed:
                ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(cast(int, run.thread.ident)), ctypes.py_object(KeyboardInterrupt))
        run.done.wait(2.0)
        return {"interrupted": True, "stopped": run.done.is_set()}

    def debug(self, action: str, expression: str | None = None) -> dict[str, Any]:
        """Resume a paused cell (continue, step, next, out) or evaluate ``expression`` where it is paused."""
        run = self._current
        if run is None or run.done.is_set() or run.debugger is None or run.guest != getattr(self._local, "guest", None):
            raise RuntimeError("No cell is running under the debugger.")
        return run.debugger.command(action, expression)

    def run(self, code: str) -> dict[str, Any]:
        """Run ``code`` to the end and return its result (used by tests and scripts)."""
        self.write_check = None  # the app's own run
        self.start(code)
        run = self._current
        assert run is not None
        run.done.wait()
        snap = run.snapshot()
        return {"status": snap["status"], "execution_count": snap["execution_count"], "outputs": snap["outputs"],
                **({"onelake": snap["onelake"]} if "onelake" in snap else {})}

    def _execute(self, code: str, run: _Run, watches: list[str] | None = None) -> None:
        from .dbutils_local import RestartPython
        from .notebookutils_local import NotebookExit
        out, err = _routers()
        ident = threading.get_ident()
        out.targets[ident] = err.targets[ident] = run
        self._local.run = run
        self._local.guest = run.guest  # this thread is the cell's: its namespace is its notebook's
        status = "error"
        restart = False
        history.record_input(self.namespace, run.count, code)
        writes = None
        write_queue.cell_writes.stats = run.onelake
        try:
            with run.cancel_lock:
                run.armed = True
                cancelled = run.cancel_requested
            if cancelled:  # Cancel came before the cell got here
                run.add({"type": "text", "text": "Cancelled. Variables are kept."})
                raise KeyboardInterrupt
            writes = self._write_queue()
            try:
                if run.debugger is not None:
                    sys.settrace(run.debugger.trace)
                try:
                    status = self._exec(code, self.namespace, run, run.debugger)
                finally:
                    if run.debugger is not None:
                        sys.settrace(None)
                self._flush_figures(run)
            except NotebookExit as done:
                status = "ok"
                restart = isinstance(done, RestartPython)
            except KeyboardInterrupt:
                status = "cancelled"
                run.add({"type": "text", "text": "Cancelled. Variables are kept."})
            if writes is not None:
                status = self._finish_writes(writes, run, status)
            if watches and status != "cancelled":
                # What a watch prints goes nowhere: the cell's output is the cell's.
                out.targets[ident] = err.targets[ident] = _Run(0)
                self._watch(watches, run)
        except KeyboardInterrupt:  # an interrupt that landed while the first one was being handled
            status = "cancelled"
        finally:
            try:
                with run.cancel_lock:  # from here a Cancel waits for the run to finish instead of raising in it
                    run.armed = False
            except KeyboardInterrupt:  # one sent just before the disarm, raised as the cell takes the lock
                status = "cancelled"
            if writes is not None:
                write_queue.cell_writes.queue = None
                writes.flush(cancel=True)  # only left after a second Cancel: drop what hasn't started
            write_queue.cell_writes.stats = None
            out.targets.pop(ident, None)
            err.targets.pop(ident, None)
            self._local.run = None
            if restart and run.guest:
                self._guests.pop(run.guest, None)  # a guest restarts only its own variables
                run.add({"type": "text", "text": "Python restarted for this notebook. Its variables were cleared; "
                                                 "the shared session goes on."})
            elif restart:
                self._reset()  # this cell is the one running, so there is nothing to interrupt
                run.add({"type": "text", "text": "Python restarted. Variables were cleared."})
            run.finish(status)

    def _write_queue(self) -> Any:
        """This cell's background writes, when its notebook writes local first (lakehouse.write_table)."""
        if self.onelake_writes != "local_first" or getattr(self, "_proof", None) is not None:
            write_queue.cell_writes.queue = None
            return None
        if self._writes is None:
            self._writes = write_queue.Queue()
        write_queue.cell_writes.queue = self._writes
        return self._writes

    def _finish_writes(self, writes: Any, run: _Run, status: str) -> str:
        """Wait for the cell's writes to reach OneLake: the cell is done when they are, and fails if one did."""
        cancelled = status == "cancelled"
        try:
            with run.onelake.waiting():
                failed = writes.flush(cancel=cancelled)
        except KeyboardInterrupt:
            failed = writes.flush(cancel=True)
            cancelled = True
        if not failed:
            return "cancelled" if cancelled else status
        if not cancelled:
            self._emit(run, {"type": "error", "ename": "WriteFailed", "traceback": "",
                             "evalue": "A table write didn't reach OneLake:\n" + "\n".join(failed)})
            return "error"
        run.add({"type": "text", "text": "\n".join(failed)})
        return "cancelled"

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

    def _exec(self, code: str, namespace: dict[str, Any], run: _Run | None, debugger: Any = None) -> str:
        """Run one cell's code in ``namespace``; returns "ok" or "error". ``debugger`` traces the
        code compiled here (not %run notebooks), so its line numbers are the cell's own."""
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
        if cell.language == "tsql":
            return self._tsql(cell, run)
        if cell.language == "writefile":
            return self._writefile(cell, run)
        if cell.language == "javascript":
            self._emit(run, {"type": "error", "ename": "NotSupported", "traceback": "", "evalue":
                             "%%javascript doesn't run here: cell output is shown in a sandboxed frame without scripts. "
                             "Use %%html for markup, or display() a chart."})
            return "error"
        if cell.language == "configure":
            return self._guarded(run, lambda: print(configure_report(cell.body)))
        if cell.language != "python":
            self._emit(run, {"type": "error", "ename": "RunsInFabric", "evalue":
                             "Scala, R and .NET cells need Fabric's Spark. Switch Runs on to Fabric to run them.",
                             "traceback": ""})
            return "error"
        code = magics.rewrite(cell.body)
        namespace.setdefault(magics.CALL, self._magic)
        if getattr(self, "_autoreload", None) is not None:
            self._autoreload.check(namespace)  # %autoreload: your edited modules, before the cell runs
        self._patch_pyplot(run)
        if cell.header == "%%time":
            started = time.monotonic()
            status = self._exec_python(code, namespace, run, debugger)
            print(f"Wall time: {_duration(time.monotonic() - started)}")
            return status
        if cell.header == "%%timeit":
            return self._guarded(run, lambda: self._timeit(cell.args, code, namespace))
        if cell.header == "%%capture":
            return self._capture(cell.args, code, namespace, run, debugger)
        return self._exec_python(code, namespace, run, debugger)

    def _exec_python(self, code: str, namespace: dict[str, Any], run: _Run | None, debugger: Any = None) -> str:
        from .dbutils_local import RestartPython
        from .notebookutils_local import NotebookExit
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=".*non-interactive.*")
                tree = ast.parse(code, _CELL_FILE)
                if self.resources_dir and os.path.isdir(self.resources_dir):  # a notebook with resources
                    tree = ast.fix_missing_locations(_BuiltinPaths(self.resources_dir).visit(tree))
                end = tree.body[-1] if tree.body else None
                last = end if isinstance(end, ast.Expr) else None
                if last is not None:
                    tree.body.pop()
                body = compile(tree, _CELL_FILE, "exec")
                tail = compile(ast.Expression(last.value), _CELL_FILE, "eval") if last is not None else None
                if debugger is not None:
                    debugger.add(body)
                    if tail is not None:
                        debugger.add(tail)
                exec(body, namespace)
                value = None
                if tail is not None:
                    value = eval(tail, namespace)
                if value is not None:
                    self._emit(run, self._show(value))  # shown first, as IPython does, so Out isn't in itself
                    if namespace is self.namespace and not getattr(self._local, "depth", 0):
                        history.record_output(namespace, self.count, value)
                    else:
                        namespace["_"] = namespace[history.LAST] = value
        except NotebookExit as done:
            if not isinstance(done, RestartPython):
                print(f"Notebook exited with value: {done.value}")
                namespace["__fd_exit__"] = done.value
            raise
        except (Exception, SystemExit) as exc:  # a cell must never take the kernel down
            self._emit(run, self._error(exc))
            return "error"
        return "ok"

    def _guarded(self, run: _Run | None, fn: Callable[[], Any]) -> str:
        try:
            fn()
        except (Exception, SystemExit) as exc:  # a cell must never take the kernel down
            self._emit(run, self._error(exc))
            return "error"
        return "ok"

    def _timeit(self, args: str, code: str, namespace: dict[str, Any]) -> None:
        """``%timeit`` / ``%%timeit``: runs the code many times and prints IPython's summary.
        ``-n N`` loops per run and ``-r R`` runs; by default enough loops for 0.2 s, 7 runs."""
        import statistics
        import timeit
        flags = dict(re.findall(r"-([nr])\s*(\d+)", args))
        if other := re.sub(r"-[nr]\s*\d+", "", args).split():
            raise ValueError(f"%timeit {other[0]} isn't supported here. Use -n N (loops per run) and -r R (runs).")
        if any(int(v) < 1 for v in flags.values()):
            raise ValueError("%timeit -n and -r need a number of 1 or more.")
        timer = timeit.Timer(stmt=code, globals=namespace)
        number = int(flags["n"]) if "n" in flags else timer.autorange()[0]
        times = [t / number for t in timer.repeat(repeat=int(flags.get("r", 7)), number=number)]
        spread = statistics.stdev(times) if len(times) > 1 else 0.0
        print(f"{_duration(statistics.mean(times))} ± {_duration(spread)} per loop (mean ± std. dev. of "
              f"{len(times)} run{'s' * (len(times) != 1)}, {number} loop{'s' * (number != 1)} each)")

    def _capture(self, args: str, code: str, namespace: dict[str, Any], run: _Run | None, debugger: Any = None) -> str:
        """``%%capture [name]``: the cell's output is kept instead of shown; ``name.show()`` shows it.
        An error still shows, so a failing cell isn't silent."""
        captured = _Run(0)
        out, err = _routers()
        ident = threading.get_ident()
        before = out.targets.get(ident), err.targets.get(ident), getattr(self._local, "run", None)
        out.targets[ident] = err.targets[ident] = self._local.run = captured
        try:
            status = self._exec_python(code, namespace, captured, debugger)
            self._flush_figures(captured)
        finally:
            for router, target in ((out, before[0]), (err, before[1])):
                if target is None:
                    router.targets.pop(ident, None)
                else:
                    router.targets[ident] = target
            self._local.run = before[2]
        name = next((w for w in args.split() if not w.startswith("-")), None)
        if name:
            namespace[name] = CapturedOutput(captured.outputs, self)
        for output in captured.outputs:
            if output["type"] == "error":
                self._emit(run, output)
        return status

    def _tsql(self, cell: Any, run: _Run | None) -> str:
        """``%%tsql`` (Fabric Python notebooks): T-SQL against a Warehouse, SQL analytics endpoint or
        SQL database. That engine is in Fabric, so here the cell is checked against its rules
        (sqlcompat, nothing sent anywhere) and stops, as a Scala cell does."""
        from . import sqlcompat
        typed = re.search(r"-type\s+(\w+)", cell.args)
        kind = (typed[1] if typed else "Warehouse").lower()
        target = {"warehouse": "warehouse", "lakehouse": "endpoint", "sqlanalyticsendpoint": "endpoint",
                  "sqldatabase": "sqldb", "sqldb": "sqldb"}.get(kind, "warehouse")
        report = sqlcompat.analyze(cell.body, target)
        found = [f"- line {d['line']}: {d['title']}. {d.get('action') or ''}".rstrip()
                 for d in report["diagnostics"] if d["severity"] in ("error", "warning")]
        named = re.search(r"-artifact\s+(\"[^\"]+\"|\S+)", cell.args)
        artifact = (named[1] if named else "the item named in -artifact").strip('"')
        print(f"Checked against {report['target']['label']} rules: {report['outcome']['text']}"
              + ("\n" + "\n".join(found) if found else ""))
        self._emit(run, {"type": "error", "ename": "RunsInFabric", "traceback": "", "evalue":
                         f"%%tsql runs in {artifact} in Fabric, which reads it with capacity there. Switch Runs on to "
                         "Fabric to run it, or check it on the SQL query page."})
        return "error"

    def _writefile(self, cell: Any, run: _Run | None) -> str:
        """``%%writefile [-a] path``: the cell's text into a file on this PC (``-a`` appends)."""
        try:
            words = magics.words(cell.args)
        except ValueError as exc:  # an unclosed quote
            self._emit(run, self._error(exc))
            return "error"
        append = "-a" in words or "--append" in words
        targets = [w for w in words if not w.startswith("-")]
        flags = [w for w in words if w.startswith("-") and w not in ("-a", "--append")]
        problem = ("%%writefile needs a file name, like: %%writefile helpers.py" if not targets else
                   f"%%writefile {flags[0]} isn't supported here. Use -a to append." if flags else
                   f"%%writefile takes one file name; quote a name with spaces, like: %%writefile \"{' '.join(targets)}\""
                   if len(targets) > 1 else None)
        if problem:
            self._emit(run, {"type": "error", "ename": "ValueError", "traceback": "", "evalue": problem})
            return "error"
        target = targets[0]
        path = Path(os.path.expandvars(os.path.expanduser(target))).resolve()
        # A cell saved on Windows has \r\n; text mode writes this PC's own line ending once.
        text = cell.body.removeprefix("\n").replace("\r\n", "\n")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            existed = path.exists()
            with path.open("a" if append else "w", encoding="utf-8") as f:
                f.write(text if text.endswith("\n") else text + "\n")
        except OSError as exc:
            self._emit(run, self._error(exc))
            return "error"
        print(f"{'Appending to' if append else 'Overwriting' if existed else 'Writing'} {path}")
        return "ok"

    @staticmethod
    def _env(line: str) -> Any:
        """``%env`` lists environment variables (values of secret-looking names hidden), ``%env NAME``
        reads one, and ``%env NAME=value`` sets one for the kernel's process."""
        line = line.strip()
        if not line:
            return {k: "hidden" if _SECRET_NAME.search(k) else v for k, v in sorted(os.environ.items())}
        m = re.match(r"([A-Za-z_]\w*)(?:\s*=\s*|\s+)(.*)$", line)
        if m is None:
            if "=" in line or re.search(r"\s", line):  # 1BAD=x or 1BAD x would read "1BAD=x" and show nothing
                bad = re.split(r"[=\s]", line, maxsplit=1)[0] or "a variable with no name"
                raise ValueError(f"%env can't set {bad}: "
                                 "a name to set starts with a letter or _ and has letters, digits and _.")
            return os.environ.get(line)  # reading takes any name, like ProgramFiles(x86) on Windows
        name, value = m.groups()
        if name.upper().startswith("FABRIC_DESKTOP_"):
            raise ValueError(f"{name} is KratosForge's own setting and can't be changed from a notebook.")
        if name.upper() in _ENV_NETWORK:
            raise ValueError(f"{name} would change where KratosForge sends its signed-in Fabric calls, or which "
                             "certificates it trusts, so %env can't set it. Set it before starting KratosForge.")
        os.environ[name] = value
        print(f"env: {name}={'hidden' if _SECRET_NAME.search(name) else value}")
        return None

    def _line_magic_help(self) -> str:
        return ("Line magics: %sql, %run (-b for a resources file), %fs, %sh, !command, %time, %timeit, %pwd, %env, %who, %whos, %reset -f, "
                "%history (with In, Out, _N and _iN), "
                "%lsmagic, %matplotlib (charts are always inline), %pip (in the notebook's environment), "
                "%autoreload and %aimport (with %load_ext autoreload), %config (skipped)\n"
                "Cell magics: %%sql, %%pyspark, %%python, %%html, %%markdown, %%configure, %%sh, %%bash, %%cmd, "
                "%%powershell, %%time, %%timeit, %%capture, %%writefile\n"
                "Databricks cells: %sql, %python, %md, %sh, %fs, %run (%scala and %r run in Fabric)")

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
            vars(plt).update(show=show)

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
        write lakehouse tables, and MERGE INTO, UPDATE and DELETE change one (Preview). A change to
        another lakehouse, or with Preview features off, runs in Fabric. A query's result stays in
        ``_sqldf`` as a spark DataFrame, as in Databricks, so the next Python cell can use it."""
        if _SQL_IN_PLACE.search(query) and not _change_runs_here(query):
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
            if m := _RUN_SCRIPT.match(line):
                return self._run_builtin(m["flags"], m["rest"])
            target, args = _run_target(line)
            return self._run_notebook_in(target, self.namespace, args)
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
        if name == "pip":
            from . import pip_magic
            guest = getattr(self._local, "guest", None)
            if guest and line.split()[:1] == ["install"]:
                raise ValueError(f"{guest} shares another notebook's session, so it uses that notebook's environment. "
                                 "Run %pip install in that notebook, or leave the shared session (Sessions) first.")
            pip_magic.run(line, self.engine_call, lambda path: pip_magic.read_text(path, self.resources_dir))
            return None
        if name == "conda":
            raise ValueError("%conda isn't on this PC. Use %pip install, which installs into the notebook's "
                             "own environment.")
        if name == "time":
            started = time.monotonic()
            try:
                value = eval(compile(line, _CELL_FILE, "eval"), self.namespace) if line.strip() else None
            except SyntaxError:  # a statement, such as x = f()
                exec(compile(line, _CELL_FILE, "exec"), self.namespace)
                value = None
            print(f"Wall time: {_duration(time.monotonic() - started)}")
            return value
        if name == "timeit":
            # options lead, as in IPython; every line matches (both parts can be empty)
            m = cast(re.Match[str], re.match(r"((?:\s*-(?:[nr]\s*\d+|[A-Za-z])(?=\s|$))*)\s*(.*)$", line))
            return self._timeit(m.group(1), m.group(2), self.namespace)
        if name == "lsmagic":
            print(self._line_magic_help())
            return None
        if name == "pwd":
            return os.getcwd()
        if name == "cd":
            raise ValueError("%cd isn't supported here: the kernel shares its working folder with the app. "
                             "Use full paths, or os.path.join with a folder of your own.")
        if name == "env":
            return self._env(line)
        if name in ("who", "whos"):
            rows = self.variables()
            if kinds := line.split():  # %who str int: only variables of those types, as in IPython
                rows = [r for r in rows if r["type"] in kinds]
            if name == "who":
                print("  ".join(r["name"] for r in rows) or "No variables yet.")
            else:
                print("\n".join(f"{r['name']:<20} {r['type']:<16} {r['size'] or r['value']}" for r in rows) or "No variables yet.")
            return None
        if name == "reset":
            if "-f" not in line.split():
                raise ValueError("%reset asks before clearing in IPython, which a notebook here can't. Use %reset -f.")
            keep = set(self._base_namespace_keys())
            for key in [k for k in self.namespace if not k.startswith("__") and k not in keep]:
                del self.namespace[key]
            history.clear_output(self.namespace)
            return None
        if name in ("history", "hist"):
            text = history.command(self.namespace, line)
            if text:
                print(text)
            return None
        if name in ("matplotlib",):
            return None  # charts are always shown in the cell
        if name in ("load_ext", "reload_ext") and line.strip() == "autoreload":
            self._reloader()  # %autoreload and %aimport work with or without this, as a convenience
            return None
        if name == "autoreload":
            self._reloader().command(line)
            return None
        if name == "aimport":
            listed = self._reloader().aimport(line, self.namespace)
            if listed:
                print(listed)
            return None
        if name in ("load_ext", "reload_ext", "unload_ext", "config"):
            what = f"the {line.strip()} extension" if name.endswith("_ext") and line.strip() else f"%{name}"
            print(f"Note: {what} needs IPython's own shell, which this kernel isn't, so this line was skipped. "
                  "%load_ext autoreload works.")
            return None
        raise ValueError(f"%{name} isn't supported on this PC. %lsmagic lists the magics that are.")

    def _reloader(self) -> Any:
        from .autoreload import Reloader
        if getattr(self, "_autoreload", None) is None:
            self._autoreload = Reloader()
        return self._autoreload

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
        if _SQL_IN_PLACE.search(query) and not _change_runs_here(query):
            raise ValueError("This MERGE, UPDATE or DELETE runs in Fabric only: on this PC they change tables of the "
                             "attached lakehouse, with Preview features on. "
                             "Put the statement in a %%sql cell; it runs once the notebook is published.")
        from . import spark_local
        return spark_local.session().sql(query)

    def run_notebook(self, name: str, timeout_seconds: int = 90, arguments: dict[str, Any] | None = None,
                     cell_timeout: float | None = None) -> Any:
        """notebookutils.notebook.run: the other notebook runs in a fresh namespace, within
        ``timeout_seconds`` (each cell within ``cell_timeout``); returns its exit value."""
        ns = self._base_namespace()
        ns[magics.CALL] = self._magic
        with _deadline(timeout_seconds, f"Notebook {name!r} didn't finish within {timeout_seconds} seconds."):
            return self._run_notebook_in(name, ns, arguments or {}, cell_timeout)

    def _run_builtin(self, flags: str, rest: str) -> Any:
        """Fabric's ``%run -b script.py`` (or ``.sql``): runs a file from the notebook's Built-in
        resources in this session. A ``.py`` file runs in the notebook's variables, after any
        ``{"name": value}`` arguments are set; a ``.sql`` file runs as a %%sql cell would."""
        from . import notebook_resources
        if "-c" in flags.split() or "--current" in flags.split():
            if getattr(self._local, "depth", 0):
                raise NotImplementedError("%run -c inside a notebook that another one runs isn't supported on "
                                          "this PC yet: it would need that notebook's own resources. Use -b, "
                                          "which reads the resources of the notebook you run.")
        file, args = _run_target(rest, keep_path=True)
        if not self.resources_dir:
            raise FileNotFoundError(f"%run -b {file}: this notebook has no resources folder yet. Add the file "
                                    "under Resources (Built-in) first.")
        path = notebook_resources.real_path(f"builtin/{file}", self.resources_dir)
        if path is None or not os.path.isfile(path):
            raise FileNotFoundError(f"%run -b: {file} isn't in this notebook's Built-in resources.")
        text = Path(path).read_text(encoding="utf-8-sig")  # Notepad may save a BOM
        kind = Path(path).suffix.lower()
        if kind == ".sql":
            if args:
                raise ValueError("%run -b with a .sql file takes no arguments.")
            if self._run_sql(text, getattr(self._local, "run", None), self.namespace) != "ok":
                raise RuntimeError(f"{file} failed; its error is shown above.")
            return None
        if kind != ".py":
            raise ValueError(f"%run -b runs .py and .sql files; {file} is neither.")
        self.namespace.update(args)
        exec(compile(text, f"builtin/{file}", "exec"), self.namespace)  # noqa: S102 - the notebook's own file
        return None

    def _run_notebook_in(self, name: str, namespace: dict[str, Any], arguments: dict[str, Any],
                         cell_timeout: float | None = None) -> Any:
        if self.load_notebook is None:
            raise RuntimeError("Other notebooks can't be opened here.")
        depth = getattr(self._local, "depth", 0)
        if depth >= MAX_RUN_DEPTH:
            raise RecursionError(f"Notebook {name!r} would be the {depth + 1}th notebook run inside another. "
                                 f"KratosForge stops at {MAX_RUN_DEPTH}, so a notebook that runs itself can't loop.")
        self._local.depth = depth + 1
        try:
            return self._run_cells(name, namespace, arguments, cell_timeout)
        finally:
            self._local.depth = depth

    def _run_cells(self, name: str, namespace: dict[str, Any], arguments: dict[str, Any],
                   cell_timeout: float | None) -> Any:
        from .dbutils_local import RestartPython
        from .notebookutils_local import NotebookExit
        cells = self.load_notebook(name.rsplit("/", 1)[-1])  # type: ignore[misc]
        has_parameters = any("parameters" in (c.get("meta") or {}).get("tags", []) for c in cells)
        widgets = getattr(namespace.get("dbutils"), "widgets", None)
        if arguments and widgets is not None:
            widgets.bind(arguments)  # Databricks notebooks read their arguments as widgets
        if arguments and not has_parameters:
            namespace.update(arguments)
        run = getattr(self._local, "run", None)
        for cell in cells:
            # Not frozen cells (Fabric skips them in a referenced run too), and not the Fabric step,
            # which runs on Fabric Spark only when started from its own notebook (fabric_step.py).
            if not notebook_cells.runs(cell, skip_fabric_step=True):
                continue
            try:
                late = f"A cell of {name!r} didn't finish within {cell_timeout or 0:g} seconds."
                with _deadline(cell_timeout, late) as fired:
                    status = self._exec(cell["source"], namespace, run)
                if fired.is_set():
                    raise RunTimeout(late)
            except NotebookExit as done:
                if isinstance(done, RestartPython):
                    raise RuntimeError(f"Notebook {name!r} asked to restart Python, which only the notebook "
                                       f"you run can do.") from None
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
            rows.append(self._describe(name, value))
        return sorted(rows, key=lambda r: r["name"].lower())

    def _describe(self, name: str, value: Any) -> dict[str, Any]:
        """A Variables pane row: name, type, size, a short preview ("hidden" for a secret), the
        memory it holds and whether variable_detail can open it."""
        hidden = self._hidden(name, value)
        kind = type(value).__name__
        try:
            return {"name": name, "type": kind, "size": _size(value),
                    "value": "hidden" if hidden else _preview(value), "memory": _memory(value),
                    "expandable": not hidden and _expandable(value)}
        except Exception:  # noqa: BLE001  # silent-ok: a value that can't be described (a DuckDB relation over a dropped view) is listed by its type, so the rest of the pane still shows
            return {"name": name, "type": kind, "size": "", "value": "hidden" if hidden else "?",
                    "memory": None, "expandable": False}

    def _frame_row(self, name: str, value: Any) -> dict[str, Any]:
        """A paused frame's local: never expandable, since variable_detail reads the namespace,
        where the same name may be another value."""
        return {**self._describe(name, value), "expandable": False}

    def variable_detail(self, name: str) -> dict[str, Any]:
        """One variable opened up, read without running code: a data frame's columns and their
        types, or the first VAR_ITEMS items of a list, tuple, set or dict. Secrets stay hidden."""
        value = self.namespace.get(name, _MISSING)
        if value is _MISSING or (name.startswith("_") and name != "_sqldf"):
            return {"name": name, "missing": True}
        out: dict[str, Any] = {"name": name, "type": type(value).__name__}
        if self._hidden(name, value):
            return {**out, "hidden": True}
        columns = _columns(value)
        if columns is not None:
            return {**out, "columns": columns}
        kind = type(value)
        if kind is dict:
            entries = [(repr(k), str(k), v) for k, v in itertools.islice(value.items(), VAR_ITEMS)]
        elif kind in (list, tuple):
            entries = [(str(i), "", v) for i, v in enumerate(value[:VAR_ITEMS])]
        elif kind in (set, frozenset):  # sorted for a stable view of the items read, not the whole set
            entries = [(str(i), "", v) for i, v in enumerate(sorted(itertools.islice(value, VAR_ITEMS), key=repr))]
        else:
            return out
        out["items"] = [{"key": key, "type": type(v).__name__, "size": _size(v),
                         "value": "hidden" if self._hidden(label, v) else _preview(v)} for key, label, v in entries]
        out["total"] = len(value)
        return out

    @staticmethod
    def _base_namespace_keys() -> tuple[str, ...]:
        return ("input", "display", "displayHTML", "notebookutils", "mssparkutils", "dbutils", "spark", "In", "Out")

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

    def inspect(self, code: str, cursor: int | None = None, call: bool = False) -> dict[str, Any] | None:
        """Hover docs for the name at ``cursor`` in ``code``, or with ``call`` the signature of the
        call the cursor is inside (intellisense.py). Reads objects like ``complete``: nothing runs."""
        from . import intellisense
        cursor = len(code) if cursor is None else cursor
        if call:
            found = intellisense.open_call(code[:cursor])
            if not found:
                return None
            name, index, kw = found
            obj = self._lookup(name)
            return None if obj is _MISSING else intellisense.call_help(obj, name, index, kw)
        at = intellisense.dotted_at(code, cursor)
        if not at:
            return None
        name, start, end = at
        obj = self._lookup(name)
        if obj is _MISSING:
            return None
        from .redact import secrets
        # Credential-shaped text in a value or a default argument is removed, as in the Variables pane.
        out = intellisense.describe(name, obj, hidden=self._hidden(name.split(".")[-1], obj),
                                    preview=lambda v: secrets(_preview(v)))
        if "signature" in out:
            out["signature"] = secrets(out["signature"])
        return {**out, "from": start, "to": end}

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
                  if f.filename not in (__file__, _WIDGETS_FILE) and not f.filename.endswith(("notebookutils_local.py",))]
        lines = traceback.format_list(frames) + traceback.format_exception_only(type(exc), exc)
        return {"type": "error", "ename": type(exc).__name__, "evalue": str(exc),
                "traceback": "Traceback (most recent call last):\n" + "".join(lines)}


_MISSING = object()
_WIDGETS_FILE = str(Path(__file__).with_name("widgets.py"))  # a control handler's error shows the user's frames only


# How many notebooks notebook.run and %run may nest, so a notebook that runs itself stops.
MAX_RUN_DEPTH = 5


class RunTimeout(TimeoutError):
    """A notebook or one of its cells ran past the timeout notebook.run or runMultiple gave it."""

    def __init__(self, message: str = "The run went past its timeout, so it was stopped."):
        super().__init__(message)


@contextlib.contextmanager
def _deadline(seconds: float | None, message: str):
    """Raise RunTimeout in this thread if the block runs longer than ``seconds`` (None or 0: no limit).

    Like Cancel, it lands between Python steps: a long DuckDB query is asked to stop too. The
    block gets an Event that is set once the time ran out (a cell reports its own errors)."""
    ident = threading.get_ident()
    fired = threading.Event()
    if not seconds or seconds <= 0:
        yield fired
        return

    def expire() -> None:
        fired.set()
        with contextlib.suppress(Exception):
            from . import spark_local
            spark_local.interrupt()
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(ident), ctypes.py_object(RunTimeout))

    timer = threading.Timer(seconds, expire)
    timer.daemon = True
    timer.start()
    try:
        yield fired
    except RunTimeout:
        raise RunTimeout(message) from None
    except Exception as exc:
        # The interrupt can surface as DuckDB's own error when it lands inside a query.
        if fired.is_set():
            raise RunTimeout(message) from exc
        raise
    else:
        # Code that catches Exception can swallow the timeout; the run still ran out of time.
        if fired.is_set():
            raise RunTimeout(message)
    finally:
        timer.cancel()
        if fired.is_set():
            # The timer may have fired as the block ended: clear the exception it left pending.
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(ident), None)


def _databricks_runtime(dbutils: Any) -> None:
    """``from databricks.sdk.runtime import dbutils`` gives the kernel's dbutils, unless the real
    Databricks SDK is installed (then it is left alone)."""
    import importlib.util
    loaded = sys.modules.get("databricks")
    if loaded is not None and not getattr(loaded, "__fd_shim__", False):
        return
    if loaded is None and importlib.util.find_spec("databricks") is not None:
        return
    root = types.ModuleType("databricks")
    root.__fd_shim__ = True  # type: ignore[attr-defined]
    sdk = types.ModuleType("databricks.sdk")
    runtime = types.ModuleType("databricks.sdk.runtime")
    runtime.dbutils = dbutils  # type: ignore[attr-defined]
    root.sdk = sdk  # type: ignore[attr-defined]
    sdk.runtime = runtime  # type: ignore[attr-defined]
    sys.modules.update({"databricks": root, "databricks.sdk": sdk, "databricks.sdk.runtime": runtime})


class _BuiltinPaths(ast.NodeTransformer):
    """``"builtin/x.csv"`` in a cell's code becomes the path of x.csv in the notebook's resources
    folder, as Fabric's working folder makes it. Rewriting the cell's string literals, rather than
    changing the working folder, keeps the engine's own folder (and other notebooks, when cells run
    inside the engine) as they were, and works the same for open(), pandas, polars and DuckDB.

    An allowlist: a string (or f-string) that starts with ``builtin/`` (or ``./builtin/``) changes
    only when it is the leftmost piece of the whole value it sits in, found by walking up through
    ``+``, ``/`` and f-strings, and that value is used on its own, as a call argument, or as the
    first argument of a join. Anything else stays as written: text after another base (which may
    be a OneLake path), ``%`` formatting, ``.format(...)``, ``str.join`` and later join arguments,
    and dict keys. The expressions inside an f-string's braces are code and are visited as such."""

    _JOINS = ("join", "joinpath")

    def __init__(self, folder: str):
        self.folder = folder
        self._parents: dict[int, ast.AST] | None = None

    def visit(self, node: ast.AST) -> Any:
        if self._parents is None:  # the tree being rewritten: where each node sits
            self._parents = {id(c): n for n in ast.walk(node) for c in ast.iter_child_nodes(n)}
        return super().visit(node)

    def _parent(self, node: ast.AST) -> ast.AST | None:
        return (self._parents or {}).get(id(node))

    @staticmethod
    def _call_name(node: ast.AST | None) -> str:
        if not isinstance(node, ast.Call):
            return ""
        return node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")

    def _starts_a_path(self, node: ast.AST) -> bool:
        """``node`` is the leftmost leaf of its whole value, and that value is a path on its own."""
        cur, up = node, self._parent(node)
        while isinstance(up, ast.BinOp):
            if not isinstance(up.op, (ast.Add, ast.Div)) or up.left is not cur:
                return False  # % formatting, or something comes before it
            cur, up = up, self._parent(up)
        if isinstance(up, ast.Dict):
            return all(k is not cur for k in up.keys)
        if isinstance(up, ast.Attribute):
            return up.attr != "format"  # "builtin/{}".format(...)
        if isinstance(up, ast.Call):
            name = self._call_name(up)
            if name == "format":
                return False
            if name in self._JOINS:
                return bool(up.args) and up.args[0] is cur
            return True
        if isinstance(up, (ast.List, ast.Tuple, ast.Set)):
            outer = self._parent(up)
            if isinstance(outer, ast.BinOp) and isinstance(outer.op, ast.Mod):
                return False
            return self._call_name(outer) not in self._JOINS + ("format",)
        return True

    def visit_Constant(self, node: ast.Constant) -> ast.AST:  # noqa: N802 - ast's name
        if isinstance(node.value, str):
            path = notebook_resources.resolve(node.value, self.folder)
            if path is not None and self._starts_a_path(node):
                return ast.copy_location(ast.Constant(path), node)
        return node

    def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.AST:  # noqa: N802 - ast's name
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                part.value = self.visit(part.value)  # the code in the braces; not its format spec
        first = node.values[0] if node.values else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and self._starts_a_path(node):
            path = notebook_resources.resolve(first.value, self.folder)
            if path is not None:
                node.values[0] = ast.copy_location(ast.Constant(path), first)
        return node


_RUN_SCRIPT = re.compile(r"^\s*(?P<flags>(?:(?:-b|--builtin|-c|--current)\s+)+)(?P<rest>.*)$", re.S)


def _run_target(line: str, keep_path: bool = False) -> tuple[str, dict[str, Any]]:
    """The notebook and arguments of a ``%run`` line, in Fabric's or Databricks' form:
    ``%run Load sales {"day": "2026-01-01", "full": true}`` (JSON or a Python dict) or
    ``%run ./Load sales $day="2026-01-01" $full=true`` (Databricks widgets, always text).
    A name may have spaces and be quoted; a folder path or .ipynb ending is dropped."""
    text = line.strip()
    if text[:1] in ("'", '"') and (end := text.find(text[0], 1)) > 0:
        name, rest = text[1:end], text[end + 1:]
    else:
        m = re.search(r"\s+(?=\{|\$\w+\s*=)", text)
        name, rest = (text[:m.start()], text[m.end():]) if m else (text, "")
    if not name:
        raise ValueError("%run needs a notebook name, like: %run Load sales")
    name = name.replace("\\", "/").rstrip("/")
    if not keep_path:  # a notebook is found by its name; a resources file keeps its folder
        name = name.rsplit("/", 1)[-1].removesuffix(".ipynb")
    rest = rest.strip()
    if not rest:
        return name, {}
    if rest.startswith("{"):
        try:
            args = json.loads(rest)
        except json.JSONDecodeError:
            try:
                args = ast.literal_eval(rest)
            except (ValueError, SyntaxError) as exc:
                raise ValueError(f"%run couldn't read the arguments {rest!r}: they're neither JSON nor a Python dict.") from exc
        if not isinstance(args, dict):
            raise ValueError("%run arguments are a {name: value} object.")
        return name, args
    pair = r"""\$(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+))"""
    pairs = re.findall(pair, rest)
    left = re.sub(pair, "", rest).strip()  # anything else would be dropped without a word
    if not pairs or left:
        raise ValueError(f"%run couldn't read the arguments {(left or rest)!r}. Use {{\"name\": value}} or $name=\"value\".")
    return name, {k: a or b or c for k, a, b, c in pairs}


# What Fabric's %%configure takes (Livy session settings), and what each means on this PC.
_CONFIGURE_KEYS = {
    "driverMemory": "memory", "driverCores": "cores", "executorMemory": "memory", "executorCores": "cores",
    "numExecutors": "cores", "conf": "conf", "defaultLakehouse": "lakehouse", "mountPoints": "mounts",
    "sessionTimeoutInSeconds": "timeout", "useStarterPool": "pool", "useWorkspacePool": "pool",
    "environment": "environment", "jars": "packages", "pyFiles": "packages", "files": "packages", "archives": "packages",
    "name": "pool", "kind": "pool", "proxyUser": "pool", "heartbeatTimeoutInSecond": "timeout",
}


def configure_report(body: str) -> str:
    """A %%configure cell on this PC: its JSON is checked as Fabric would, and the report says what
    each setting does in Fabric and why nothing changes here. Nothing on this PC is changed."""
    text = body.strip()
    flags = re.match(r"^(-f|--force)?\s*", text)
    text = text[flags.end():] if flags else text
    if not text:
        raise ValueError("%%configure needs a JSON object, like: %%configure\n{\"driverMemory\": \"28g\"}")
    try:
        settings = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"%%configure isn't valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg}). "
                         "Fabric would stop the session start on it too.") from exc
    if not isinstance(settings, dict):
        raise ValueError("%%configure takes a JSON object of settings.")
    lines = ["%%configure checked: the JSON is valid. These settings take effect when the notebook runs in Fabric:"]
    unknown = []
    for key, value in settings.items():
        kind = _CONFIGURE_KEYS.get(key)
        if kind is None:
            unknown.append(key)
        elif kind == "conf" and isinstance(value, dict):
            lines.append(f"- conf: {', '.join(sorted(value)) or 'none'} (Spark settings for Fabric's session; "
                         "this PC's spark is DuckDB and doesn't read them)")
        elif kind == "lakehouse" and isinstance(value, dict):
            from . import lakehouse
            wanted = value.get("name") or value.get("id") or "?"
            here = lakehouse.name()
            same = "the same one is attached here" if here and here == value.get("name") else \
                f"this PC uses {'the attached lakehouse, ' + here if here else 'no attached lakehouse'}; attach {wanted} with Add data items to match"
            lines.append(f"- defaultLakehouse: {wanted} ({same})")
        elif kind in ("memory", "cores"):
            lines.append(f"- {key}: {value} (Fabric's Spark nodes; on this PC the limits in Settings apply)")
        else:
            lines.append(f"- {key}: {json.dumps(value)[:80]}")
    if unknown:
        lines.append(f"Fabric doesn't know {', '.join(unknown)}; check the spelling (names are case-sensitive).")
    return "\n".join(lines)


def _duration(seconds: float) -> str:
    """A time the way IPython prints it: 3 significant figures in ns, µs, ms or s."""
    for unit, scale in (("s", 1.0), ("ms", 1e-3), ("µs", 1e-6), ("ns", 1e-9)):
        if seconds >= scale or unit == "ns":
            return f"{seconds / scale:.3g} {unit}"
    return f"{seconds:.3g} s"


class CapturedOutput:
    """What a ``%%capture`` cell printed and showed. ``show()`` (or calling it) shows it again."""

    def __init__(self, outputs: list[dict[str, Any]], kernel: Kernel):
        self.outputs = [o for o in outputs if o["type"] != "error"]
        self._kernel = kernel

    @property
    def stdout(self) -> str:
        return "".join(o["text"] for o in self.outputs if o["type"] == "stream")

    stderr = ""

    def show(self) -> None:
        run = getattr(self._kernel._local, "run", None)
        for output in self.outputs:
            if output["type"] == "stream":
                print(output["text"], end="")
            elif run is not None:
                run.add(output)

    __call__ = show

    def __repr__(self) -> str:
        return f"<CapturedOutput: {len(self.outputs)} output{'s' * (len(self.outputs) != 1)}>"


def _fs_magic(fs: Any, line: str) -> Any:
    """Databricks' ``%fs`` on notebookutils.fs: ``%fs ls Files/raw``, ``head``, ``cp [-r]``,
    ``mv``, ``rm [-r]``, ``mkdirs``, ``put [-f] path "text"``. Same paths and safety rules as fs."""
    words = magics.words(line)
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


VAR_ITEMS = 100  # items shown when a variable is opened in the Variables pane


# Only the built-in containers are opened or measured item by item: a subclass's own __iter__ or
# __len__ is the user's code.
_CONTAINERS = (dict, list, tuple, set, frozenset)
_SCALARS = (str, bytes, bytearray, int, float, bool, complex, type(None))


def _expandable(value: Any) -> bool:
    if type(value) in _CONTAINERS:
        return len(value) > 0
    return _columns(value) is not None


def _columns(value: Any) -> list[dict[str, str]] | None:
    """A data frame's columns and types, from its schema; None for anything else. Nothing is
    computed: Spark (DuckDB) frames give their types without running the query."""
    module = type(value).__module__
    if type(value).__name__ == "LazyFrame":  # its schema would be resolved from the query plan
        return None
    try:
        if module.startswith("polars") and hasattr(value, "schema"):
            return [{"name": str(k), "type": str(t)} for k, t in value.schema.items()]
        if isinstance(value, (pa.Table, pa.RecordBatch)):
            return [{"name": f.name, "type": str(f.type)} for f in value.schema]
        if module.startswith("duckdb.experimental.spark") and hasattr(value, "dtypes"):
            return [{"name": str(k), "type": str(t)} for k, t in value.dtypes]
        if module.startswith("pandas") and hasattr(value, "dtypes") and hasattr(value, "columns"):
            return [{"name": str(k), "type": str(t)} for k, t in value.dtypes.items()]
    except Exception:  # noqa: BLE001  # silent-ok: a frame whose schema can't be read just isn't expandable
        return None
    return None


def _memory(value: Any) -> int | None:
    """Bytes a variable holds, estimated: a frame's or array's buffers, or a container and its
    first 10,000 items scaled up. None when it can't be told cheaply (a Spark frame, an object)."""
    module = type(value).__module__
    try:
        if module.startswith("polars") and hasattr(value, "estimated_size"):
            return int(value.estimated_size())
        if isinstance(value, (pa.Table, pa.RecordBatch, pa.Array, pa.ChunkedArray)):
            return int(value.nbytes)
        if module.startswith("numpy") and hasattr(value, "nbytes"):
            return int(value.nbytes)
        if module.startswith("pandas") and hasattr(value, "memory_usage"):
            usage = value.memory_usage(deep=False)
            return int(usage.sum() if hasattr(usage, "sum") else usage)
        if type(value) in _SCALARS:
            return sys.getsizeof(value)
        if type(value) in _CONTAINERS:
            items = value.items() if type(value) is dict else value
            sample = 0
            n = 0
            for item in itertools.islice(items, 10_000):
                parts = item if type(value) is dict else (item,)
                # A user object's own __sizeof__ isn't called: its base size counts instead.
                sample += sum(sys.getsizeof(p) if type(p) in _SCALARS else type(p).__basicsize__ for p in parts)
                n += 1
            return sys.getsizeof(value) + (sample * len(value) // n if n else 0)
    except Exception:  # noqa: BLE001  # silent-ok: an estimate that fails is shown as unknown
        return None
    return None


def _count(n: Any) -> str:
    if not isinstance(n, int):
        return str(n)
    for unit, size in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 10_000)):
        if n >= size:
            return f"{n / size:.1f}{unit}".replace(".0", "") if unit != "K" else f"{n // 1000}K"
    return str(n)


def _preview(value: Any) -> str:
    module = type(value).__module__
    # A LazyFrame's columns come from resolving its plan, which reads its files (or fails when they're
    # gone), so it shows as its repr, as _columns leaves it unexpanded.
    if module.startswith(("polars", "pandas", "pyarrow", "duckdb")) and type(value).__name__ != "LazyFrame":
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


# Folders for notebooks (notebook_folders.py): a name may carry up to MAX_FOLDER_DEPTH folders before
# the notebook's own name, "Sales/Daily load", saved as Sales/Daily load.ipynb in the place's folder.
MAX_FOLDER_DEPTH = 3
FOLDER_SEP = "/"


def _check_part(part: str, whole: str) -> str:
    part = part.strip()
    if not part or _SAFE_NAME.search(part) or part.endswith("."):
        raise ValueError(f"Invalid notebook name {whole!r}: avoid < > : \" \\ | ? * and a trailing dot"
                         " (a / only separates folders).")
    if part.split(".")[0].strip().upper() in _WINDOWS_DEVICES:  # CON, con.txt, COM1...: Windows can't save these
        raise ValueError(f"{part!r} is a name Windows keeps for devices, so it can't be a notebook or folder name. "
                         "Pick another name.")
    return part


_WINDOWS_DEVICES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))})


def check_folder_part(part: str, whole: str | None = None) -> str:
    """One folder's name: the notebook name rule, and not hidden (.git) or a resources folder."""
    part = _check_part(part, whole or part)
    if part.startswith("."):
        raise ValueError(f"A folder name can't start with a dot: {part!r}.")
    if part.lower().endswith(notebook_resources.SUFFIX):
        raise ValueError(f"A folder name can't end with {notebook_resources.SUFFIX}: that's where a notebook keeps its resources.")
    return part


def check_notebook_name(name: str) -> str:
    """The notebook's name as saved: its folders (each checked, at most MAX_FOLDER_DEPTH) and its own
    name, joined by "/". Raises ValueError saying what's wrong."""
    whole = str(name).strip()
    parts = whole.split(FOLDER_SEP)
    if len(parts) - 1 > MAX_FOLDER_DEPTH:
        raise ValueError(f"Folders go {MAX_FOLDER_DEPTH} levels deep at most: {whole!r} is in {len(parts) - 1}.")
    folders = [check_folder_part(p, whole) for p in parts[:-1]]
    return FOLDER_SEP.join([*folders, _check_part(parts[-1], whole)])


def folder_of(name: str) -> str:
    """The folders part of a notebook name, "" for one not in a folder."""
    return name.rpartition(FOLDER_SEP)[0]


def leaf_name(name: str) -> str:
    """The notebook's own name, without its folders: what its Fabric item is called."""
    return name.rpartition(FOLDER_SEP)[2]


def is_notebook_folder(dirname: str) -> bool:
    """Whether a directory in a notebooks folder is a folder of notebooks (not .git or X.resources)."""
    try:
        check_folder_part(dirname)
    except ValueError:
        return False
    return True


def notebook_files(folder: Path) -> list[tuple[str, Path]]:
    """Every notebook in ``folder`` and its subfolders (to MAX_FOLDER_DEPTH), as (name, file). Hidden
    folders (.git, .ipynb_checkpoints) and resources folders are skipped."""
    out: list[tuple[str, Path]] = []

    def walk(d: Path, prefix: str, depth: int) -> None:
        try:
            entries = sorted(d.iterdir())
        except OSError as exc:  # a folder that went away or can't be read lists nothing
            _log.debug("Couldn't list %s: %s", d, exc)
            return
        for p in entries:
            if p.suffix == ".ipynb" and p.is_file():
                out.append((prefix + p.stem, p))
            elif depth < MAX_FOLDER_DEPTH and is_notebook_folder(p.name) and p.is_dir():
                walk(p, f"{prefix}{p.name}{FOLDER_SEP}", depth + 1)

    if folder.is_dir():
        walk(folder, "", 0)
    return out


COMPUTE = ("default", "local", "fabric")


def _cell_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """A cell's settings as Jupyter and Fabric store them: the parameters tag, hidden input or
    output, and frozen (can't be edited or run); a collapsed heading section as JupyterLab stores it,
    and a cell title where Databricks keeps it, so both read them back."""
    md: dict[str, Any] = {}
    tags = [t for t in meta.get("tags", []) if isinstance(t, str)]
    if tags:
        md["tags"] = tags
    hidden = {k: True for k in ("source_hidden", "outputs_hidden") if meta.get(k)}
    if hidden:
        md["jupyter"] = hidden
    if meta.get("frozen"):
        md.update(editable=False, deletable=False, run_control={"frozen": True})
    if meta.get("heading_collapsed"):
        md[_HEADING_COLLAPSED] = True
    title = meta.get("title")
    if isinstance(title, str) and title.strip():
        md[_DATABRICKS_CELL] = {"title": title.strip()[:200]}
    chart = _chart_meta(meta.get("chart"))
    charts = _charts_meta(meta.get("charts"))
    if chart or charts:
        md["fabric_desktop"] = {"chart": chart} if chart else {}
        if charts:
            md["fabric_desktop"]["charts"] = charts
    if meta.get("fabric_step"):  # the last cell runs on Fabric Spark (fabric_step.py)
        md.setdefault("fabric_desktop", {})["run_on"] = "fabric"
    return md


_HEADING_COLLAPSED = "jp-MarkdownHeadingCollapsed"
_DATABRICKS_CELL = "application/vnd.databricks.v1+cell"


_CHART_KEYS = ("kind", "x", "y", "agg", "series", "title")


def _chart_meta(chart: Any) -> dict[str, Any] | None:
    """The chart a cell's table output shows (charts.js), by column name: plain short strings only."""
    if not isinstance(chart, dict):
        return None
    out = {k: chart[k][:200] for k in _CHART_KEYS if isinstance(chart.get(k), str)}
    return out if out.get("kind") else None


MAX_EXTRA_CHARTS = 9  # charts.js MAX_CHARTS less the first, which stays in "chart" for older builds


def _charts_meta(charts: Any) -> list[dict[str, Any]]:
    """The charts after the first, in order. One that isn't a chart is dropped with the rest kept."""
    if not isinstance(charts, list):
        return []
    return [c for c in map(_chart_meta, charts) if c][:MAX_EXTRA_CHARTS]


def _cell_meta(md: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if md.get("tags"):
        meta["tags"] = [t for t in md["tags"] if isinstance(t, str)]
    for k in ("source_hidden", "outputs_hidden"):
        if (md.get("jupyter") or {}).get(k):
            meta[k] = True
    if (md.get("run_control") or {}).get("frozen"):
        meta["frozen"] = True
    if md.get(_HEADING_COLLAPSED) is True:
        meta["heading_collapsed"] = True
    title = (md.get(_DATABRICKS_CELL) or {}).get("title") if isinstance(md.get(_DATABRICKS_CELL), dict) else None
    if isinstance(title, str) and title.strip():
        meta["title"] = title.strip()
    own_md = md.get("fabric_desktop")
    own = own_md if isinstance(own_md, dict) else {}
    chart = _chart_meta(own.get("chart"))
    if chart:
        meta["chart"] = chart
    charts = _charts_meta(own.get("charts"))
    if charts:
        meta["charts"] = charts
    if own.get("run_on") == "fabric":
        meta["fabric_step"] = True
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
    keep = {k: v for k, v in before.items() if k not in ("tags", "editable", "deletable", _HEADING_COLLAPSED)}
    if isinstance(keep.get(_DATABRICKS_CELL), dict):  # the title is ours; Databricks' other keys aren't
        keep[_DATABRICKS_CELL] = {k: v for k, v in keep[_DATABRICKS_CELL].items() if k != "title"}
    if isinstance(keep.get("jupyter"), dict):
        rest = {k: v for k, v in keep["jupyter"].items() if k not in ("source_hidden", "outputs_hidden")}
        keep["jupyter"] = rest
    if isinstance(keep.get("run_control"), dict):
        keep["run_control"] = {k: v for k, v in keep["run_control"].items() if k != "frozen"}
    if isinstance(keep.get("fabric_desktop"), dict):
        keep["fabric_desktop"] = {k: v for k, v in keep["fabric_desktop"].items() if k not in ("chart", "charts", "run_on")}
    for k in ("jupyter", "run_control", _DATABRICKS_CELL, "fabric_desktop"):
        if k in keep and not keep[k]:
            del keep[k]
    for k, v in ours.items():
        keep[k] = keep[k] | v if isinstance(v, dict) and isinstance(keep.get(k), dict) else v
    return keep


def merge_saved(new: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    """``new`` (from to_ipynb) with the metadata Fabric Desktop doesn't own carried over from the
    notebook as it was on disk: notebook-level keys, other tools' ``fabric_desktop`` keys and
    ``dependencies`` such as a Fabric environment, and each cell's other metadata, by cell id."""
    saved_meta = before.get("metadata")
    old_meta = saved_meta if isinstance(saved_meta, dict) else {}
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


_WRITE_LOCKS: dict[str, threading.Lock] = {}
_WRITE_LOCKS_GUARD = threading.Lock()


def _path_write_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve()))
    with _WRITE_LOCKS_GUARD:
        return _WRITE_LOCKS.setdefault(key, threading.Lock())


def _write_atomic(path: Path, text: str) -> None:
    """Write through a temporary file of this write's own, so two writers never share one.

    Saves of one notebook take turns: Windows refuses a second os.replace onto a file
    another replace is still finishing, and briefly while a reader (or a virus scanner)
    has it open, so a refused replace is tried a few more times before giving up."""
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        with _path_write_lock(path):
            for attempt in range(6):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(0.05 * (attempt + 1))
    finally:
        tmp.unlink(missing_ok=True)


def disk_stamp(nb: dict[str, Any]) -> str:
    """A short hash of a saved notebook's cells (type and source), so a save can tell whether the
    file was changed since the app read it, by VS Code or any other program. Metadata is left out:
    the app's own metadata writes (comments, inputs, proofs) don't count as a change."""
    cells = [[c["type"], c["source"]] for c in from_ipynb(nb)["cells"]]
    return hashlib.sha256(json.dumps(cells, ensure_ascii=False).encode("utf-8")).hexdigest()[:32]


def raw_stamp(data: bytes) -> str:
    """The stamp of a file that isn't a notebook the app can read (a Git merge left conflict
    markers in it, say): its bytes, so it never matches a notebook's ``disk_stamp``."""
    return "raw-" + hashlib.sha256(data).hexdigest()[:28]


class NotebookStore:
    # Held while a save or a metadata write reads, changes and replaces a notebook file, so one
    # doesn't write over the other's change (the local API and MCP tools call in from other threads).
    _write_lock = threading.RLock()

    def __init__(self, folder: Path):
        self.folder = folder

    def path(self, name: str) -> Path:
        return self.folder / f"{check_notebook_name(name)}.ipynb"

    def list(self) -> builtins.list[dict[str, Any]]:
        files = sorted(notebook_files(self.folder), key=lambda f: f[1].stat().st_mtime, reverse=True)
        return [{"name": name, "modified": p.stat().st_mtime} for name, p in files]

    def load(self, name: str) -> dict[str, Any]:
        nb = json.loads(self.path(name).read_text(encoding="utf-8"))
        return {"name": name, **from_ipynb(nb), "disk_stamp": disk_stamp(nb)}

    def save(self, name: str, cells: builtins.list[dict[str, Any]], lakehouse: dict[str, Any] | None = None,
             compute: str | None = None, workspace: dict[str, Any] | None = None,
             expected_stamp: str | None = None) -> dict[str, Any]:
        """Save the notebook. ``compute`` is where Run all runs it: "local" (this PC), "fabric",
        or "default" (whatever the environment picked in the top bar runs on). ``workspace`` is
        where it is published ({"workspace_id", "workspace_name"}; {} forgets it). None keeps the saved choice.

        Cells keep their ``cell_id``; a cell without one gets one, returned in ``cell_ids`` so the
        app can keep it. Metadata other tools wrote into the file is kept.

        ``expected_stamp`` is the ``disk_stamp`` the caller last read or saved. When the file's cells
        no longer match it (another program saved the notebook since), nothing is written and the
        result has ``changed_on_disk`` and the file's stamp now (and ``unreadable`` when the file
        isn't a notebook any more, e.g. Git conflict markers). None saves without checking."""
        with self._write_lock:
            return self._save(name, cells, lakehouse, compute, workspace, expected_stamp)

    def _save(self, name: str, cells: builtins.list[dict[str, Any]], lakehouse: dict[str, Any] | None,
              compute: str | None, workspace: dict[str, Any] | None,
              expected_stamp: str | None = None) -> dict[str, Any]:
        path = self.path(name)
        before: dict[str, Any] = {}
        raw: bytes | None = None
        parsed = False
        if path.exists():
            try:
                raw = path.read_bytes()
                before = json.loads(raw.decode("utf-8"))
                parsed = isinstance(before, dict)
            except (OSError, ValueError):
                pass
            if not parsed:
                before = {}
        if expected_stamp is not None and raw is not None:
            now = disk_stamp(before) if parsed else raw_stamp(raw)
            if now != expected_stamp:
                return {"name": name, "changed_on_disk": True, "disk_stamp": now, "unreadable": not parsed}
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
        _write_atomic(path, json.dumps(nb, indent=1))
        return {"name": name, "cell_ids": ids, "disk_stamp": disk_stamp(nb)}

    def extra(self, name: str, key: str) -> Any:
        """A value Fabric Desktop keeps in the notebook's metadata under ``fabric_desktop.<key>``
        that the notebook screen doesn't send (the proof contract). None when it isn't set."""
        nb = json.loads(self.path(name).read_text(encoding="utf-8"))
        return ((nb.get("metadata") or {}).get("fabric_desktop") or {}).get(key)

    def set_extra(self, name: str, key: str, value: Any) -> None:
        """Set (or, with None, remove) ``fabric_desktop.<key>`` in the saved notebook. Saves from the
        notebook screen keep it (merge_saved)."""
        self.update_extra(name, key, lambda _old, _nb: value)

    def update_extra(self, name: str, key: str, change: Callable[[Any, dict[str, Any]], Any]) -> Any:
        """Read ``fabric_desktop.<key>`` from the file as it is now, set it to ``change(old, nb)``
        (None removes it) and replace the file in one step, under the write lock. ``nb`` is the
        whole notebook as read, for checks. Returns the new value."""
        if key in ("compute", "workspace", "lakehouse"):
            raise ValueError(f"{key} is saved with the notebook, not set on its own.")
        with self._write_lock:
            path = self.path(name)
            nb = json.loads(path.read_text(encoding="utf-8"))
            fd = nb.setdefault("metadata", {}).setdefault("fabric_desktop", {})
            value = change(fd.get(key), nb)
            if value is None:
                fd.pop(key, None)
            else:
                fd[key] = value
            _write_atomic(path, json.dumps(nb, indent=1))
            return value

    def new(self, prefix: str = "Notebook") -> dict[str, Any]:
        taken = {n["name"] for n in self.list()}
        i = 1
        while f"{prefix} {i}" in taken:
            i += 1
        name = f"{prefix} {i}"
        self.save(name, [{"type": "code", "source": ""}])
        return self.load(name)

    def resources(self, name: str) -> Path:
        """The notebook's resources folder, ``<name>.resources`` beside it (notebook_resources)."""
        return notebook_resources.folder_for(self.path(name))

    def rename(self, name: str, new_name: str) -> dict[str, Any]:
        target = self.path(new_name)
        if target.exists():
            raise ValueError(f"A notebook named {new_name!r} already exists.")
        return _rename_with_resources(self.path(name), target, new_name)

    def delete(self, name: str) -> dict[str, Any]:
        """Deletes the notebook's file. Its resources folder is never deleted here: it stays where it
        is (named in ``resources_kept``), and a new notebook of the same name picks it up again."""
        path = self.path(name)
        folder = notebook_resources.folder_for(path)
        path.unlink(missing_ok=True)
        return {"resources_kept": str(folder) if folder.is_dir() else None}


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
            for name, p in notebook_files(folder):  # its folders too (notebook_folders)
                if name in names:
                    continue
                where = {"root": place["root"], "label": place["label"]} if place else None
                out.append({"name": name, "modified": p.stat().st_mtime, "place": where})
                names.add(name)
        return sorted(out, key=lambda n: n["modified"], reverse=True)

    def _taken(self, name: str) -> bool:
        return any((f / f"{check_notebook_name(name)}.ipynb").exists() for _, f in self._folders())

    def new(self, prefix: str = "Notebook", place: str | None = None, folder_name: str = "") -> dict[str, Any]:
        """A new notebook in ``place``, inside ``folder_name`` ("Sales", "" for none) when given;
        a folder that isn't there yet is made."""
        _, folder = self._place(place)
        if folder_name:
            prefix = check_notebook_name(f"{folder_name}{FOLDER_SEP}{prefix}")
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
        if not src.is_file():
            raise ValueError(f"There's no notebook named {name!r}.")
        folder = self._where(name)[1]  # stays in its place; a folder in the new name is made there
        return _rename_with_resources(src, folder / f"{check_notebook_name(new_name)}.ipynb", new_name)

    def move(self, name: str, place: str | None) -> dict[str, Any]:
        """Moves the notebook's file, and its resources folder, to another place (a repository root,
        or None for the app's own folder). The copies are written and checked before the originals
        are removed, and a notebook or folder already there is never replaced."""
        src = self.path(name)
        if not src.is_file():
            raise ValueError(f"There's no notebook named {name!r}.")
        target_place, folder = self._place(place)
        dest = folder / f"{check_notebook_name(name)}.ipynb"  # in the same folders there
        if _resolved(dest) == _resolved(src):
            return {"name": name, "place": self.place_of(name), "moved": False}
        data = src.read_bytes()
        dest.parent.mkdir(parents=True, exist_ok=True)
        where = target_place["label"] if target_place else "the app's notebooks folder"
        res_src, res_dest = notebook_resources.folder_for(src), notebook_resources.folder_for(dest)
        if res_src.is_dir() and res_dest.exists():
            raise ValueError(f"A folder named {res_dest.name!r} is already in {where}. Nothing was moved.")
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
        try:
            notebook_resources.copy_folder(res_src, res_dest)
        except (OSError, ValueError) as e:
            dest.unlink(missing_ok=True)
            raise OSError(f"Couldn't copy {name}'s resources to {where} ({e}). The notebook is still where it was.") from None
        src.unlink()
        result: dict[str, Any] = {"name": name, "place": self.place_of(name), "moved": True}
        try:
            notebook_resources.remove_folder(res_src)
        except OSError as e:  # a file held open: the moved copy is whole, the old folder stays
            result["note"] = f"The resources were copied, but the old folder {res_src} couldn't be removed ({e}). Delete it by hand."
        return result


def _rename_with_resources(src: Path, dest: Path, new_name: str) -> dict[str, Any]:
    """Renames the notebook's file and its resources folder together; a failed file rename puts the
    folder back."""
    res_src, res_dest = notebook_resources.folder_for(src), notebook_resources.folder_for(dest)
    if dest.exists():  # never replace a notebook (one made since the name was checked)
        raise ValueError(f"A notebook named {new_name!r} already exists. Nothing was renamed.")
    made = not dest.parent.is_dir()
    dest.parent.mkdir(parents=True, exist_ok=True)  # moving into a folder that isn't there yet
    try:
        moved = notebook_resources.rename_folder(res_src, res_dest)
        try:
            src.rename(dest)
        except OSError:
            if moved:
                res_dest.rename(res_src)
            raise
    except (OSError, ValueError):
        if made:
            dest.parent.rmdir()  # the folder made for it, still empty
        raise
    return {"name": new_name}


def _resolved(p: Path) -> str:
    try:
        return os.path.normcase(str(p.resolve()))
    except OSError:
        return os.path.normcase(str(p))
