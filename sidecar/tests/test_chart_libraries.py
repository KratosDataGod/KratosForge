"""Altair charts and plotly's fig.show() in a cell's output (chart_output.py)."""

from __future__ import annotations

import json

from fabric_desktop import chart_output
from fabric_desktop.notebook import Kernel
from fabric_desktop.notebook_formats import to_ipynb_text

CHART = ("import altair as alt, pandas as pd\n"
         "c = alt.Chart(pd.DataFrame({'a': ['x', 'y'], 'b': [3, 4]})).mark_bar().encode(x='a', y='b')\n")


def test_an_altair_chart_is_its_vega_lite_spec_wherever_it_is_shown():
    k = Kernel()
    for code in ("c", "display(c)", "c.show()"):
        out = k.run(CHART + code)
        assert out["status"] == "ok", (code, out)
        [chart] = out["outputs"]
        assert chart["type"] == "vegalite" and chart["spec"]["mark"]["type"] == "bar", code
        assert chart["spec"]["datasets"], "the data travels in the spec: the frame can't fetch it"
    # Charts drawn by an interact handler land in its Output too.
    out = k.run("import ipywidgets as w\nfrom IPython.display import display\n"
                "w.interact(lambda n: display(c.properties(width=n)), n=(100, 400))")
    assert any(o["type"] == "vegalite" for o in out["outputs"][0]["view"]["children"][-1]["outputs"])


def test_altair_errors_and_limits_say_what_to_do(monkeypatch):
    k = Kernel()
    out = k.run("import altair as alt, pandas as pd\nalt.Chart(pd.DataFrame({'a': range(6000)})).mark_point().encode(x='a')")
    assert out["status"] == "error" and out["outputs"][0]["ename"] == "MaxRowsError"
    monkeypatch.setattr(chart_output, "MAX_SPEC", 100)
    out = k.run(CHART + "c")
    assert out["outputs"][0]["text"].startswith("This Altair chart's data is too large to draw in a cell")


def test_show_from_a_thread_says_so_instead_of_opening_a_browser():
    k = Kernel()
    out = k.run(CHART + "import threading\nerrors = []\n"
                "def go():\n    try:\n        c.show()\n    except RuntimeError as e:\n        errors.append(str(e))\n"
                "t = threading.Thread(target=go)\nt.start()\nt.join()\nprint(errors[0])")
    assert "from the cell's own code" in out["outputs"][-1]["text"]


def test_plotly_fig_show_draws_in_the_cell():
    out = Kernel().run("import plotly.express as px\npx.bar(x=[1, 2], y=[3, 4]).show()\nprint('after')")
    assert out["status"] == "ok", out
    html, after = out["outputs"]
    assert html["type"] == "html" and "Plotly.newPlot" in html["html"] and '<script src="https' not in html["html"]
    assert after == {"type": "stream", "text": "after\n"}
    # A renderer the notebook picks is still used.
    out = Kernel().run("import plotly.io as pio\npio.renderers.default = 'json'\nprint(pio.renderers.default)")
    assert out["outputs"][-1]["text"] == "json\n"


def test_a_notebook_exported_with_outputs_keeps_altair_charts_for_jupyter():
    spec = {"mark": "bar", "data": {"values": [{"a": 1}]}}
    nb = json.loads(to_ipynb_text([{"type": "code", "source": "c", "outputs": [{"type": "vegalite", "spec": spec}]}], True))
    [out] = nb["cells"][0]["outputs"]
    assert out["data"]["application/vnd.vegalite.v6+json"] == spec
