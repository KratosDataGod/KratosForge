"""Charts from bokeh, altair and plotly in a cell's output.

bokeh's ``show()`` writes an HTML file next to the running script and opens the system browser,
which from the engine means a file in Python's folder and a browser tab on cdn.bokeh.org. In a
cell it draws in the output instead, with bokeh's JavaScript inline as plotly's is, so nothing is
fetched. Each library is patched as it is imported, so the engine doesn't load it until a cell does.

An altair chart becomes a ``vegalite`` output: its Vega-Lite spec, which the app draws with the
Vega libraries it ships (app/ui/vendor/vega) in the same sandboxed frame as HTML output, so
nothing is fetched. ``chart.show()`` draws in the cell too, instead of starting a server for a
browser. plotly's ``fig.show()`` (which drew nothing, as no renderer suits the engine) draws in
the cell, as the figure does when it's a cell's last line.
"""

from __future__ import annotations

import importlib.abc
import json
import os
import sys
from typing import Any, Callable

SHOWING = "bokeh.io.showing"
ALTAIR = "altair"
PLOTLY_RENDERERS = "plotly.io._renderers"
MAX_SPEC = 20_000_000  # characters of Vega-Lite JSON; Altair's own limit (5,000 rows) usually comes first
ALTAIR_TOO_BIG = ("This Altair chart's data is too large to draw in a cell ({size:,} characters). Aggregate it first, "
                  "or chart.save('chart.html') saves it to open in a browser.")

THREAD_NOTE = ("bokeh's show() draws in the cell only from the cell's own code, not from a thread it started. "
               "Call show() in the cell, or bokeh.io.save(p, 'chart.html') to save it to open in a browser.")
SHOW_THREAD_NOTE = ("{what} draws in the cell only from the cell's own code, not from a thread it started. "
                    "Call it in the cell, or save the chart to an HTML file to open in a browser.")

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


def altair_output(chart: Any) -> dict[str, Any]:
    """The chart's Vega-Lite spec, for the app to draw; altair's own errors (MaxRowsError) say what to do."""
    spec = chart.to_dict()
    size = len(json.dumps(spec))
    if size > MAX_SPEC:
        return {"type": "text", "text": ALTAIR_TOO_BIG.format(size=size)}
    return {"type": "vegalite", "spec": spec}


def _draw(output: Any, what: str) -> bool:
    """Draws ``output`` (a callable making it) in the running cell; False outside any cell."""
    if _emit(output()):
        return True
    if _cell_running():
        raise RuntimeError(SHOW_THREAD_NOTE.format(what=what))
    return False


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


def _patch_altair(altair: Any) -> None:
    mixin = getattr(altair, "TopLevelMixin", None)
    original = getattr(mixin, "show", None)
    if original is None or getattr(original, "_fabric_desktop", False):
        return

    def show(self: Any, *args: Any, **kwargs: Any) -> Any:
        if _draw(lambda: altair_output(self), "An Altair chart's show()"):
            return None
        return original(self, *args, **kwargs)

    show._fabric_desktop = True  # type: ignore[attr-defined]
    show.__doc__ = original.__doc__
    mixin.show = show


def _patch_plotly(module: Any) -> None:
    """Adds a renderer that draws in the cell and makes it the default. plotly picks "browser" where it
    finds one (always on Windows), which opens a browser tab from the engine; only PLOTLY_RENDERER, set
    on purpose, is kept. A renderer the notebook sets afterwards is used as usual."""
    if "kratosforge" in module.renderers:
        return
    from plotly.io.base_renderers import ExternalRenderer

    class CellRenderer(ExternalRenderer):
        def render(self, fig_dict: Any) -> None:
            import plotly.io as pio
            _draw(lambda: {"type": "html", "html": pio.to_html(fig_dict, include_plotlyjs=True, full_html=True,
                                                                validate=False)}, "plotly's fig.show()")

    module.renderers["kratosforge"] = CellRenderer()
    if not os.environ.get("PLOTLY_RENDERER"):
        module.renderers.default = "kratosforge"


_PATCHES: dict[str, Callable[[Any], None]] = {SHOWING: _patch, ALTAIR: _patch_altair, PLOTLY_RENDERERS: _patch_plotly}


class _PatchOnImport(importlib.abc.MetaPathFinder):
    """Finds the modules in ``_PATCHES`` with the other finders, and patches each once it has run."""

    def find_spec(self, name: str, path: Any, target: Any = None) -> Any:
        if name not in _PATCHES:
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
        _PATCHES[module.__name__](module)


def install(emit: Callable[[dict[str, Any]], bool], cell_running: Callable[[], bool]) -> None:
    """``emit`` adds an output to the cell running on this thread, and says False when none is;
    ``cell_running`` says whether any cell is running, on any thread."""
    global _emit, _cell_running
    _emit, _cell_running = emit, cell_running
    for name, patch in _PATCHES.items():
        if name in sys.modules:
            patch(sys.modules[name])
    if not any(isinstance(f, _PatchOnImport) for f in sys.meta_path):
        sys.meta_path.insert(0, _PatchOnImport())
