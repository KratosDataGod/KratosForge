"""ipywidgets stand-in edges (widgets.py) the widget tests don't reach: displaying and capturing
with and without a kernel bound, Output's decorator and display_data, a widget shown inside an
Output, a function interact can't read the signature of, and a real ipywidgets already imported."""

import sys
import types

import pytest

from fabric_desktop import widgets as W


@pytest.fixture
def bound(monkeypatch):
    shown, captured = [], []

    class Capture:
        def __init__(self, out):
            self.out = out

        def __enter__(self):
            captured.append(("enter", self.out.model_id))

        def __exit__(self, *exc):
            captured.append(("exit", self.out.model_id))
    monkeypatch.setattr(W.registry, "show", shown.append)
    monkeypatch.setattr(W.registry, "capture", Capture)
    return shown, captured


def test_displaying_a_widget_goes_to_the_kernel_only_when_one_is_bound(monkeypatch, bound):
    shown, _ = bound
    w = W.IntSlider(5)
    w._ipython_display_()
    assert shown == [w]
    monkeypatch.setattr(W.registry, "show", None)
    w._ipython_display_()  # no kernel: nothing happens
    assert shown == [w]


def test_a_tooltip_and_the_plain_defaults_are_in_the_view():
    view = W.Checkbox(tooltip="Tick to include").__dict__["_state"]
    assert view["value"] is False
    assert W.view(W.Checkbox(tooltip="Tick to include"))["tooltip"] == "Tick to include"
    assert W.Text().value == "" and W.Label().value == ""
    assert W.ValueWidget().value is None


def test_select_multiple_refuses_a_value_not_in_the_options_and_takes_new_options():
    w = W.SelectMultiple(options=["a", "b"], value=["a"])
    with pytest.raises(ValueError, match="'c' isn't one of the options"):
        w.value = ("c",)
    w.options = [("One", 1), ("Two", 2)]
    assert w.value == () and w.options == [("One", 1), ("Two", 2)]
    w.value = (2,)
    assert w.label == ("Two",) and w.index == (1,)


def test_output_without_a_kernel_captures_nothing_and_with_one_enters_and_leaves(monkeypatch, bound):
    _, captured = bound
    out = W.Output()
    with out:
        pass
    assert captured == [("enter", out.model_id), ("exit", out.model_id)]
    monkeypatch.setattr(W.registry, "capture", None)
    with out as same:
        assert same is out
    assert len(captured) == 2


def test_display_data_and_the_capture_decorator_go_through_the_output(bound):
    shown, captured = bound
    out = W.Output()
    out.append_stdout("hello")
    out.append_display_data("a value")
    assert shown == ["a value"]

    @out.capture(clear_output=True)
    def work(x):
        return x * 2
    assert work(21) == 42
    assert out.outputs == []  # cleared before the function ran
    assert captured.count(("enter", out.model_id)) == 2


def test_replacing_a_display_id_the_output_never_showed_changes_nothing():
    out = W.Output()
    out.append_stdout("kept")
    before = out.outputs
    out._replace("never-shown", {"type": "text", "text": "new"})
    assert out.outputs == before


def test_a_widget_shown_inside_an_output_shows_its_current_state():
    out = W.Output()
    inner = W.IntSlider(3, description="n")
    out._append({"type": "widget", "model": inner.model_id})
    out._append({"type": "widget", "model": "gone"})
    view = W.view(out)
    first, second = view["outputs"]
    assert first["view"]["value"] == 3 and "view" not in second


def test_an_unnamed_widget_passed_to_interact_takes_the_parameter_name():
    slider = W.IntSlider(2)
    assert W._control("rows", slider) is slider and slider.description == "rows"
    named = W.IntSlider(2, description="Rows")
    assert W._control("rows", named).description == "Rows"


def test_interact_on_a_callable_without_a_signature_still_builds(monkeypatch):
    import inspect

    def no_signature(f):
        raise ValueError("no signature found for builtin")
    monkeypatch.setattr(inspect, "signature", no_signature)
    seen = []
    ui = W.interactive(lambda **kw: seen.append(kw), n=5)
    assert ui.kwargs == {"n": 5}


def test_interact_options_with_manual_add_a_run_button(bound):
    seen = []
    W.interact.options(manual=True, manual_name="Go")(lambda n: seen.append(n), n=3)
    shown, _ = bound
    ui = shown[-1]
    buttons = [c for c in ui.children if isinstance(c, W.Button)]
    assert buttons and buttons[0].description == "Go"
    assert seen == []  # manual: nothing runs until the button is clicked
    buttons[0].click()
    assert seen == [3]


@pytest.mark.parametrize("value, plain", [((1, "a", None), [1, "a", None]), ([[1], (2,)], [[1], [2]]),
                                          (b"x", "b'x'"), (1.5, 1.5)])
def test_values_sent_to_the_app_are_plain(value, plain):
    assert W._plain(value) == plain


def test_a_real_ipywidgets_already_imported_is_replaced_by_this_one(monkeypatch):
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    real = types.ModuleType("ipywidgets")
    real.__kratosforge__ = False
    monkeypatch.setitem(sys.modules, "ipywidgets", real)
    monkeypatch.setattr(W.registry, "show", None)
    monkeypatch.setattr(W.registry, "capture", None)
    W.install(lambda v: None, lambda out: W.nothing())
    assert "ipywidgets" not in sys.modules or sys.modules["ipywidgets"] is not real
    assert sys.meta_path[0] is W._finder
    with W.nothing():
        pass
