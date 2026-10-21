"""Cell outputs with values JSON can't carry, Data Wrangler's typed-in numbers, and display edge cases."""

from __future__ import annotations

import json

import pyarrow as pa
import pytest

from fabric_desktop import widgets, wrangler
from fabric_desktop.notebook import Kernel
from fabric_desktop.server import _reply_text


def _strict(text: str):
    """Parses as the shell's serde_json and JavaScript do: no NaN or Infinity."""
    def refuse(name):
        raise ValueError(f"{name} isn't JSON")
    return json.loads(text, parse_constant=refuse)


def test_a_reply_holding_nan_or_infinity_still_reaches_the_app():
    k = Kernel()
    widget = k.run("import ipywidgets as w\nw.FloatSlider(value=float('nan'))")["outputs"][0]
    shown = k.run("from IPython.display import JSON, display\ndisplay(JSON({'a': float('inf'), 'b': [1.0, float('-inf')]}))")
    reply = {"id": 7, "result": {"outputs": [widget] + shown["outputs"]}}
    out = _strict(_reply_text(reply))
    assert out["id"] == 7
    assert out["result"]["outputs"][0]["view"]["value"] is None
    assert out["result"]["outputs"][1]["data"] == {"a": None, "b": [1.0, None]}
    # An ordinary reply is written as before.
    assert _reply_text({"id": 1, "result": {"x": 1.5}}) == json.dumps({"id": 1, "result": {"x": 1.5}})


def test_nan_and_infinity_in_a_table_show_as_text():
    k = Kernel()
    out = k.run("import polars as pl\npl.DataFrame({'a': [float('nan'), float('inf'), -float('inf'), 1.5]})")["outputs"][0]
    assert out["rows"] == [["nan"], ["inf"], ["-inf"], [1.5]]
    page = k.frame_page(out["frame"])
    assert [r[0] for r in page["rows"]] == ["nan", "inf", "-inf", 1.5]
    _strict(json.dumps(page))


def test_wrangler_matches_large_whole_numbers_exactly():
    t = pa.table({"id": pa.array([9007199254740993, 9007199254740992], pa.int64())})
    out = wrangler.run(t, None, [{"op": "filter", "column": "id", "test": "eq", "value": " 9007199254740993 "}])
    assert out["error"] is None and out["rows"] == [[9007199254740993]]
    assert "== 9007199254740993" in out["code"]
    # Other ways of writing a whole number still work.
    out = wrangler.run(pa.table({"n": [1000, 5]}), None, [{"op": "filter", "column": "n", "test": "eq", "value": "1e3"}])
    assert out["rows"] == [[1000]]


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "Infinity"])
def test_wrangler_refuses_numbers_that_arent_finite(value):
    t = pa.table({"x": [1.5, 2.5]})
    for step in ({"op": "filter", "column": "x", "test": "lt", "value": value},
                 {"op": "fill", "columns": ["x"], "value": value}):
        out = wrangler.run(t, None, [step])
        assert out["error"]["message"].startswith("Type a number such as 3 or 2.5"), out["error"]


def test_display_html_takes_bytes_and_unfetched_urls_are_named():
    k = Kernel()
    assert k.run("displayHTML(b'<b>caf\\xc3\\xa9</b>')")["outputs"] == [{"type": "html", "html": "<b>café</b>"}]
    out = k.run("from IPython.display import HTML, Markdown, display\n"
                "display(HTML(url='https://example.com/page'))\ndisplay(Markdown(url='https://example.com/r.md'))")
    assert [o["text"] for o in out["outputs"]] == ["HTML: https://example.com/page", "Markdown: https://example.com/r.md"]


def test_link_and_dlink_return_a_link_that_unlinks():
    k = Kernel()
    out = k.run("import ipywidgets as w\na, b, c = w.IntSlider(), w.IntSlider(), w.IntSlider()\n"
                "l = w.link((a, 'value'), (b, 'value'))\nd = w.dlink((a, 'value'), (c, 'value'), transform=lambda v: v * 2)\n"
                "a.value = 4\nprint(b.value, c.value)\nb.value = 6\nprint(a.value, c.value)\n"
                "c.value = 1\nprint(a.value)\n"  # dlink is one way
                "l.unlink()\nd.unlink()\na.value = 9\nprint(b.value, c.value)")
    assert out["status"] == "ok", out
    assert out["outputs"][0]["text"].split("\n")[:-1] == ["4 8", "6 12", "6", "6 1"]


def test_jslink_keeps_two_controls_in_step_in_the_kernel():
    k = Kernel()
    out = k.run("import ipywidgets as w\na, b = w.IntSlider(), w.IntText()\nw.jslink((a, 'value'), (b, 'value'))\n"
                "c = w.IntText()\nw.jsdlink((a, 'value'), (c, 'value'))\nw.HBox([a, b, c])")
    a, b, c = (x["id"] for x in out["outputs"][0]["view"]["children"])
    ev = k.ipywidget_event(a, {"value": 7}, out["outputs"][0]["model"])
    assert [x["value"] for x in ev["view"]["children"]] == [7, 7, 7]
    ev = k.ipywidget_event(c, {"value": 2}, out["outputs"][0]["model"])
    assert [x["value"] for x in ev["view"]["children"]] == [7, 7, 2]


def test_links_stop_instead_of_recursing_and_a_broken_one_says_so():
    k = Kernel()
    # Transforms that aren't each other's inverse: one update each way, as traitlets does, no recursion.
    out = k.run("import ipywidgets as w\na, b = w.IntSlider(), w.IntSlider(max=1000)\n"
                "w.link((a, 'value'), (b, 'value'), transform=(lambda v: v * 2, lambda v: v * 3))\n"
                "a.value = 5\nprint(a.value, b.value)")
    assert out["status"] == "ok", out
    assert out["outputs"][0]["text"] == "5 10\n"
    # Transforms that undo each other keep both sides in step.
    out = k.run("c, d = w.IntSlider(), w.IntSlider(max=1000)\n"
                "w.link((c, 'value'), (d, 'value'), transform=(lambda v: v * 10, lambda v: v // 10))\n"
                "c.value = 4\nprint(d.value)\nd.value = 70\nprint(c.value)")
    assert out["outputs"][0]["text"] == "40\n7\n"
    # Something that changes the source while the link updates the target breaks the link.
    out = k.run("e, f = w.IntSlider(), w.IntSlider()\nw.link((e, 'value'), (f, 'value'))\n"
                "f.observe(lambda ch: setattr(e, 'value', 99), names='value')\ne.value = 3")
    assert out["status"] == "error" and out["outputs"][-1]["ename"] == "TraitError"
    assert out["outputs"][-1]["evalue"].startswith("Broken link")


def test_new_bounds_clamp_the_value_and_min_above_max_is_refused():
    s = widgets.IntSlider(value=50)
    s.max = 10
    assert s.value == 10
    s.max, s.min = 30, 20
    assert s.value == 20
    r = widgets.IntRangeSlider(value=(20, 80))
    r.max = 10
    assert r.value == (10, 10)
    with pytest.raises(widgets.TraitError, match="min > max"):
        widgets.FloatSlider(min=5.0, max=1.0)
    with pytest.raises(widgets.TraitError, match="min > max"):
        s.min = 99
    assert (s.min, s.value) == (20, 20)  # a refused bound changes nothing
    with pytest.raises(widgets.TraitError, match="max < min"):
        s.max = 1
    assert widgets.IntText(value=500).value == 500  # unbounded boxes never clamp


def test_new_options_select_the_first_and_keep_value_label_and_index_together():
    d = widgets.Dropdown(options=[("One", 1), ("Two", 2)], value=2)
    seen = []
    d.observe(lambda c: seen.append(c.new), names="value")
    d.options = ["a", "b"]
    assert (d.value, d.label, d.index) == ("a", "a", 0)
    assert seen == ["a"]
    d.options = []
    assert (d.value, d.label, d.index) == (None, None, None)


def test_select_multiple_takes_index_and_label_like_ipywidgets():
    m = widgets.SelectMultiple(options=[("A", 1), ("B", 2), ("C", 3)], index=(0, 2))
    assert (m.value, m.label, m.index) == ((1, 3), ("A", "C"), (0, 2))
    m.label = ("B",)
    assert m.value == (2,)
    m.options = ["x", "y"]
    assert (m.value, m.index) == ((), ())
    with pytest.raises(ValueError):
        m.index = (5,)


def test_tab_and_accordion_titles():
    t = widgets.Tab(children=[widgets.Label("a"), widgets.Label("b")])
    t.set_title(1, "Second")
    assert t.titles == ("", "Second")
    assert (t.get_title(1), t.get_title(0), t.get_title(9)) == ("Second", "", "")
    a = widgets.Accordion(children=[widgets.Label("a")], titles=("Only",), selected_index=0)
    assert (a.get_title(0), a.selected_index) == ("Only", 0)


def test_wrangler_keeps_decimal_digits_and_reads_times_in_the_columns_zone():
    import datetime as dt
    import decimal

    import polars as pl
    df = pl.DataFrame({
        "price": pl.Series([decimal.Decimal("1.50"), None], dtype=pl.Decimal(10, 2)),
        "at": pl.Series([dt.datetime(2024, 1, 1), None]).dt.replace_time_zone("UTC"),
        "ns": pl.Series([dt.datetime(2024, 1, 1), None], dtype=pl.Datetime("ns")),
        "clock": [dt.time(9), dt.time(13)],
        "took": [dt.timedelta(1), None],
    })
    t = df.to_arrow()
    filled = wrangler.run(t, df, [{"op": "fill", "columns": ["price"], "value": "0.10"}])
    assert filled["error"] is None and filled["types"][0].startswith("decimal")
    assert [r[0] for r in filled["rows"]] == ["1.50", "0.10"]
    later = wrangler.run(t, df, [{"op": "filter", "column": "at", "test": "ge", "value": "2023-12-31"}])
    assert later["error"] is None and later["rows_after"] == 1
    assert wrangler.run(t, df, [{"op": "fill", "columns": ["at"], "value": "2024-02-01"}])["error"] is None
    assert wrangler.run(t, df, [{"op": "fill", "columns": ["ns"], "value": "2024-02-01"}])["error"] is None
    assert wrangler.run(t, df, [{"op": "filter", "column": "clock", "test": "gt", "value": "12:00"}])["rows_after"] == 1
    took = wrangler.run(t, df, [{"op": "filter", "column": "took", "test": "gt", "value": "1"}])
    assert "duration" in took["error"]["message"]


def test_an_interact_function_that_raises_keeps_its_controls_and_shows_the_error_inside():
    k = Kernel()
    out = k.run("import ipywidgets as w\ndef f(x):\n    return 10 / x\nw.interact(f, x=(0, 1))\n"
                "o = w.interactive_output(f, {'x': w.IntSlider(value=0)})\nprint('after')")
    assert out["status"] == "ok"
    box = out["outputs"][0]["view"]
    error = box["children"][-1]["outputs"][0]
    assert error["ename"] == "ZeroDivisionError" and "widgets.py" not in error["traceback"]
    assert out["outputs"][-1]["text"] == "after\n"
    assert k.run("o.outputs[0]['ename']")["outputs"][0]["text"] == "'ZeroDivisionError'"


def test_older_ipython_core_display_imports_and_raw_bundles_work():
    k = Kernel()
    out = k.run("from IPython.core.display import HTML, display\ndisplay(HTML('<b>x</b>'))\n"
                "import IPython.core.display as d\nimport IPython\nprint(d is IPython.display)")
    assert out["outputs"][0] == {"type": "html", "html": "<b>x</b>"}
    assert out["outputs"][1]["text"] == "True\n"
    raw = k.run("display({'text/plain': 'plain'}, {'text/html': '<i>h</i>', 'text/plain': 'h'}, raw=True)")["outputs"]
    assert raw == [{"type": "text", "text": "plain"}, {"type": "html", "html": "<i>h</i>"}]
