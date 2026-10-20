"""``IPython.display`` for notebook cells when IPython isn't installed.

Fabric and Databricks notebooks run under IPython, so notebook code often does
``from IPython.display import display, HTML, Markdown``. The engine doesn't ship IPython; this
module stands in for the part of ``IPython.display`` that notebooks use, and ``Kernel`` installs it
as ``IPython.display`` only when the real one can't be imported. Each object has the ``_repr_*_``
view the kernel's ``display()`` already renders, so HTML still goes only to the app's sandboxed
frame and Markdown through the app's escaping renderer.
"""

from __future__ import annotations

import base64
import json as _json
import sys
import types
from pathlib import Path
from typing import Any, Callable


class DisplayObject:
    def __init__(self, data: Any = None, url: str | None = None, filename: str | None = None, **_kw: Any):
        if filename is not None:
            mode = "rb" if self._binary else "r"
            with open(filename, mode, **({} if self._binary else {"encoding": "utf-8"})) as f:
                data = f.read()
        self.data = data
        self.url = url
        self.filename = filename

    _binary = False

    def __repr__(self) -> str:
        return f"<{type(self).__module__}.{type(self).__name__} object>"


class TextDisplayObject(DisplayObject):
    pass


class HTML(TextDisplayObject):
    def _repr_html_(self) -> str:
        return str(self.data or "")


class Markdown(TextDisplayObject):
    def _repr_markdown_(self) -> str:
        return str(self.data or "")


class Latex(TextDisplayObject):
    def _repr_latex_(self) -> str:
        return str(self.data or "")


Math = Latex


class Pretty(TextDisplayObject):
    def __repr__(self) -> str:
        return str(self.data or "")


class Code(TextDisplayObject):
    def __init__(self, data: Any = None, url: str | None = None, filename: str | None = None, language: str | None = None):
        super().__init__(data, url, filename)
        self.language = language

    def __repr__(self) -> str:
        return str(self.data or "")


class SVG(DisplayObject):
    def _repr_svg_(self) -> str:
        data = self.data
        return data.decode("utf-8") if isinstance(data, bytes) else str(data or "")


class JSON(DisplayObject):
    def __init__(self, data: Any = None, url: str | None = None, filename: str | None = None, expanded: bool = False, **kw: Any):
        super().__init__(data, url, filename, **kw)
        if isinstance(self.data, str) and filename is not None:
            self.data = _json.loads(self.data)
        self.expanded = expanded

    def _repr_json_(self) -> Any:
        return self.data


_IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


class Image(DisplayObject):
    """An image from bytes or a file on this PC. A URL isn't fetched: the cell shows the URL."""
    _binary = True

    def __init__(self, data: Any = None, url: str | None = None, filename: str | None = None, format: str | None = None,  # noqa: A002 - IPython's name
                 embed: bool | None = None, width: Any = None, height: Any = None, **kw: Any):
        if isinstance(data, str) and filename is None and url is None:
            if data.startswith(("http://", "https://")):
                url, data = data, None
            else:
                filename, data = data, None
        super().__init__(data, url, filename, **kw)
        ext = (format or Path(filename or "").suffix.lstrip(".") or _sniff(self.data or b"")).lower()
        self.format = ext
        self.width, self.height = width, height

    def _repr_mimebundle_(self, include: Any = None, exclude: Any = None) -> dict[str, Any]:
        if self.data is None:
            return {"text/plain": f"Image: {self.url}"}
        mime = _IMAGE_TYPES.get(self.format, "image/png")
        return {mime: base64.b64encode(self.data).decode("ascii")}


def _sniff(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return "png"


class IFrame:
    """Shown as its address: the cell's sandboxed frame doesn't load other pages."""

    def __init__(self, src: str, width: Any = None, height: Any = None, **_kw: Any):
        self.src = src

    def __repr__(self) -> str:
        return f"IFrame: {self.src}"


class Javascript(TextDisplayObject):
    """Notebook JavaScript doesn't run against the app; shown as its source."""

    def __repr__(self) -> str:
        return str(self.data or "")


def build(display: Callable[..., None], clear_output: Callable[..., None]) -> types.ModuleType:
    """The ``IPython`` package and its ``IPython.display`` module, bound to one kernel."""
    mod = types.ModuleType("IPython.display", __doc__)
    for name, value in (("DisplayObject", DisplayObject), ("TextDisplayObject", TextDisplayObject), ("HTML", HTML),
                        ("Markdown", Markdown), ("Latex", Latex), ("Math", Math), ("Pretty", Pretty), ("Code", Code),
                        ("SVG", SVG), ("JSON", JSON), ("Image", Image), ("IFrame", IFrame), ("Javascript", Javascript)):
        setattr(mod, name, value)
    mod.display = display
    mod.clear_output = clear_output

    def display_kind(cls: type) -> Callable[..., None]:
        return lambda *objs, raw=False, **_kw: display(*(cls(o) if not raw or isinstance(o, str) else o for o in objs))
    mod.display_html = display_kind(HTML)
    mod.display_markdown = display_kind(Markdown)
    mod.display_json = display_kind(JSON)
    mod.display_svg = display_kind(SVG)
    mod.display_png = display_kind(Image)
    mod.display_jpeg = display_kind(Image)
    pkg = types.ModuleType("IPython", "IPython isn't installed here; KratosForge provides IPython.display.")
    pkg.display = mod  # type: ignore[attr-defined]
    pkg.__path__ = []  # type: ignore[attr-defined]  # so "import IPython.display" finds the submodule
    pkg.get_ipython = lambda: None  # type: ignore[attr-defined]
    return pkg


def install(pkg: types.ModuleType) -> None:
    """Put the stand-in in place, unless the real IPython is importable."""
    real = sys.modules.get("IPython")
    if real is not None and not getattr(real, "__kratosforge__", False):
        return
    if real is None:
        try:
            import IPython  # noqa: F401 - the real one wins when it's installed
            return
        except ImportError:
            pass
    pkg.__kratosforge__ = True  # type: ignore[attr-defined]
    sys.modules["IPython"] = pkg
    sys.modules["IPython.display"] = pkg.display  # type: ignore[attr-defined]
