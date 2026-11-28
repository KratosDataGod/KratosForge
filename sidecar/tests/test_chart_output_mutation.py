"""Edge cases of chart_output.py, on a separately loaded copy of the module with stand-in libraries."""

from __future__ import annotations

import importlib.util
import sys
import types

import pytest

from fabric_desktop import chart_output


@pytest.fixture
def co(monkeypatch):
    """A fresh copy of chart_output, so install() and its globals don't touch the one the engine uses."""
    spec = importlib.util.spec_from_file_location("chart_output_under_test", chart_output.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    return module


def _hooks(co, monkeypatch, emitted, running=False):
    """Make the copy's cell hooks record outputs: emit says True only when ``emitted`` is a list."""
    def emit(output):
        if emitted is None:
            return False
        emitted.append(output)
        return True
    monkeypatch.setattr(co, "_emit", emit)
    monkeypatch.setattr(co, "_cell_running", lambda: running)


def test_before_install_nothing_is_drawn_and_no_cell_is_running(co):
    assert co._emit({"type": "text", "text": "x"}) is False
    assert co._cell_running() is False


def test_install_puts_its_import_hook_first_and_only_once(co, monkeypatch):
    for name in co._PATCHES:  # libraries already imported would be patched with this copy's hooks
        monkeypatch.delitem(sys.modules, name, raising=False)
    co.install(lambda _o: False, lambda: False)
    assert isinstance(sys.meta_path[0], co._PatchOnImport)
    co.install(lambda _o: False, lambda: False)
    assert sum(isinstance(f, co._PatchOnImport) for f in sys.meta_path) == 1


class _Chart:
    def __init__(self, spec):
        self.spec = spec

    def to_dict(self):
        return self.spec


def test_an_altair_spec_at_the_size_limit_is_drawn_and_one_character_more_is_not(co):
    limit = 20_000_000
    at_limit = {"a": "x" * (limit - len('{"a": ""}'))}
    out = co.altair_output(_Chart(at_limit))
    assert out["type"] == "vegalite" and out["spec"] is at_limit
    over = co.altair_output(_Chart({"a": "x" * (limit + 1 - len('{"a": ""}'))}))
    assert over == {"type": "text", "text": co.ALTAIR_TOO_BIG.format(size=limit + 1)}


def test_a_plotly_dict_with_a_property_plotly_doesnt_know_is_still_drawn(co):
    fig = {"data": [{"type": "bar", "x": [1], "custom_note": 1}], "layout": {}}
    loaded = set(sys.modules)
    try:
        assert co.plotly_output(fig) == {"type": "plotly", "figure": fig}
    finally:  # plotly imported here, before the engine's import hook, would miss its patches later
        for name in set(sys.modules) - loaded:
            del sys.modules[name]


def test_bokeh_models_are_recognised_and_other_objects_are_not(co):
    from bokeh.plotting import figure
    p = figure()
    assert co.is_bokeh(p) is True
    assert co.is_bokeh(42) is False
    look_alike = type("Thing", (), {"__module__": "bokeh.fake", "document": None})()
    assert co.is_bokeh(look_alike) is False
    html = co.bokeh_html(p)
    assert html["type"] == "html" and "<title>Chart</title>" in html["html"]


def test_only_plotly_objects_count_as_plotly_figures(co):
    elsewhere = type("Fig", (), {"to_plotly_json": lambda self: {}})()
    assert co.is_plotly(elsewhere) is False
    assert co.is_plotly(type("Fig", (), {"__module__": "plotly.fake", "to_plotly_json": lambda self: {}})()) is True


def test_draw_outside_any_cell_says_false(co, monkeypatch):
    _hooks(co, monkeypatch, None)
    assert co._draw(lambda: {"type": "text", "text": "x"}, "x") is False


def _bokeh_modules(monkeypatch, original):
    showing = types.SimpleNamespace(show=original)
    io = types.SimpleNamespace(show=original)
    plotting = types.SimpleNamespace(show=lambda *a, **k: "someone else's")
    for name, module in ((chart_output.SHOWING, showing), ("bokeh.io", io), ("bokeh.plotting", plotting)):
        monkeypatch.setitem(sys.modules, name, module)
    return showing, io, plotting


def test_bokeh_show_is_replaced_once_and_only_where_it_is_bokehs_own(co, monkeypatch):
    def original(obj, *args, **kwargs):
        """bokeh's docstring"""
        return ("opened", obj, args, kwargs)
    showing, io, plotting = _bokeh_modules(monkeypatch, original)
    other = plotting.show
    co._patch(showing)
    patched = showing.show
    assert patched is not original and patched._fabric_desktop is True and patched.__doc__ == "bokeh's docstring"
    assert io.show is patched and plotting.show is other
    co._patch(showing)
    assert showing.show is patched


def test_patched_bokeh_show_draws_in_a_cell_refuses_in_a_thread_and_falls_back_elsewhere(co, monkeypatch):
    calls = []

    def original(obj, *args, **kwargs):
        calls.append((obj, args, kwargs))
        return "opened"
    showing, _io, _plotting = _bokeh_modules(monkeypatch, original)
    monkeypatch.setattr(co, "bokeh_html", lambda obj: {"type": "html", "html": f"<p>{obj}</p>"})
    co._patch(showing)
    emitted = []
    _hooks(co, monkeypatch, emitted)
    assert showing.show("plot") is None and emitted == [{"type": "html", "html": "<p>plot</p>"}] and calls == []
    _hooks(co, monkeypatch, None, running=True)
    with pytest.raises(RuntimeError, match="from the cell's own code"):
        showing.show("plot")
    assert calls == []
    _hooks(co, monkeypatch, None)
    assert showing.show("plot", 1, browser="x") == "opened"
    assert calls == [("plot", (1,), {"browser": "x"})]


def test_altair_show_is_patched_once_and_falls_back_outside_a_cell(co, monkeypatch):
    class Mixin:
        def show(self, *args, **kwargs):
            """altair's docstring"""
            return ("served", args, kwargs)

        def to_dict(self):
            return {"mark": "bar"}
    altair = types.SimpleNamespace(TopLevelMixin=Mixin)
    co._patch_altair(altair)
    patched = Mixin.show
    assert patched._fabric_desktop is True and patched.__doc__ == "altair's docstring"
    co._patch_altair(altair)
    assert Mixin.show is patched
    _hooks(co, monkeypatch, None)
    assert Mixin().show(1, port=2) == ("served", (1,), {"port": 2})
    emitted = []
    _hooks(co, monkeypatch, emitted)
    assert Mixin().show() is None and emitted == [{"type": "vegalite", "spec": {"mark": "bar"}}]
    co._patch_altair(types.SimpleNamespace())  # an altair without the mixin is left alone


def test_plotlys_browser_stand_in_opens_nothing(co):
    assert co._NoBrowser.open("https://example.invalid") is False


def test_plotly_offline_plot_is_patched_once(co, tmp_path):
    def plot(figure_or_data, output_type="file", filename="temp-plot.html", auto_open=True):
        """plotly's docstring"""
        return "<div></div>"
    offline = types.SimpleNamespace(plot=plot)
    co._patch_plotly_offline(offline)
    patched = offline.plot
    assert patched._fabric_desktop is True and patched.__doc__ == "plotly's docstring"
    co._patch_plotly_offline(offline)
    assert offline.plot is patched
    assert patched({}, output_type="div") == "<div></div>"


def test_the_loader_wrapper_hands_back_the_module_its_loader_creates(co):
    made = types.ModuleType("made")
    loader = types.SimpleNamespace(create_module=lambda spec: made)
    assert co._PatchAfter(loader).create_module(None) is made
