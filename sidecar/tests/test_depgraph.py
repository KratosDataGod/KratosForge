"""Cell dependencies read from code (depgraph.py)."""

from fabric_desktop import depgraph


def cells(*sources, meta=None):
    meta = meta or {}
    return [{"type": "markdown" if s.startswith("#md") else "code", "source": s, "cell_id": f"c{i}",
             "meta": meta.get(i, {})} for i, s in enumerate(sources, 1)]


def by_n(g):
    return {c["n"]: c for c in g["cells"]}


def test_variables_flow_from_the_last_cell_that_defined_them():
    g = by_n(depgraph.graph(cells("import polars as pl\nx = 1", "x = x + 1", "y = pl.DataFrame({'a': [x]})")))
    assert g[1]["defines"] == ["pl", "x"]
    assert [(e["n"], e["via"]) for e in g[2]["upstream"]] == [(1, ["x"])]
    assert [(e["n"], sorted(e["via"])) for e in g[3]["upstream"]] == [(1, ["pl"]), (2, ["x"])]
    assert g[1]["downstream"] == [2, 3]


def test_function_and_comprehension_locals_are_not_dependencies():
    a = depgraph.analyze("def f(a, b=k):\n    c = a + b\n    return c\nz = [i * 2 for i in rows]\nlam = lambda q: q + w")
    assert sorted(a["uses"]) == ["k", "rows", "w"]
    assert "c" not in a["defines"] and "i" not in a["defines"]


def test_tables_written_by_one_cell_and_read_by_another():
    g = by_n(depgraph.graph(cells(
        "lakehouse.write_table(df, 'silver.daily')",
        "%%sql\nSELECT region FROM silver.daily JOIN Orders o ON 1=1",
        "spark.read.table('silver.daily')")))
    assert g[1]["writes"] == [{"kind": "table", "name": "silver.daily", "line": 1}]
    assert [e["kind"] for e in g[2]["upstream"]] == ["table"]
    assert {r["name"] for r in g[2]["reads"]} == {"silver.daily", "dbo.orders"}
    assert g[3]["upstream"][0]["n"] == 1
    assert any("writes the table silver.daily" in e["text"] for e in g[1]["effects"])


def test_sql_writes_are_side_effects_not_reads():
    a = depgraph.analyze("%%sql\nINSERT INTO gold.t SELECT * FROM silver.s")
    assert [w["name"] for w in a["writes"]] == ["gold.t"]
    assert [r["name"] for r in a["reads"]] == ["silver.s"]


def test_side_effects_are_found():
    a = depgraph.analyze("%pip install x\nrequests.post(url)\nnotebookutils.fs.rm('Files/a')\ndf.write_csv('out.csv')\n"
                         "open('f.txt', 'w')")
    texts = " | ".join(e["text"] for e in a["effects"])
    for part in ("installs packages", "requests.post", "notebookutils.fs.rm", "writes the file out.csv", "writes the file f.txt"):
        assert part in texts


def test_what_reading_cannot_follow_is_flagged():
    for src, word in (("exec(code)", "exec"), ("from m import *", "import *"), ("%run other", "%run"),
                      ("mssparkutils.notebook.run('x')", "another notebook")):
        assert any(word in d["text"] for d in depgraph.analyze(src)["dynamic"]), src
    bad = depgraph.analyze("x = (")
    assert bad["parsed"] is False and bad["dynamic"]


def test_unresolved_names_say_why():
    g = by_n(depgraph.graph(cells("print(later)", "later = 1", "print(nowhere)")))
    assert "later cell (2)" in g[1]["unresolved"][0]["text"]
    assert "isn't defined by any cell" in g[3]["unresolved"][0]["text"]


def test_declared_dependencies_are_kept_apart_from_detected_ones():
    g = by_n(depgraph.graph(cells("a = 1", "#md notes", "b = 2", meta={3: {"depends_on": ["c1", "c9", "c3"]}})))
    assert 2 not in g  # markdown isn't analyzed
    assert [(e["n"], e["kind"], e["source"]) for e in g[3]["upstream"]] == [(1, "declared", "declared")]


def test_builtins_and_notebook_globals_are_not_dependencies():
    assert depgraph.analyze("display(spark.range(3)); print(len([1]))")["uses"] == []
