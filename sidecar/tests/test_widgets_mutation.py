"""ipywidgets stand-in (widgets.py) behaviour that mutation testing (batch 11,
docs/quality/mutation-testing.md) showed no test checked: how deep a nested view goes, the controls
interact picks for each kind of value, the defaults of a range slider, Output used without a kernel,
and the module that replaces a real ipywidgets."""

import json
import sys

import pytest

from fabric_desktop import notebook
from fabric_desktop import widgets as W


@pytest.fixture(autouse=True)
def no_kernel(monkeypatch):
    monkeypatch.setattr(W.registry, "show", None)
    monkeypatch.setattr(W.registry, "capture", None)


# ---- how deep a view goes

def _chain(n):
    """n VBoxes, each holding the next, around one Label; returns the outer box and every widget."""
    inner = W.Label(value="leaf")
    widgets = [inner]
    for _ in range(n):
        widgets.append(W.VBox([widgets[-1]]))
    return widgets[-1], widgets[::-1]


def _at(view, depth):
    for _ in range(depth):
        view = view["children"][0]
    return view


def test_a_view_is_drawn_to_max_depth_and_shows_a_placeholder_below_it():
    assert W.MAX_DEPTH == 20
    outer, widgets = _chain(W.MAX_DEPTH + 1)
    v = W.view(outer)
    # the outer box is depth 0: everything down to depth 20 is drawn, depth 21 is a placeholder
    assert _at(v, W.MAX_DEPTH)["id"] == widgets[W.MAX_DEPTH].model_id
    assert _at(v, W.MAX_DEPTH)["kind"] == "vbox"
    leaf = _at(v, W.MAX_DEPTH + 1)
    assert leaf == {"id": widgets[W.MAX_DEPTH + 1].model_id, "kind": "label", "description": "",
                    "disabled": False, "value": "(shown above)"}


def test_a_chain_exactly_max_depth_deep_is_drawn_to_its_leaf():
    outer, widgets = _chain(W.MAX_DEPTH)
    leaf = _at(W.view(outer), W.MAX_DEPTH)
    assert leaf["id"] == widgets[-1].model_id and leaf["value"] == "leaf"


# ---- values from the app and defaults

def test_an_int_slider_reads_a_number_from_the_app_as_an_int():
    w = W.IntSlider(value=1, min=0, max=10)
    w._event({"value": "3.7"})
    assert w.value == 3 and type(w.value) is int
    f = W.FloatSlider(value=1.0, min=0, max=10)
    f._event({"value": "3.5"})
    assert f.value == 3.5 and type(f.value) is float


def test_a_range_slider_starts_at_a_quarter_and_three_quarters_of_its_bounds():
    assert W.IntRangeSlider(min=10, max=50).value == (20, 40)
    assert W.IntRangeSlider(min=0, max=7).value == (1, 5)  # (7 // 4, 3 * 7 // 4): whole numbers
    assert W.FloatRangeSlider(min=2.0, max=10.0).value == (4.0, 8.0)
    assert W.FloatRangeSlider(min=0.0, max=1.0).value == (0.25, 0.75)


def test_text_turns_none_into_an_empty_value_and_leaves_its_other_settings_alone():
    t = W.Text(value=None)
    assert t.value == ""
    t.value = None
    assert t.value == ""
    t.value = 12
    assert t.value == "12"
    t.placeholder = None
    assert t.placeholder is None


def test_a_dropdowns_description_and_other_settings_can_be_changed():
    d = W.Dropdown(options=["a", "b"], value="a")
    d.description = "Pick one"
    d.disabled = True
    assert d.description == "Pick one" and d.disabled is True
    assert d.value == "a" and d.index == 0


# ---- Output without a kernel bound

def test_output_without_a_kernel_is_its_own_context_and_never_swallows_an_error():
    out = W.Output()
    with out as entered:
        assert entered is out
    with pytest.raises(ValueError, match="boom"):
        with out:
            raise ValueError("boom")


def test_outputs_capture_decorator_keeps_what_was_there_and_returns_the_result():
    out = W.Output()
    out.append_stdout("before\n")

    @out.capture()
    def double(x):
        return 2 * x

    assert double(21) == 42
    assert out.outputs == [{"type": "stream", "text": "before\n"}]

    @out.capture(clear_output=True)
    def again():
        return "done"

    assert again() == "done"
    assert out.outputs == []


def test_an_output_that_exactly_fills_the_rich_cap_is_shown(monkeypatch):
    shown = {"type": "html", "html": "<b>x</b>"}
    monkeypatch.setattr(notebook, "RICH_CHARS", len(json.dumps(shown)))
    out = W.Output()
    out._add(dict(shown))
    assert out.outputs == [shown]
    out.clear_output()
    out._add(dict(shown))  # counted from zero again after clear_output
    assert out.outputs == [shown]
    out._add(dict(shown))  # one more doesn't fit
    assert out.outputs[-1]["type"] == "text" and "Output truncated" in out.outputs[-1]["text"]


# ---- the control interact picks for a value

def _controls(**kw):
    seen = []

    def f(**args):
        seen.append(args)
    box = W.interactive(f, None, **kw)
    return {c.description: c for c in box.children if isinstance(c, W.ValueWidget) or isinstance(c, W.Dropdown)}


@pytest.mark.parametrize("v, lo, hi", [(1, -1, 3), (5, -5, 15), (0, 0, 1), (-1, -3, 1), (-5, -15, 5)])
def test_an_int_gives_a_slider_around_it(v, lo, hi):
    c = _controls(x=v)["x"]
    assert isinstance(c, W.IntSlider)
    assert (c.value, c.min, c.max, c.step) == (v, lo, hi, 1)


@pytest.mark.parametrize("v, lo, hi", [(2.0, -2.0, 6.0), (-2.0, -6.0, 2.0), (0.0, 0.0, 1.0)])
def test_a_float_gives_a_slider_around_it_in_hundredths(v, lo, hi):
    c = _controls(x=v)["x"]
    assert isinstance(c, W.FloatSlider)
    assert (c.value, c.min, c.max) == (v, lo, hi)
    assert c.step == pytest.approx((hi - lo) / 100)


def test_a_two_number_tuple_gives_a_slider_with_a_step_of_one():
    c = _controls(x=(0, 10))["x"]
    assert isinstance(c, W.IntSlider)
    assert (c.value, c.min, c.max, c.step) == (5, 0, 10, 1)
    c = _controls(x=(0, 10, 2))["x"]
    assert (c.value, c.min, c.max, c.step) == (5, 0, 10, 2)


@pytest.mark.parametrize("abbrev", [(1, 2, 3, 4), [1, 2], [1, 2, 3], ("a", "b"), (1, True)])
def test_anything_else_listed_gives_a_dropdown(abbrev):
    c = _controls(x=abbrev)["x"]
    assert type(c) is W.Dropdown
    assert [v for _, v in c.options] == list(abbrev)
    assert c.value == abbrev[0]


def test_interact_as_a_decorator_and_through_options_gives_back_the_function():
    @W.interact(x=1)
    def f(x):
        return x

    assert callable(f) and f(3) == 3 and isinstance(f.widget, W.interactive)

    def g(x):
        return x
    assert W.interact.options(manual=False)(g, x=2) is g
    assert isinstance(g.widget, W.interactive)
    assert W.interact.options(manual=True, manual_name="Go")(g, x=2) is g


# ---- the module cells import

def test_installing_twice_keeps_this_module(monkeypatch):
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    for name in ("ipywidgets", "ipywidgets.widgets"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    W.install(lambda v: None, lambda out: W.nothing())
    import ipywidgets
    assert ipywidgets is W._finder.mod and ipywidgets.__kratosforge__ is True
    W.install(lambda v: None, lambda out: W.nothing())
    assert sys.modules["ipywidgets"] is W._finder.mod


def test_output_with_a_kernel_bound_is_its_own_context_too(monkeypatch):
    calls = []

    class Capture:
        def __init__(self, out):
            self.out = out

        def __enter__(self):
            calls.append("enter")

        def __exit__(self, *exc):
            calls.append("exit")
    monkeypatch.setattr(W.registry, "capture", Capture)
    out = W.Output()
    with out as entered:
        assert entered is out
    assert calls == ["enter", "exit"]
