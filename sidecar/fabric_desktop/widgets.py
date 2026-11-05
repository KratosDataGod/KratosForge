"""``ipywidgets`` for notebook cells: sliders, dropdowns, text boxes, buttons and ``interact``.

Fabric and Databricks notebooks run ipywidgets through Jupyter's comm channel. KratosForge has no
Jupyter frontend, so this module stands in for the part of ``ipywidgets`` notebooks use: each
widget keeps its state here, ``display(widget)`` sends that state to the app as a ``widget``
output, and the app draws native controls. When a control changes, the app calls
``notebook_widget_event``; the kernel sets the value and runs the widget's handlers (``observe``,
``on_click``, ``interact``'s function) on a thread of its own, as it runs a cell, and returns the
widget's new state. Handlers run the person's own notebook code, nothing else.

``import ipywidgets`` finds this module ahead of a real ipywidgets, which can't draw without a
Jupyter frontend. Widgets live until the kernel restarts; the last ``MAX_WIDGETS`` are kept.
"""

from __future__ import annotations

import contextlib
import importlib.abc
import importlib.machinery
import itertools
import json
import sys
import threading
import traceback
import types
from collections import OrderedDict
from typing import Any, Callable, Iterator

MAX_WIDGETS = 1000
MAX_DEPTH = 20
__version__ = "8.1.5"  # the ipywidgets API this stands in for


class _Registry:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.widgets: OrderedDict[str, Widget] = OrderedDict()
        self.ids = itertools.count(1)
        # Set by the kernel: a context manager that sends a thread's output to an Output widget.
        self.capture: Callable[[Any], Any] | None = None
        self.show: Callable[[Any], None] | None = None

    def add(self, w: "Widget") -> str:
        with self.lock:
            wid = f"w{next(self.ids)}"
            self.widgets[wid] = w
            while len(self.widgets) > MAX_WIDGETS:
                self.widgets.popitem(last=False)
            return wid

    def get(self, wid: str) -> "Widget | None":
        with self.lock:
            return self.widgets.get(wid)

    def reset(self) -> None:
        with self.lock:
            self.widgets.clear()


registry = _Registry()


class Change(dict):
    """What an ``observe`` handler gets: ``change["new"]`` or ``change.new``."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


class Layout:
    """Accepted and ignored: the app lays controls out itself."""

    def __init__(self, **kw: Any):
        self.__dict__.update(kw)


Style = Layout


class Widget:
    _kind = "widget"
    _traits: tuple[str, ...] = ("description", "disabled", "tooltip")

    def __init__(self, **kw: Any):
        state = self.__dict__.get("_state")
        if state is None:
            state = {}
            object.__setattr__(self, "_state", state)
        for k, v in (("description", ""), ("disabled", False), ("tooltip", None)):
            state.setdefault(k, v)
        object.__setattr__(self, "_observers", [])
        object.__setattr__(self, "model_id", registry.add(self))
        self.layout = kw.pop("layout", None) or Layout()
        self.style = kw.pop("style", None) or Style()
        for k, v in kw.items():
            setattr(self, k, v)

    # Traits: setting one tells observers, as traitlets does.
    def __getattr__(self, name: str) -> Any:
        state = object.__getattribute__(self, "_state")
        if name in state:
            return state[name]
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in type(self)._traits:
            self._set(name, value)
        else:
            object.__setattr__(self, name, value)

    def _validate(self, name: str, value: Any) -> Any:
        return value

    def _set(self, name: str, value: Any) -> None:
        value = self._validate(name, value)
        old = self._state.get(name)
        self._state[name] = value
        try:
            same = old is value or (type(old) is type(value) and bool(old == value))
        except Exception:  # noqa: BLE001 - a value that can't compare (an array) counts as changed
            same = False
        if same:
            return
        for handler, names in list(self._observers):
            if names is None or name in names:
                handler(Change(name=name, old=old, new=value, owner=self, type="change"))

    def observe(self, handler: Callable[[Change], Any], names: Any = None, type: str = "change") -> None:  # noqa: A002
        self._observers.append((handler, None if names is None else ({names} if isinstance(names, str) else set(names))))

    def unobserve(self, handler: Callable[[Change], Any], names: Any = None, type: str = "change") -> None:  # noqa: A002
        self._observers[:] = [(h, n) for h, n in self._observers if h is not handler]

    def unobserve_all(self, name: Any = None) -> None:
        self._observers.clear()

    def close(self) -> None:
        with registry.lock:
            registry.widgets.pop(self.model_id, None)

    def _ipython_display_(self) -> None:
        if registry.show is not None:
            registry.show(self)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(description={self.description!r})"

    # ---- the state the app draws

    def _view(self, depth: int = 0, seen: set[str] | None = None) -> dict[str, Any]:
        out = {"id": self.model_id, "kind": self._kind, "description": str(self.description or ""),
               "disabled": bool(self.disabled)}
        if self.tooltip:
            out["tooltip"] = str(self.tooltip)
        out.update(self._extra(depth, seen if seen is not None else set()))
        return out

    def _extra(self, depth: int, seen: set[str]) -> dict[str, Any]:
        return {}

    # ---- an event from the app

    def _event(self, event: dict[str, Any]) -> None:
        if "value" in event and "value" in self._traits:
            self.value = self._from_app(event["value"])

    def _from_app(self, value: Any) -> Any:
        return value


class DOMWidget(Widget):
    pass


class ValueWidget(DOMWidget):
    _traits = Widget._traits + ("value",)

    def __init__(self, value: Any = None, **kw: Any):
        super().__init__(**kw)
        if value is not None or "value" not in self._state:
            self._state["value"] = self._validate("value", value if value is not None else self._default())

    def _default(self) -> Any:
        return None

    def _extra(self, depth: int, seen: set[str]) -> dict[str, Any]:
        return {"value": _plain(self.value)}


# ---- numbers

class _Number(ValueWidget):
    _traits = ValueWidget._traits + ("min", "max", "step", "readout", "continuous_update", "orientation")
    _cast: Callable[[Any], Any] = int
    _bounded = True

    def __init__(self, value: Any = None, min: Any = 0, max: Any = 100, step: Any = 1, **kw: Any):  # noqa: A002
        object.__setattr__(self, "_state", {"min": self._cast(min), "max": self._cast(max), "step": self._cast(step)})
        if self._bounded:
            _check_bounds(self._state["min"], self._state["max"])
        kw.setdefault("readout", True)
        kw.setdefault("continuous_update", True)
        kw.setdefault("orientation", "horizontal")
        super().__init__(value=value, **kw)

    def _default(self) -> Any:
        return self._state.get("min", 0) if self._bounded else self._cast(0)

    def _validate(self, name: str, value: Any) -> Any:
        if name in ("value", "min", "max", "step"):
            value = self._cast(value)
        if name == "value" and self._bounded:
            value = min(max(value, self._state.get("min", value)), self._state.get("max", value))
        return value

    def _set(self, name: str, value: Any) -> None:
        bound = self._bounded and name in ("min", "max")
        if bound:
            value = self._cast(value)
            _check_bounds(*(value if name == n else self._state[n] for n in ("min", "max")), setting=name)
        super()._set(name, value)
        if bound:
            self.value = self._state["value"]  # clamps into the new bounds, as ipywidgets does

    def _from_app(self, value: Any) -> Any:
        return self._cast(float(value)) if self._cast is int else float(value)

    def _extra(self, depth: int, seen: set[str]) -> dict[str, Any]:
        out = super()._extra(depth, seen)
        if self._bounded:
            out.update(min=self.min, max=self.max)
        out["step"] = self.step
        return out


def _check_bounds(lo: Any, hi: Any, setting: str = "min") -> None:
    if lo > hi:  # worded as ipywidgets words it, after the bound being set
        raise TraitError(f"setting max < min ({hi} < {lo})" if setting == "max" else f"setting min > max ({lo} > {hi})")


class IntSlider(_Number):
    _kind = "slider"


class FloatSlider(_Number):
    _kind = "slider"
    _cast = float

    def __init__(self, value: Any = None, min: float = 0.0, max: float = 10.0, step: float = 0.1, **kw: Any):  # noqa: A002
        super().__init__(value, min, max, step, **kw)


class BoundedIntText(_Number):
    _kind = "number"


class BoundedFloatText(FloatSlider):
    _kind = "number"


class IntText(_Number):
    _kind = "number"
    _bounded = False


class FloatText(IntText):
    _cast = float


class IntProgress(_Number):
    _kind = "progress"


class FloatProgress(FloatSlider):
    _kind = "progress"


class _Range(ValueWidget):
    """A two-handle range slider, drawn as two number boxes."""
    _kind = "range"
    _traits = ValueWidget._traits + ("min", "max", "step")
    _cast: Callable[[Any], Any] = int

    def __init__(self, value: Any = None, min: Any = 0, max: Any = 100, step: Any = 1, **kw: Any):  # noqa: A002
        object.__setattr__(self, "_state", {"min": self._cast(min), "max": self._cast(max), "step": self._cast(step)})
        lo, hi = self._state["min"], self._state["max"]
        _check_bounds(lo, hi)
        super().__init__(value=value if value is not None else (lo + (hi - lo) // 4 if self._cast is int else lo + (hi - lo) / 4,
                                                               lo + 3 * (hi - lo) // 4 if self._cast is int else lo + 3 * (hi - lo) / 4), **kw)

    def _validate(self, name: str, value: Any) -> Any:
        if name == "value":
            a, b = sorted(self._cast(v) for v in value)
            lo, hi = self._state.get("min", a), self._state.get("max", b)
            return (min(max(a, lo), hi), min(max(b, lo), hi))
        return self._cast(value) if name in ("min", "max", "step") else value

    def _set(self, name: str, value: Any) -> None:
        bound = name in ("min", "max")
        if bound:
            value = self._cast(value)
            _check_bounds(*(value if name == n else self._state[n] for n in ("min", "max")), setting=name)
        super()._set(name, value)
        if bound:
            self.value = self._state["value"]

    def _from_app(self, value: Any) -> Any:
        return tuple(float(v) for v in value)

    def _extra(self, depth: int, seen: set[str]) -> dict[str, Any]:
        return {"value": list(self.value), "min": self.min, "max": self.max, "step": self.step}


class IntRangeSlider(_Range):
    pass


class FloatRangeSlider(_Range):
    _cast = float


# ---- booleans and text

class Checkbox(ValueWidget):
    _kind = "checkbox"

    def _default(self) -> Any:
        return False

    def _validate(self, name: str, value: Any) -> Any:
        return bool(value) if name == "value" else value


class ToggleButton(Checkbox):
    _kind = "toggle"


class Valid(Checkbox):
    _kind = "valid"


class Text(ValueWidget):
    _kind = "text"
    _traits = ValueWidget._traits + ("placeholder", "continuous_update")

    def __init__(self, value: Any = "", placeholder: str = "", **kw: Any):
        super().__init__(value=value, placeholder=placeholder, **kw)
        object.__setattr__(self, "_submit", [])

    def _default(self) -> Any:
        return ""

    def _validate(self, name: str, value: Any) -> Any:
        return "" if name == "value" and value is None else (str(value) if name == "value" else value)

    def on_submit(self, handler: Callable[["Text"], Any]) -> None:
        self._submit.append(handler)

    def _event(self, event: dict[str, Any]) -> None:
        super()._event(event)
        if event.get("submit"):
            for handler in list(self._submit):
                handler(self)

    def _extra(self, depth: int, seen: set[str]) -> dict[str, Any]:
        return {"value": self.value, "placeholder": str(self.placeholder or "")}


class Textarea(Text):
    _kind = "textarea"


class Password(Text):
    """Drawn as a password box; its value isn't sent back to the app."""
    _kind = "password"

    def _extra(self, depth: int, seen: set[str]) -> dict[str, Any]:
        return {"value": "", "placeholder": str(self.placeholder or ""), "filled": bool(self.value)}


class Label(ValueWidget):
    _kind = "label"

    def _default(self) -> Any:
        return ""

    def _validate(self, name: str, value: Any) -> Any:
        return str(value) if name == "value" else value


class HTML(Label):
    """Its HTML is drawn in the cell's sandboxed frame, like any HTML output."""
    _kind = "html"


HTMLMath = HTML


# ---- choices

class _Selection(ValueWidget):
    _kind = "dropdown"
    _traits = ValueWidget._traits + ("options", "index", "label")

    def __init__(self, options: Any = (), value: Any = None, index: Any = None, label: Any = None, **kw: Any):
        object.__setattr__(self, "_state", {})
        pairs = _pairs(options)
        self._state.update(options=pairs, index=None, label=None, value=None)
        super().__init__(**kw)
        if label is not None:
            self.label = label
        elif value is not None:
            self.value = value
        elif index is not None:
            self.index = index
        elif pairs:
            self.index = 0

    def _set(self, name: str, value: Any) -> None:
        pairs = self._state.get("options", [])
        if name == "options":
            pairs = _pairs(value)
            super()._set("options", pairs)
            # New options select the first one, as ipywidgets does, so value and label follow.
            self._state["index"] = None
            return self._set("index", 0 if pairs else None)
        if name == "index":
            i = None if value is None else int(value)
            if i is not None and not 0 <= i < len(pairs):
                raise ValueError(f"index {i} is out of range")
            label, val = pairs[i] if i is not None else (None, None)
        elif name == "value":
            hits = [i for i, (_, v) in enumerate(pairs) if v is value or v == value]
            if value is not None and not hits:
                raise ValueError(f"{value!r} isn't one of the options")
            i = hits[0] if hits else None
            label, val = pairs[i] if i is not None else (None, None)
        elif name == "label":
            hits = [i for i, (lab, _) in enumerate(pairs) if lab == value]
            if not hits:
                raise ValueError(f"{value!r} isn't one of the options")
            i = hits[0]
            label, val = pairs[i]
        else:
            return super()._set(name, value)
        old_index = self._state.get("index")
        self._state["label"] = label
        self._state["index"] = i
        super()._set("value", val)
        if old_index != i:
            for handler, names in list(self._observers):
                if names is not None and "index" in names:
                    handler(Change(name="index", old=old_index, new=i, owner=self, type="change"))

    def _event(self, event: dict[str, Any]) -> None:
        if "index" in event:
            self.index = event["index"]

    def _extra(self, depth: int, seen: set[str]) -> dict[str, Any]:
        return {"options": [str(lab) for lab, _ in self.options], "index": self.index}


class Dropdown(_Selection):
    pass


class RadioButtons(_Selection):
    _kind = "radio"


class Select(_Selection):
    pass


class ToggleButtons(_Selection):
    _kind = "radio"


class SelectionSlider(_Selection):
    pass


class SelectMultiple(ValueWidget):
    _kind = "multiselect"
    _traits = ValueWidget._traits + ("options", "index", "label")

    def __init__(self, options: Any = (), value: Any = (), index: Any = None, label: Any = None, **kw: Any):
        object.__setattr__(self, "_state", {"options": _pairs(options), "index": (), "label": ()})
        super().__init__(**kw)
        if label is not None:
            self.label = label
        elif index is not None:
            self.index = index
        else:
            self.value = tuple(value)

    def _set(self, name: str, value: Any) -> None:
        pairs = self._state["options"]
        if name == "index":
            idx = [int(i) for i in value]
            bad = [i for i in idx if not 0 <= i < len(pairs)]
            if bad:
                raise ValueError(f"index {bad[0]} is out of range")
            return self._set("value", tuple(pairs[i][1] for i in idx))
        if name == "label":
            labels = [lab for lab, _ in pairs]
            missing = [lab for lab in value if lab not in labels]
            if missing:
                raise ValueError(f"{missing[0]!r} isn't one of the options")
            return self._set("value", tuple(pairs[labels.index(lab)][1] for lab in value))
        super()._set(name, value)
        if name == "options":
            self.value = ()  # new options clear the selection, as ipywidgets does

    def _default(self) -> Any:
        return ()

    def _validate(self, name: str, value: Any) -> Any:
        if name == "value":
            values = [v for _, v in self._state["options"]]
            idx = []
            for item in value:
                if item not in values:
                    raise ValueError(f"{item!r} isn't one of the options")
                idx.append(values.index(item))
            self._state["index"] = tuple(idx)
            self._state["label"] = tuple(self._state["options"][i][0] for i in idx)
            return tuple(value)
        if name == "options":
            return _pairs(value)
        return value

    def _event(self, event: dict[str, Any]) -> None:
        if "index" in event:
            pairs = self._state["options"]
            self.value = tuple(pairs[int(i)][1] for i in event["index"] if 0 <= int(i) < len(pairs))

    def _extra(self, depth: int, seen: set[str]) -> dict[str, Any]:
        return {"options": [str(lab) for lab, _ in self.options], "index": list(self.index)}


# ---- buttons and output

class Button(DOMWidget):
    _kind = "button"
    _traits = Widget._traits + ("button_style", "icon")

    def __init__(self, **kw: Any):
        super().__init__(**kw)
        object.__setattr__(self, "_click", [])

    def on_click(self, handler: Callable[["Button"], Any], remove: bool = False) -> None:
        if remove:
            self._click[:] = [h for h in self._click if h is not handler]
        else:
            self._click.append(handler)

    def click(self) -> None:
        for handler in list(self._click):
            handler(self)

    def _event(self, event: dict[str, Any]) -> None:
        if event.get("click"):
            self.click()


class Output(DOMWidget):
    """``with out:`` sends what the block prints and displays to this widget instead of the cell."""
    _kind = "output"

    def __init__(self, **kw: Any):
        super().__init__(**kw)
        object.__setattr__(self, "_outputs", [])
        object.__setattr__(self, "_tail", [])  # text printed since, joined onto the last stream output when read
        object.__setattr__(self, "_lock", threading.Lock())
        object.__setattr__(self, "_stack", [])
        self._recount()

    @property
    def outputs(self) -> list[dict[str, Any]]:
        with self._lock:
            self._join()
            return self._outputs

    @outputs.setter
    def outputs(self, value: Any) -> None:  # ``out.outputs = ()`` clears it, as in ipywidgets
        with self._lock:
            object.__setattr__(self, "_outputs", list(value))
            object.__setattr__(self, "_tail", [])
        self._recount()  # the caps count from here, as after clear_output()

    def _join(self) -> None:
        """Join the printed text kept in pieces; one join instead of copying the text on every print."""
        if self._tail:
            self._outputs[-1] = {"type": "stream", "text": self._outputs[-1]["text"] + "".join(self._tail)}
            self._tail.clear()

    def _recount(self) -> None:
        # The same caps as a cell's own output (notebook.STREAM_CHARS, RICH_CHARS), counted from the
        # last clear_output: a control's handler can add to one Output on every click.
        object.__setattr__(self, "_chars", 0)
        object.__setattr__(self, "_rich", 0)
        object.__setattr__(self, "_cut", set())

    def __enter__(self) -> "Output":
        if registry.capture is None:
            return self
        cm = registry.capture(self)
        cm.__enter__()
        self._stack.append(cm)
        return self

    def __exit__(self, *exc: Any) -> bool:
        if self._stack:
            self._stack.pop().__exit__(*exc)
        return False

    def clear_output(self, wait: bool = False) -> None:
        self.outputs = []

    def append_stdout(self, text: str) -> None:
        self._write(text)

    append_stderr = append_stdout

    def append_display_data(self, value: Any) -> None:
        with self:
            if registry.show is not None:
                registry.show(value)

    def capture(self, clear_output: bool = False, wait: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
            def wrapped(*a: Any, **kw: Any) -> Any:
                if clear_output:
                    self.clear_output(wait)
                with self:
                    return fn(*a, **kw)
            return wrapped
        return decorate

    # what a captured block writes
    def _write(self, text: str) -> None:
        from .notebook import STREAM_CHARS
        if not text or "stream" in self._cut:
            return
        room = STREAM_CHARS - self._chars
        part = text[:room]
        object.__setattr__(self, "_chars", self._chars + len(part))
        with self._lock:
            if part and self._outputs and self._outputs[-1].get("type") == "stream":
                self._tail.append(part)
            elif part:
                self._outputs.append({"type": "stream", "text": part})
        if len(text) > room:
            self._cut.add("stream")
            self._append({"type": "text", "text": f"Output truncated: this output printed more than "
                                                  f"{STREAM_CHARS:,} characters. clear_output() starts it again."})

    def _add(self, output: dict[str, Any]) -> None:
        from .notebook import RICH_CHARS
        if output.get("type") != "error":  # an error always shows
            if "rich" in self._cut:
                return
            size = len(json.dumps(output, default=str))
            if self._rich + size > RICH_CHARS:
                self._cut.add("rich")
                output = {"type": "text", "text": f"Output truncated: this output's tables, charts and HTML passed "
                                                  f"{RICH_CHARS // 1_000_000} MB, so later ones aren't shown."}
            else:
                object.__setattr__(self, "_rich", self._rich + size)
        self._append(output)

    def _append(self, output: dict[str, Any]) -> None:
        with self._lock:
            self._join()
            self._outputs.append(output)

    def _replace(self, display_id: str, output: dict[str, Any]) -> None:
        """update_display inside ``with out:``: the outputs shown with ``display_id`` become
        ``output``, under the same rich cap as ``_add``."""
        from .notebook import _replace_capped
        with self._lock:
            self._join()
            hits = [i for i, o in enumerate(self._outputs) if o.get("display_id") == display_id]
            if not hits:
                return
            rich, output = _replace_capped(self._outputs, hits, output, self._rich)
            object.__setattr__(self, "_rich", rich)
            for i in hits:
                self._outputs[i] = output

    def _extra(self, depth: int, seen: set[str]) -> dict[str, Any]:
        with self._lock:
            self._join()
            outputs = list(self._outputs)
        return {"outputs": [_nested(o, depth, seen) for o in outputs]}


def _nested(output: dict[str, Any], depth: int, seen: set[str]) -> dict[str, Any]:
    """A widget displayed inside an Output shows its current state."""
    if output.get("type") == "widget":
        w = registry.get(output.get("model", ""))
        if w is not None:
            return {**output, "view": _view_of(w, depth + 1, seen)}
    return output


# ---- boxes

class Box(DOMWidget):
    _kind = "vbox"
    _traits = Widget._traits + ("children",)

    def __init__(self, children: Any = (), **kw: Any):
        super().__init__(children=tuple(children), **kw)

    def _extra(self, depth: int, seen: set[str]) -> dict[str, Any]:
        return {"children": [_view_of(c, depth + 1, seen) for c in self.children if isinstance(c, Widget)]}


class VBox(Box):
    pass


class HBox(Box):
    _kind = "hbox"


class GridBox(Box):
    pass


class _Titled(Box):
    """Accordion and Tab keep titles and the selected index, as ipywidgets does. The app draws the children stacked."""
    _traits = Box._traits + ("titles", "selected_index")

    def __init__(self, children: Any = (), titles: Any = (), selected_index: Any = None, **kw: Any):
        super().__init__(children=children, titles=tuple(titles), selected_index=selected_index, **kw)

    def set_title(self, index: int, title: Any) -> None:
        titles = list(self.titles) + [""] * (len(self.children) - len(self.titles))
        titles[int(index)] = "" if title is None else str(title)
        self.titles = tuple(titles)

    def get_title(self, index: int) -> str:
        titles = self.titles
        return titles[int(index)] if 0 <= int(index) < len(titles) else ""


class Accordion(_Titled):
    pass


class Tab(_Titled):
    pass


def _view_of(w: Widget, depth: int, seen: set[str]) -> dict[str, Any]:
    if depth > MAX_DEPTH or w.model_id in seen:
        return {"id": w.model_id, "kind": "label", "description": "", "disabled": False, "value": "(shown above)"}
    seen = seen | {w.model_id}
    return w._view(depth, seen)


def view(w: Widget) -> dict[str, Any]:
    """The state the app draws for ``w`` and everything inside it."""
    with registry.lock:
        return _view_of(w, 0, set())


# ---- interact

class fixed:  # noqa: N801 - ipywidgets' name
    """An argument interact passes as it is, with no control."""

    def __init__(self, value: Any):
        self.value = value


def _control(name: str, abbrev: Any) -> Widget | fixed | None:
    if isinstance(abbrev, (Widget, fixed)):
        if isinstance(abbrev, Widget) and not abbrev.description:
            abbrev.description = name
        return abbrev
    if isinstance(abbrev, bool):
        return Checkbox(value=abbrev, description=name)
    if isinstance(abbrev, int):
        v = abbrev
        return IntSlider(value=v, min=-v if v > 0 else 3 * v, max=3 * v if v > 0 else -v if v < 0 else 1, description=name)
    if isinstance(abbrev, float):
        v = abbrev
        lo, hi = (-v, 3 * v) if v > 0 else (3 * v, -v) if v < 0 else (0.0, 1.0)
        return FloatSlider(value=v, min=lo, max=hi, step=(hi - lo) / 100, description=name)
    if isinstance(abbrev, str):
        return Text(value=abbrev, description=name)
    if isinstance(abbrev, tuple) and 2 <= len(abbrev) <= 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in abbrev):
        if all(isinstance(x, int) for x in abbrev):
            lo, hi, step = (*abbrev, 1)[:3]
            return IntSlider(value=(lo + hi) // 2, min=lo, max=hi, step=step, description=name)
        lo, hi = float(abbrev[0]), float(abbrev[1])
        step = float(abbrev[2]) if len(abbrev) == 3 else (hi - lo) / 100
        return FloatSlider(value=(lo + hi) / 2, min=lo, max=hi, step=step, description=name)
    if isinstance(abbrev, (list, tuple, dict)):
        return Dropdown(options=abbrev, description=name)
    return None


class interactive(VBox):  # noqa: N801 - ipywidgets' name
    """``interactive(f, x=...)``: a control per argument and an Output; changing a control calls ``f``."""

    def __init__(self, __interact_f: Callable[..., Any], __options: dict[str, Any] | None = None, **kwargs: Any):
        import inspect
        self.f = __interact_f
        manual = bool((__options or {}).get("manual"))
        try:
            params = inspect.signature(__interact_f).parameters
        except (TypeError, ValueError):
            params = {}
        controls: dict[str, Widget | fixed] = {}
        for name, abbrev in kwargs.items():
            c = _control(name, abbrev)
            if c is None:
                raise ValueError(f"{abbrev!r} can't be turned into a control for {name}.")
            controls[name] = c
        for name, p in params.items():
            if name not in controls and p.default is not inspect.Parameter.empty:
                c = _control(name, p.default)
                if c is not None:
                    controls[name] = c
        self.controls = controls
        self.out = Output()
        self.result = None
        widgets = [c for c in controls.values() if isinstance(c, Widget)]
        kids = list(widgets)
        if manual:
            self.manual_button = Button(description=(__options or {}).get("manual_name") or "Run Interact")
            self.manual_button.on_click(lambda _b: self.update())
            kids.append(self.manual_button)
        super().__init__(children=kids + [self.out])
        if not manual:
            for w in widgets:
                w.observe(lambda _c: self.update(), names="value")

    @property
    def kwargs(self) -> dict[str, Any]:
        return {k: (c.value if isinstance(c, fixed) else c.value) for k, c in self.controls.items()}

    def update(self, *_a: Any) -> None:
        self.out.clear_output()
        with self.out:
            try:
                self.result = self.f(**self.kwargs)
            except Exception as exc:  # noqa: BLE001 - as ipywidgets: the error shows in the output and the controls stay
                self.out._add(_error_output(exc))
                return
            if self.result is not None and registry.show is not None:
                registry.show(self.result)


def _error_output(exc: Exception) -> dict[str, Any]:
    """The error a cell would show, without this module's own frames."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename != __file__]
    lines = traceback.format_list(frames) + traceback.format_exception_only(type(exc), exc)
    return {"type": "error", "ename": type(exc).__name__, "evalue": str(exc),
            "traceback": "Traceback (most recent call last):\n" + "".join(lines)}


def interact(__interact_f: Callable[..., Any] | None = None, **kwargs: Any) -> Any:
    """``interact(f, x=10)`` or ``@interact(x=10)``: shows the controls and calls ``f`` with them."""
    def show(f: Callable[..., Any]) -> Callable[..., Any]:
        box = interactive(f, None, **kwargs)
        box.update()
        if registry.show is not None:
            registry.show(box)
        f.widget = box  # type: ignore[attr-defined]
        return f
    if __interact_f is None:
        return show
    return show(__interact_f)


def _interact_manual(__interact_f: Callable[..., Any] | None = None, __manual_name: str = "", **kwargs: Any) -> Any:
    def show(f: Callable[..., Any]) -> Callable[..., Any]:
        box = interactive(f, {"manual": True, "manual_name": __manual_name}, **kwargs)
        if registry.show is not None:
            registry.show(box)
        f.widget = box  # type: ignore[attr-defined]
        return f
    return show if __interact_f is None else show(__interact_f)


def _options(**opts: Any) -> Callable[..., Any]:
    """``interact.options(manual=True, manual_name="Go")``: the button is named as asked, as in ipywidgets."""
    def apply(f: Callable[..., Any] | None = None, **kw: Any) -> Any:
        if opts.get("manual"):
            return _interact_manual(f, str(opts.get("manual_name") or ""), **kw)
        return interact(f, **kw)
    return apply


interact.options = _options  # type: ignore[attr-defined]
interact_manual = _interact_manual


def interactive_output(f: Callable[..., Any], controls: dict[str, Widget]) -> Output:
    out = Output()

    def run(*_a: Any) -> None:
        out.clear_output()
        with out:
            try:
                result = f(**{k: w.value for k, w in controls.items()})
            except Exception as exc:  # noqa: BLE001 - as ipywidgets: the error shows in the output
                out._add(_error_output(exc))
                return
            if result is not None and registry.show is not None:
                registry.show(result)
    for w in controls.values():
        w.observe(run, names="value")
    run()
    return out




class TraitError(Exception):
    """As traitlets' TraitError: bounds where min > max, or a link whose two transforms don't undo each other."""


class _Link:
    """What ``link`` and ``dlink`` return, as traitlets does: ``unlink()`` stops it. While one side is
    being set, a change coming back from the other is ignored; if that leaves the two sides out of
    step (transforms that aren't each other's inverse), it's a broken link, as in traitlets."""

    def __init__(self, source: tuple[Widget, str], target: tuple[Widget, str],
                 forward: Callable[[Any], Any] | None, backward: Callable[[Any], Any] | None, both: bool):
        self.source, self.target = source, target
        (sw, sn), (tw, tn) = source, target
        to = forward or (lambda v: v)
        back = backward or (lambda v: v)
        self._updating = False

        def update(src: Widget, sname: str, dst: Widget, dname: str, fn: Callable[[Any], Any]) -> Callable[[Any], None]:
            def handler(change: Any) -> None:
                if self._updating:
                    return
                self._updating = True
                try:
                    setattr(dst, dname, fn(change["new"]))
                    if both and getattr(src, sname) != change["new"]:
                        raise TraitError(f"Broken link {self}: the source value changed while updating the target.")
                finally:
                    self._updating = False
            return handler

        setattr(tw, tn, to(getattr(sw, sn)))
        self._handlers = [(sw, update(sw, sn, tw, tn, to), sn)]
        if both:
            self._handlers.append((tw, update(tw, tn, sw, sn, back), tn))
        for w, handler, name in self._handlers:
            w.observe(handler, names=name)

    def __repr__(self) -> str:
        (sw, sn), (tw, tn) = self.source, self.target
        return f"<link {type(sw).__name__}.{sn} -> {type(tw).__name__}.{tn}>"

    def unlink(self) -> None:
        for w, handler, name in self._handlers:
            w.unobserve(handler, names=name)
        self._handlers = []


def link(source: tuple[Widget, str], target: tuple[Widget, str], transform: Any = None) -> _Link:
    """Keep two traits equal both ways; ``transform`` is a (forward, backward) pair of functions."""
    forward, backward = transform if transform else (None, None)
    return _Link(source, target, forward, backward, both=True)


def dlink(source: tuple[Widget, str], target: tuple[Widget, str], transform: Callable[[Any], Any] | None = None) -> _Link:
    """Copy ``source``'s trait to ``target``'s whenever it changes, one way, through ``transform``."""
    return _Link(source, target, transform, None, both=False)


def jslink(source: tuple[Widget, str], target: tuple[Widget, str]) -> _Link:
    """In Jupyter the browser keeps the two in step; here the kernel holds every widget, so it does."""
    return link(source, target)


def jsdlink(source: tuple[Widget, str], target: tuple[Widget, str]) -> _Link:
    return dlink(source, target)


# ---- helpers

def _pairs(options: Any) -> list[tuple[str, Any]]:
    if isinstance(options, dict):
        return [(str(k), v) for k, v in options.items()]
    out = []
    for o in options:
        if isinstance(o, tuple) and len(o) == 2:
            out.append((str(o[0]), o[1]))
        else:
            out.append((str(o), o))
    return out


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return str(value)


# ---- the event from the app

class WidgetGone(LookupError):
    """The app's control points at a widget the kernel no longer holds."""


def handle(model: str, event: dict[str, Any]) -> None:
    """Apply one change from the app: a new value, an index, a click or a submit."""
    w = registry.get(model)
    if w is None:
        raise WidgetGone(model)
    if w.disabled:
        return
    w._event(dict(event or {}))


# ---- the module cells import

def build() -> types.ModuleType:
    mod = types.ModuleType("ipywidgets", "ipywidgets isn't drawn by Jupyter here; KratosForge provides these widgets.")
    for name, value in list(globals().items()):
        if isinstance(value, type) and issubclass(value, (Widget, Layout)) or name in (
                "interact", "interactive", "interact_manual", "interactive_output", "fixed", "link", "dlink", "jslink", "jsdlink", "TraitError", "Layout",
                "Style", "Change"):
            setattr(mod, name, value)
    mod.__version__ = __version__  # type: ignore[attr-defined]
    mod.version_info = (8, 1, 5)  # type: ignore[attr-defined]
    mod.widgets = mod  # type: ignore[attr-defined]  # from ipywidgets import widgets
    mod.__path__ = []  # type: ignore[attr-defined]
    mod.__kratosforge__ = True  # type: ignore[attr-defined]
    return mod


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """First on ``sys.meta_path`` for ``ipywidgets`` only: a real ipywidgets can't draw without Jupyter."""

    def __init__(self) -> None:
        self.mod = build()

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> importlib.machinery.ModuleSpec | None:
        if fullname not in ("ipywidgets", "ipywidgets.widgets"):
            return None
        return importlib.machinery.ModuleSpec(fullname, self, is_package=fullname == "ipywidgets")

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
        return self.mod

    def exec_module(self, module: types.ModuleType) -> None:
        return None


_finder = _Finder()


def install(show: Callable[[Any], None], capture: Callable[[Any], Any]) -> None:
    """Bind the widgets to a kernel: ``show`` displays a value in the running cell, ``capture(out)``
    sends a thread's output to an Output widget. Clears the widgets of the kernel before."""
    registry.reset()
    registry.show = show
    registry.capture = capture
    if _finder not in sys.meta_path:
        sys.meta_path.insert(0, _finder)
    for name in ("ipywidgets", "ipywidgets.widgets"):
        if not getattr(sys.modules.get(name), "__kratosforge__", True):
            del sys.modules[name]  # a real ipywidgets a library imported: cells get this one


@contextlib.contextmanager
def nothing() -> Iterator[None]:
    yield
