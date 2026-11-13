"""Cell dependencies read from code (depgraph.py): the Python shapes the main tests don't reach. Augmented
assignment, decorators, classes, filtered and dict comprehensions, ``global``, lakehouse file reads, and a
cell magic part way down a Python cell. Nothing runs: each cell is only parsed."""

from fabric_desktop import depgraph
from tests.test_depgraph import by_n, cells


def test_augmented_assignment_reads_the_name_before_it_rebinds_it():
    a = depgraph.analyze("total += step\nrows[0] += 1")
    assert a["uses"] == ["total", "step", "rows"]
    assert a["defines"] == ["total"]
    g = by_n(depgraph.graph(cells("total = 0\nstep = 2", "total += step")))
    assert [(e["n"], sorted(e["via"])) for e in g[2]["upstream"]] == [(1, ["step", "total"])]


def test_a_decorator_is_read_when_the_function_is_defined():
    a = depgraph.analyze("@cache_for(ttl)\ndef load():\n    return source")
    assert a["defines"] == ["load"]
    assert a["uses"] == ["cache_for", "ttl", "source"]
    assert a["deferred"] == ["source"]  # read only when load() is called


def test_a_class_reads_its_bases_and_keeps_its_body_names_to_itself():
    a = depgraph.analyze("@register\nclass Orders(Base):\n    table = name\n    limit = 10\n    def rows(self):\n        return limit")
    assert a["defines"] == ["Orders"]
    assert "table" not in a["defines"] and "limit" not in a["defines"]
    assert a["uses"][:3] == ["Base", "register", "name"]
    assert "limit" not in a["uses"]  # bound in the class body, so not a dependency on another cell


def test_a_comprehension_filter_and_a_dict_comprehension_read_names_outside_them():
    a = depgraph.analyze("evens = [n for n in nums if n % k == 0]\nlookup = {key: v * rate for key, v in pairs.items()}")
    assert a["uses"] == ["nums", "k", "pairs", "rate"]
    assert a["defines"] == ["evens", "lookup"]
    assert not {"n", "key", "v"} & set(a["defines"] + a["uses"])


def test_a_global_name_counts_as_defined_by_the_cell_once():
    a = depgraph.analyze("global counter\ncounter = 1\nglobal counter")
    assert a["defines"] == ["counter"]
    assert a["uses"] == []


def test_a_lakehouse_file_read_is_a_file_dependency_and_a_local_path_is_not():
    a = depgraph.analyze("df = pd.read_csv('Files/raw/orders.csv')\nother = pd.read_csv('C:/temp/x.csv')")
    assert a["reads"] == [{"kind": "file", "name": "Files/raw/orders.csv", "line": 1}]
    g = by_n(depgraph.graph(cells("df.to_csv('Files/raw/orders.csv')", "df2 = pd.read_parquet('Files/raw/orders.csv')")))
    assert [(e["n"], e["kind"]) for e in g[2]["upstream"]] == [(1, "file")]


def test_a_cell_magic_line_part_way_down_a_python_cell_is_blanked_and_the_rest_still_read():
    a = depgraph.analyze("x = 1\n%%time\ny = x + z")
    assert a["parsed"] is True
    assert a["defines"] == ["x", "y"] and a["uses"] == ["z"]
    assert a["dynamic"] == [] and a["effects"] == []
