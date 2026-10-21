"""Charts from bokeh and altair in a cell's output.

bokeh's ``show()`` writes an HTML file next to the running script and opens the system browser,
which from the engine means a file in Python's folder and a browser tab on cdn.bokeh.org. In a
cell it draws in the output instead, with bokeh's JavaScript inline as plotly's is, so nothing is
fetched. bokeh is patched as it is imported, so the engine doesn't load it until a cell does.

altair needs JavaScript from a CDN (or vl-convert, which the engine doesn't ship) to draw, so an
altair chart says how to see it instead of showing its repr.
"""

from __future__ import annotations

import importlib.abc
import sys
from typing import Any, Callable

SHOWING = "bokeh.io.showing"
ALTAIR_NOTE = ("Altair charts don't draw in a cell on this PC yet. chart.save('chart.html') saves it to open "
               "in a browser; plotly, bokeh, seaborn and matplotlib charts draw here.")

THREAD_NOTE = ("bokeh's show() draws in the cell only from the cell's own code, not from a thread it started. "
               "Call show() in the cell, or bokeh.io.save(p, 'chart.html') to save it to open in a browser.")

_emit: Callable[[dict[str, Any]], bool] = lambda _output: False  # set by install()
_cell_running: Callable[[], bool] = lambda: False  # set by install()


def bokeh_html(obj: Any) -> dict[str, Any]:
    from bokeh.embed import file_html
    from bokeh.resources import INLINE
    return {"type": "html", "html": file_html(obj, INLINE, title="Chart")}


def is_bokeh(value: Any) -> bool:
    if not type(value).__module__.startswith("bokeh"):
        return False
    from bokeh.model import Model
    return isinstance(value, Model) and hasattr(value, "document")


def is_altair(value: Any) -> bool:
    return type(value).__module__.startswith("altair") and hasattr(value, "to_dict") and hasattr(value, "save")


def _patch(showing: Any) -> None:
    original = showing.show
    if getattr(original, "_fabric_desktop", False):
        return

    def show(obj: Any, *args: Any, **kwargs: Any) -> Any:
        if _emit(bokeh_html(obj)):  # inside a cell: draw it there
            return None
        if _cell_running():  # a thread a cell started: no file next to Python, no browser tab
            raise RuntimeError(THREAD_NOTE)
        return original(obj, *args, **kwargs)

    show._fabric_desktop = True  # type: ignore[attr-defined]
    show.__doc__ = original.__doc__
    for name in (SHOWING, "bokeh.io", "bokeh.plotting"):
        module = sys.modules.get(name)
        if module is not None and getattr(module, "show", None) is original:
            module.show = show


class _PatchOnImport(importlib.abc.MetaPathFinder):
    """Finds bokeh.io.showing with the other finders, and patches it once it has run."""

    def find_spec(self, name: str, path: Any, target: Any = None) -> Any:
        if name != SHOWING:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(name, path, target)
            if spec is not None and spec.loader is not None and hasattr(spec.loader, "exec_module"):
                spec.loader = _PatchAfter(spec.loader)
                return spec
        return None


class _PatchAfter(importlib.abc.Loader):
    def __init__(self, loader: Any):
        self.loader = loader

    def create_module(self, spec: Any) -> Any:
        return self.loader.create_module(spec)

    def exec_module(self, module: Any) -> None:
        self.loader.exec_module(module)
        _patch(module)


def install(emit: Callable[[dict[str, Any]], bool], cell_running: Callable[[], bool]) -> None:
    """``emit`` adds an output to the cell running on this thread, and says False when none is;
    ``cell_running`` says whether any cell is running, on any thread."""
    global _emit, _cell_running
    _emit, _cell_running = emit, cell_running
    if SHOWING in sys.modules:
        _patch(sys.modules[SHOWING])
    elif not any(isinstance(f, _PatchOnImport) for f in sys.meta_path):
        sys.meta_path.insert(0, _PatchOnImport())
