"""pandas is in the engine, as in Fabric and Databricks notebooks: import pandas, toPandas(),
pandas data frames shown as tables, and times with a time zone handed back by DuckDB."""

from fabric_desktop.notebook import Kernel


def test_pandas_works_in_a_notebook_cell():
    k = Kernel()
    out = k.run("import pandas as pd\n"
                "df = spark.createDataFrame([(1, 'EU'), (2, 'US')], ['id', 'region'])\n"
                "pdf = df.toPandas()\n"
                "print(type(pdf).__name__, len(pdf), pd.__version__.split('.')[0])\n"
                "pdf")
    assert out["status"] == "ok", out
    assert out["outputs"][0]["text"].split() == ["DataFrame", "2", "2"]
    assert out["outputs"][1]["type"] == "table"


def test_a_time_with_a_zone_comes_back_from_spark():
    out = Kernel().run("spark.sql(\"SELECT TIMESTAMPTZ '2026-10-20 13:00:00+00' AS t\").collect()[0]['t'].year")
    assert out["status"] == "ok", out
    assert out["outputs"] == [{"type": "text", "text": "2026"}]


def test_the_chart_libraries_fabric_notebooks_use_are_there():
    out = Kernel().run(
        "import pandas as pd, plotly.express as px, seaborn as sns, altair as alt\n"
        "from bokeh.plotting import figure\n"
        "pdf = pd.DataFrame({'x': [1, 2, 3], 'y': [3, 1, 2]})\n"
        "fig = px.bar(pdf, x='x', y='y')\n"
        "chart = alt.Chart(pdf).mark_line().encode(x='x', y='y')\n"
        "p = figure(); p.line(pdf.x, pdf.y)\n"
        "ax = sns.lineplot(data=pdf, x='x', y='y')\n"
        "print(type(fig).__name__, chart.to_dict()['mark']['type'], type(p).__name__, type(ax).__name__)")
    assert out["status"] == "ok", out
    assert out["outputs"][0]["text"].split() == ["Figure", "line", "figure", "Axes"]


def test_bokeh_draws_in_the_cell_without_a_file_or_a_browser(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: opened.append(a))
    monkeypatch.setattr("webbrowser.get", lambda *a, **k: opened.append(a))
    k = Kernel()
    out = k.run("from bokeh.plotting import figure, show\n"
                "p = figure(title='Sales'); p.line([1, 2, 3], [3, 1, 2])\n"
                "show(p)\n"
                "print('after')")
    assert out["status"] == "ok", out
    html, text = out["outputs"]
    assert html["type"] == "html" and "Sales" in html["html"] and "Bokeh" in html["html"]
    assert "cdn.bokeh.org" not in html["html"]  # its JavaScript is inline: nothing is fetched
    assert text["text"] == "after\n"
    assert opened == [] and list(tmp_path.iterdir()) == []
    # As a cell's last value too, and through bokeh.io.
    out = k.run("import bokeh.io\nbokeh.io.show(p)\np")
    assert [o["type"] for o in out["outputs"]] == ["html", "html"]


def test_an_altair_chart_says_how_to_see_it():
    out = Kernel().run("import altair as alt, pandas as pd\n"
                       "alt.Chart(pd.DataFrame({'x': [1, 2]})).mark_line().encode(x='x')")
    assert out["status"] == "ok", out
    assert out["outputs"][0]["text"].startswith("Altair charts don't draw in a cell on this PC yet.")
