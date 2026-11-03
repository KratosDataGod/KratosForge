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
import importlib.abc
import importlib.machinery
import json as _json
import sys
import types
from pathlib import Path
from typing import Any, Callable


class DisplayObject:
    def __init__(self, data: Any = None, url: str | None = None, filename: str | None = None, **_kw: Any):
        if filename is not None:
            path = Path(filename)
            data = path.read_bytes() if self._binary else path.read_text(encoding="utf-8")
        self.data = data
        self.url = url
        self.filename = filename

    _binary = False

    def __repr__(self) -> str:
        if self.data is None and self.url:  # IPython fetches the URL; the engine doesn't, so name it
            return f"{type(self).__name__}: {self.url}"
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


class DisplayHandle:
    """What ``display(..., display_id=True)`` returns, as IPython's: ``update`` redraws that output."""

    def __init__(self, display_id: str | None = None, display: Callable[..., Any] | None = None):
        import uuid
        self.display_id = display_id or uuid.uuid4().hex
        self._display = display

    def __repr__(self) -> str:
        return f"<DisplayHandle display_id={self.display_id}>"

    def display(self, obj: Any, **kwargs: Any) -> None:
        if self._display is not None:
            self._display(obj, display_id=self.display_id, **kwargs)

    def update(self, obj: Any, **kwargs: Any) -> None:
        if self._display is not None:
            self._display(obj, display_id=self.display_id, update=True, **kwargs)


def build(display: Callable[..., Any], clear_output: Callable[..., None]) -> types.ModuleType:
    """The ``IPython`` package and its ``IPython.display`` module, bound to one kernel."""
    mod = types.ModuleType("IPython.display", __doc__)
    for name, value in (("DisplayObject", DisplayObject), ("TextDisplayObject", TextDisplayObject), ("HTML", HTML),
                        ("Markdown", Markdown), ("Latex", Latex), ("Math", Math), ("Pretty", Pretty), ("Code", Code),
                        ("SVG", SVG), ("JSON", JSON), ("Image", Image), ("IFrame", IFrame), ("Javascript", Javascript)):
        setattr(mod, name, value)

    def update_display(obj: Any, *, display_id: Any, **kwargs: Any) -> None:
        display(obj, display_id=display_id, update=True, **kwargs)

    class BoundHandle(DisplayHandle):
        def __init__(self, display_id: str | None = None):
            super().__init__(display_id, display)
    BoundHandle.__name__ = BoundHandle.__qualname__ = "DisplayHandle"

    def display_kind(cls: type) -> Callable[..., None]:
        return lambda *objs, raw=False, **_kw: display(*(cls(o) if not raw or isinstance(o, str) else o for o in objs))
    vars(mod).update(display=display, clear_output=clear_output, update_display=update_display,
                     DisplayHandle=BoundHandle, display_html=display_kind(HTML),
                     display_markdown=display_kind(Markdown), display_json=display_kind(JSON),
                     display_svg=display_kind(SVG), display_png=display_kind(Image), display_jpeg=display_kind(Image))
    pkg = types.ModuleType("IPython", "IPython isn't installed here; KratosForge provides IPython.display.")
    pkg.display = mod  # type: ignore[attr-defined]
    pkg.__path__ = []  # type: ignore[attr-defined]  # so "import IPython.display" finds the submodule
    # Older notebooks import from IPython.core.display: the same module.
    core = types.ModuleType("IPython.core", "IPython.core.display only, as KratosForge provides it.")
    core.display = mod  # type: ignore[attr-defined]
    core.__path__ = []  # type: ignore[attr-defined]
    pkg.core = core  # type: ignore[attr-defined]
    pkg.get_ipython = lambda: None  # type: ignore[attr-defined]  # no interactive shell, so libraries take their plain path
    pkg.version_info = (8, 30, 0, "")  # type: ignore[attr-defined]  # the IPython.display API this stands in for
    pkg.__version__ = "8.30.0"  # type: ignore[attr-defined]
    return pkg


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Last on ``sys.meta_path``: ``import IPython`` finds the stand-in only when the real IPython
    isn't installed, and only when cell code imports it. Nothing is put in ``sys.modules`` before
    that, because libraries change course when they see an ``IPython`` module (matplotlib reads
    ``IPython.version_info`` on every figure)."""

    NAMES = ("IPython", "IPython.display", "IPython.core", "IPython.core.display")

    def __init__(self) -> None:
        self.pkg: types.ModuleType | None = None

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> importlib.machinery.ModuleSpec | None:
        if fullname not in self.NAMES or self.pkg is None:
            return None
        return importlib.machinery.ModuleSpec(fullname, self, is_package=fullname in ("IPython", "IPython.core"))

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
        assert self.pkg is not None
        return {"IPython": self.pkg, "IPython.core": self.pkg.core}.get(spec.name, self.pkg.display)  # type: ignore[attr-defined]

    def exec_module(self, module: types.ModuleType) -> None:
        return None


_finder = _Finder()


def install(pkg: types.ModuleType) -> None:
    """Make ``import IPython.display`` find ``pkg`` from now on, unless the real IPython is installed.
    A stand-in a cell already imported is pointed at this kernel too."""
    pkg.__kratosforge__ = True  # type: ignore[attr-defined]
    _finder.pkg = pkg
    if _finder not in sys.meta_path:
        sys.meta_path.append(_finder)
    if getattr(sys.modules.get("IPython"), "__kratosforge__", False):
        for name, fresh in (("IPython", pkg), ("IPython.display", pkg.display), ("IPython.core", pkg.core),  # type: ignore[attr-defined]
                            ("IPython.core.display", pkg.display)):  # type: ignore[attr-defined]
            mod = sys.modules.get(name)
            if mod is not None:
                mod.__dict__.update({k: v for k, v in fresh.__dict__.items() if not k.startswith("__")})
