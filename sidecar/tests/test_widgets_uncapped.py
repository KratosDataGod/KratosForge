"""ipywidgets stand-in (widgets.py) behaviour that the uncapped mutation run (batch 172,
docs/quality/mutation-testing.md) showed no test checked."""

import pytest

from fabric_desktop import widgets as W


@pytest.fixture(autouse=True)
def no_kernel(monkeypatch):
    monkeypatch.setattr(W.registry, "show", None)
    monkeypatch.setattr(W.registry, "capture", None)


def test_the_registry_keeps_the_last_1000_widgets():
    assert W.MAX_WIDGETS == 1000
    W.registry.reset()
    first = W.Label(value="first")
    second = W.Label(value="second")
    for _ in range(W.MAX_WIDGETS - 2):
        W.Label()
    assert len(W.registry.widgets) == 1000 and W.registry.get(first.model_id) is first
    W.Label()
    assert len(W.registry.widgets) == 1000
    assert W.registry.get(first.model_id) is None and W.registry.get(second.model_id) is second
    W.registry.reset()


def test_a_layout_and_a_style_given_are_kept():
    layout, style = W.Layout(width="10px"), W.Layout(color="red")
    w = W.Button(description="go", layout=layout, style=style)
    assert w.layout is layout and w.style is style
    plain = W.Button()
    assert isinstance(plain.layout, W.Layout) and isinstance(plain.style, W.Layout)


def test_a_select_multiple_keeps_its_other_traits():
    w = W.SelectMultiple(options=["a", "b"], description="pick", disabled=True, tooltip="tip")
    assert w.description == "pick" and w.disabled is True and w.tooltip == "tip"
    assert W.view(w)["description"] == "pick" and W.view(w)["tooltip"] == "tip"


def test_widget_ids_count_from_w1():
    assert W._Registry().add(W.Label()) == "w1"


def test_an_event_without_a_value_leaves_the_value_alone():
    t = W.Text(value="kept")
    seen = []
    t.on_submit(lambda w: seen.append(w.value))
    W.handle(t.model_id, {"submit": True})
    assert t.value == "kept" and seen == ["kept"]
    s = W.IntSlider(value=4)
    W.handle(s.model_id, {})
    assert s.value == 4


def test_setting_an_equal_value_tells_no_observer():
    t = W.Text(value="ab")
    changes = []
    t.observe(changes.append, names="value")
    t.value = "".join(["a", "b"])  # equal, but not the same object
    assert changes == []
    t.value = "abc"
    assert [c["new"] for c in changes] == ["abc"]


def test_number_defaults():
    s = W.IntSlider()
    assert (s.min, s.max, s.step, s.value) == (0, 100, 1, 0)
    assert s.readout is True and s.continuous_update is True and s.orientation == "horizontal"


def test_an_unbounded_number_starts_at_zero_whatever_its_min():
    assert W.IntText(min=7).value == 0
    assert W.FloatText(min=2.5).value == 0.0


def test_range_slider_defaults():
    r = W.IntRangeSlider()
    assert (r.min, r.max, r.step) == (0, 100, 1)
    assert r.value == (25, 75)


def test_a_range_slider_casts_its_numbers_and_keeps_its_other_traits():
    r = W.IntRangeSlider(description="span", tooltip="pick two")
    assert r.description == "span" and r.tooltip == "pick two"
    r.step = "2"
    r.max = "50"
    assert r.step == 2 and r.max == 50 and isinstance(r.max, int)


def test_capture_keeps_what_was_there_unless_asked_to_clear():
    out = W.Output()
    out.append_stdout("before\n")
    kept = out.capture()(lambda: 1)
    assert kept() == 1
    assert out.outputs == [{"type": "stream", "text": "before\n"}]
    cleared = out.capture(clear_output=True)(lambda: 2)
    assert cleared() == 2 and out.outputs == []


def test_update_display_inside_an_output_replaces_the_output_with_that_id():
    out = W.Output()
    out._add({"type": "html", "html": "<b>old</b>", "display_id": "d1"})
    out._add({"type": "html", "html": "<i>other</i>", "display_id": "d2"})
    out._replace("d1", {"type": "html", "html": "<b>new</b>", "display_id": "d1"})
    assert [o["html"] for o in out.outputs] == ["<b>new</b>", "<i>other</i>"]
    out._replace("nope", {"type": "html", "html": "x", "display_id": "nope"})
    assert [o["html"] for o in out.outputs] == ["<b>new</b>", "<i>other</i>"]


def test_interactive_output_shows_a_result_and_not_none(monkeypatch):
    shown = []
    monkeypatch.setattr(W.registry, "show", shown.append)
    s = W.IntSlider(value=3)
    W.interactive_output(lambda x: x * 2 if x != 4 else None, {"x": s})
    assert shown == [6]
    s.value = 4
    assert shown == [6]
    s.value = 5
    assert shown == [6, 10]


def test_a_control_given_for_an_argument_wins_over_its_default():
    def f(x=1, y="a"):
        return None
    box = W.interactive(f, None, x=5)
    assert isinstance(box.controls["x"], W.IntSlider) and box.controls["x"].value == 5
    assert isinstance(box.controls["y"], W.Text) and box.controls["y"].value == "a"


def test_a_password_box_shows_its_placeholder_but_not_its_value():
    p = W.Password(value="s3cret", placeholder="your word")  # pragma: allowlist secret (a fake value)
    v = W.view(p)
    assert v["placeholder"] == "your word" and v["value"] == "" and v["filled"] is True


def test_a_small_float_gets_a_slider_around_it():
    s = W._control("x", 0.5)
    assert (s.min, s.max, s.value) == (-0.5, 1.5, 0.5)
    s = W._control("x", 0.0)
    assert (s.min, s.max) == (0.0, 1.0)


def test_a_float_range_takes_a_hundredth_of_its_span_as_the_step():
    s = W._control("x", (2.0, 12.0))
    assert s.step == pytest.approx(0.1) and (s.min, s.max, s.value) == (2.0, 12.0, 7.0)


def test_ipywidgets_is_a_package_and_its_widgets_module_is_not():
    assert W._finder.find_spec("ipywidgets").submodule_search_locations is not None
    assert W._finder.find_spec("ipywidgets.widgets").submodule_search_locations is None
    assert W._finder.find_spec("numpy") is None


def test_a_widget_shown_in_an_output_is_one_level_below_the_output():
    out = W.Output()
    leaf = W.Label(value="leaf")
    out._add({"type": "widget", "model": leaf.model_id})
    box = out
    for _ in range(W.MAX_DEPTH):  # the Output ends up at depth MAX_DEPTH, the label one below it
        box = W.VBox([box])
    v = W.view(box)
    for _ in range(W.MAX_DEPTH):
        v = v["children"][0]
    assert v["kind"] == "output"
    assert v["outputs"][0]["view"]["value"] == "(shown above)"
    one = W.Output()
    one._add({"type": "widget", "model": leaf.model_id})
    assert W.view(W.VBox([one]))["children"][0]["outputs"][0]["view"]["value"] == "leaf"


def test_a_dropdown_value_is_found_by_equality():
    d = W.Dropdown(options=["ab", "cd"])
    d.value = "".join(["c", "d"])
    assert d.index == 1 and d.label == "cd"


def test_rich_output_past_the_cap_names_the_cap_in_mb():
    from fabric_desktop import notebook
    assert notebook.RICH_CHARS == 16_000_000
    out = W.Output()
    out._add({"type": "html", "html": "x" * (notebook.RICH_CHARS + 1)})
    assert out.outputs[-1]["type"] == "text"
    assert "passed 16 MB, so later ones aren't shown" in out.outputs[-1]["text"]


def test_an_int_of_zero_gets_a_slider_from_0_to_1():
    s = W._control("x", 0)
    assert (s.min, s.max, s.value) == (0, 1, 0)
    s = W._control("x", 4)
    assert (s.min, s.max) == (-4, 12)
    s = W._control("x", -2)
    assert (s.min, s.max) == (-6, 2)
