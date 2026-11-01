"""ipywidgets parity: the documented behaviors test_ipywidgets.py doesn't reach. Each follows
ipywidgets 8 (traitlets) or says plainly why not. Test-only; nothing leaves this PC."""

from __future__ import annotations

import pytest

from fabric_desktop import widgets as w
from fabric_desktop.notebook import Kernel


def changes(widget, names=None):
    seen = []
    widget.observe(lambda c: seen.append((c.name, c.old, c.new)), names=names)
    return seen


# ---------------------------------------------------------------- traits and observers


def test_observe_reports_only_real_changes_to_the_names_asked_for():
    s = w.IntSlider(5)
    seen = changes(s, "value")
    s.value = 5
    s.description = "n"
    s.value = 7
    assert seen == [("value", 5, 7)]
    assert s.description == "n" and repr(s) == "IntSlider(description='n')"


def test_unobserve_and_unobserve_all_stop_the_handlers():
    s = w.IntSlider()
    seen = []

    def h(c):
        seen.append(c.new)

    s.observe(h)
    s.value = 1
    s.unobserve(h)
    s.value = 2
    s.observe(h, ["value"])
    s.unobserve_all()
    s.value = 3
    assert seen == [1]


def test_a_change_reads_as_attributes_and_a_missing_one_says_so():
    c = w.Change(name="value", old=1, new=2)
    assert (c.name, c.old, c.new) == ("value", 1, 2)
    with pytest.raises(AttributeError):
        c.owner  # noqa: B018
    with pytest.raises(AttributeError):
        w.IntSlider().nope  # noqa: B018


def test_a_value_that_cant_compare_counts_as_changed():
    class Odd:
        def __eq__(self, other):
            raise TypeError("ambiguous")

    lab = w.ValueWidget(Odd())
    seen = changes(lab, "value")
    lab.value = Odd()
    assert len(seen) == 1


def test_a_closed_widget_is_gone_and_a_disabled_one_ignores_the_app():
    s = w.IntSlider(1)
    s.close()
    with pytest.raises(w.WidgetGone):
        w.handle(s.model_id, {"value": 2})
    t = w.IntSlider(1, disabled=True)
    w.handle(t.model_id, {"value": 9})
    assert t.value == 1


def test_the_oldest_widgets_are_let_go_past_the_cap(monkeypatch):
    monkeypatch.setattr(w, "MAX_WIDGETS", 3)
    first = w.Label("a")
    for _ in range(3):
        w.Label("b")
    assert w.registry.get(first.model_id) is None


# ---------------------------------------------------------------- numbers and ranges


def test_a_number_is_clamped_into_its_bounds_and_follows_new_bounds():
    s = w.IntSlider(50, min=0, max=100)
    s.value = 500
    assert s.value == 100
    s.max = 10
    assert s.value == 10
    with pytest.raises(w.TraitError, match="max"):
        s.max = -1
    with pytest.raises(w.TraitError):
        w.IntSlider(min=5, max=1)


def test_unbounded_number_boxes_take_any_value_and_floats_from_the_app():
    t = w.IntText()
    assert t.value == 0
    t.value = 10**6
    assert t.value == 10**6 and "min" not in w.view(t)
    w.handle(t.model_id, {"value": "7.0"})
    assert t.value == 7
    f = w.FloatText()
    w.handle(f.model_id, {"value": "2.5"})
    assert f.value == 2.5


@pytest.mark.parametrize("cls", [w.BoundedIntText, w.BoundedFloatText, w.IntProgress, w.FloatProgress])
def test_bounded_boxes_and_progress_bars_clamp(cls):
    s = cls(min=0, max=10)
    s.value = 99
    assert s.value == 10


def test_a_range_is_sorted_clamped_and_follows_new_bounds():
    r = w.IntRangeSlider(min=0, max=100)
    assert r.value == (25, 75)
    r.value = (90, 10)
    assert r.value == (10, 90)
    r.value = (-5, 500)
    assert r.value == (0, 100)
    r.max = 50
    assert r.value == (0, 50)
    with pytest.raises(w.TraitError):
        r.min = 60
    fr = w.FloatRangeSlider(min=0.0, max=1.0)
    assert fr.value == (0.25, 0.75)
    w.handle(fr.model_id, {"value": ["0.1", "0.2"]})
    assert fr.value == (0.1, 0.2) and w.view(fr)["value"] == [0.1, 0.2]


# ---------------------------------------------------------------- text, booleans and labels


def test_text_submits_to_its_handlers_and_none_is_empty():
    t = w.Text("a", placeholder="type")
    got = []
    t.on_submit(lambda box: got.append(box.value))
    w.handle(t.model_id, {"value": "hi", "submit": True})
    t.value = None
    assert got == ["hi"] and t.value == "" and w.view(t)["placeholder"] == "type"


def test_a_password_is_never_sent_back_to_the_app():
    p = w.Password("s3cret")
    v = w.view(p)
    assert v["value"] == "" and v["filled"] is True and "s3cret" not in str(v)


def test_checkboxes_and_labels_keep_their_types():
    assert w.Valid(1).value is True and w.ToggleButton(0).value is False
    assert w.Label(5).value == "5" and w.HTML("<b>x</b>").value == "<b>x</b>"
    assert w.HTMLMath is w.HTML


# ---------------------------------------------------------------- choices


def test_a_selection_by_label_index_or_value_keeps_all_three_in_step():
    d = w.Dropdown(options=[("One", 1), ("Two", 2)], label="Two")
    assert (d.value, d.index, d.label) == (2, 1, "Two")
    seen = changes(d, ["index"])
    d.index = 0
    assert (d.value, d.label) == (1, "One") and seen == [("index", 1, 0)]
    for bad in ({"value": 3}, {"label": "Three"}, {"index": 5}):
        with pytest.raises(ValueError):
            setattr(d, *next(iter(bad.items())))
    d.value = None
    assert (d.index, d.label) == (None, None)


def test_new_options_select_the_first_and_no_options_select_nothing():
    d = w.Select(options=["a", "b"], index=1)
    d.options = ["x", "y"]
    assert (d.value, d.index) == ("x", 0)
    d.options = []
    assert (d.value, d.index) == (None, None)
    assert w.ToggleButtons(options=["a"])._kind == "radio" and w.SelectionSlider(options=["a"]).value == "a"


def test_the_app_picks_a_choice_by_index():
    d = w.RadioButtons(options={"One": 1, "Two": 2})
    w.handle(d.model_id, {"index": 1})
    assert d.value == 2 and w.view(d)["options"] == ["One", "Two"]


def test_a_multiselect_by_label_or_index_and_new_options_clear_it():
    m = w.SelectMultiple(options=[("A", 1), ("B", 2), ("C", 3)], label=["C", "A"])
    assert (m.value, m.index) == ((3, 1), (2, 0))
    m.index = [1]
    assert m.value == (2,) and m.label == ("B",)
    with pytest.raises(ValueError, match="out of range"):
        m.index = [7]
    with pytest.raises(ValueError, match="isn't one of the options"):
        m.label = ["Z"]
    w.handle(m.model_id, {"index": [0, 9]})
    assert m.value == (1,)
    m.options = ["x"]
    assert m.value == () and w.view(m)["index"] == []


# ---------------------------------------------------------------- buttons, boxes, output


def test_a_button_handler_can_be_removed_and_the_app_clicks_it():
    b = w.Button(description="Go")
    clicks = []

    def h(btn):
        clicks.append(btn.description)

    b.on_click(h)
    w.handle(b.model_id, {"click": True})
    b.on_click(h, remove=True)
    b.click()
    assert clicks == ["Go"]


def test_tabs_and_accordions_keep_titles():
    t = w.Tab([w.Label("a"), w.Label("b")])
    t.set_title(1, "Second")
    assert t.titles == ("", "Second") and t.get_title(1) == "Second" and t.get_title(5) == ""
    a = w.Accordion([w.Label("a")], titles=["Only"], selected_index=0)
    a.set_title(0, None)
    assert a.titles == ("",) and a.selected_index == 0


def test_a_box_that_holds_itself_is_drawn_once():
    box = w.VBox()
    box.children = (box, w.GridBox([w.Label("x")]))
    kids = w.view(box)["children"]
    assert kids[0]["value"] == "(shown above)" and kids[1]["children"][0]["value"] == "x"


def test_an_output_takes_text_written_to_it_and_clears():
    out = w.Output()
    out.append_stdout("a")
    out.append_stderr("b")
    assert out.outputs == [{"type": "stream", "text": "ab"}]
    out.outputs = ()
    assert out.outputs == []


def test_an_output_cuts_text_past_the_cap_and_starts_again_after_clear(monkeypatch):
    from fabric_desktop import notebook
    monkeypatch.setattr(notebook, "STREAM_CHARS", 5)
    out = w.Output()
    out.append_stdout("abcdefgh")
    out.append_stdout("more")
    assert out.outputs[0]["text"] == "abcde" and "Output truncated" in out.outputs[-1]["text"]
    assert len(out.outputs) == 2
    out.clear_output()
    out.append_stdout("xy")
    assert out.outputs == [{"type": "stream", "text": "xy"}]


def test_an_output_cuts_rich_output_past_the_cap_but_always_shows_an_error(monkeypatch):
    from fabric_desktop import notebook
    monkeypatch.setattr(notebook, "RICH_CHARS", 200)
    out = w.Output()
    out._add({"type": "html", "html": "x" * 500})
    out._add({"type": "html", "html": "y"})
    out._add({"type": "error", "ename": "E", "evalue": "boom", "traceback": ""})
    kinds = [o["type"] for o in out.outputs]
    assert kinds == ["text", "error"] and "Output truncated" in out.outputs[0]["text"]


# ---------------------------------------------------------------- interact, links


def test_interact_abbreviations_make_the_documented_controls():
    assert isinstance(w._control("x", "hi"), w.Text)
    neg = w._control("x", -2)
    assert (neg.min, neg.max, neg.value) == (-6, 2, -2)
    zero = w._control("x", 0.0)
    assert (zero.min, zero.max) == (0.0, 1.0)
    fneg = w._control("x", -1.0)
    assert (fneg.min, fneg.max) == (-3.0, 1.0)
    ft = w._control("x", (0.0, 1.0, 0.5))
    assert (ft.value, ft.step) == (0.5, 0.5)
    it = w._control("x", (0, 10, 2))
    assert (it.value, it.step) == (5, 2)
    fx = w.fixed(3)
    assert w._control("x", fx) is fx and w._control("x", object()) is None


def test_interactive_refuses_what_cant_be_a_control_and_shows_a_failing_function():
    with pytest.raises(ValueError, match="can't be turned into a control for x"):
        w.interactive(lambda x: x, None, x=object())

    def f(x=3, y=w.fixed(1)):
        raise RuntimeError(f"bad {x}")

    box = w.interactive(f, None)
    box.update()
    assert box.kwargs == {"x": 3, "y": 1}
    err = box.out.outputs[-1]
    assert err["ename"] == "RuntimeError" and "widgets.py" not in err["traceback"]


def test_interact_manual_runs_only_on_its_button():
    k = Kernel()
    out = k.run("import ipywidgets as w\ncalls = []\n"
                "@w.interact_manual(n=(1, 3))\ndef f(n):\n    calls.append(n)\n")
    assert out["status"] == "ok"
    assert k.run("calls")["outputs"][-1]["text"] == "[]"
    k.run("f.widget.controls['n'].value = 3")
    assert k.run("calls")["outputs"][-1]["text"] == "[]"
    k.run("f.widget.manual_button.click()")
    assert k.run("calls")["outputs"][-1]["text"] == "[3]"
    out = k.run("import ipywidgets as w\n@w.interact.options(manual=True, manual_name='Go')\ndef g(a=1):\n    pass\n"
                "g.widget.manual_button.description")
    assert out["outputs"][-1]["text"] == "'Go'"


def test_interactive_output_shows_an_error_and_reruns_on_a_change():
    s = w.IntSlider(1)
    out = w.interactive_output(lambda n: 1 / (n - 1), {"n": s})
    assert out.outputs[-1]["ename"] == "ZeroDivisionError"
    s.value = 2
    assert not any(o.get("type") == "error" for o in out.outputs)


def test_links_follow_both_ways_one_way_and_stop_on_unlink():
    a, b, c = w.IntSlider(1), w.IntSlider(0), w.IntSlider(0)
    two = w.jslink((a, "value"), (b, "value"))
    one = w.jsdlink((a, "value"), (c, "value"))
    assert repr(two) == "<link IntSlider.value -> IntSlider.value>"
    b.value = 4
    assert (a.value, c.value) == (4, 4)
    c.value = 9
    assert a.value == 4
    two.unlink()
    one.unlink()
    a.value = 7
    assert (b.value, c.value) == (4, 9)


def test_a_link_whose_source_changes_while_it_updates_the_target_is_broken():
    a, b = w.IntSlider(1), w.IntSlider(0)
    w.link((a, "value"), (b, "value"), transform=(lambda v: v + 1, lambda v: v - 1))
    assert b.value == 2
    b.observe(lambda c: setattr(a, "value", 0), names="value")  # as traitlets: the source moved mid-update
    with pytest.raises(w.TraitError, match="Broken link"):
        a.value = 5


def test_the_module_reports_its_ipywidgets_version():
    mod = w.build()
    assert mod.__version__ == "8.1.5" and mod.version_info == (8, 1, 5) and mod.widgets is mod
