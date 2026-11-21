"""Boundary kill test for depgraph (mutation testing, batch 167)."""
from __future__ import annotations

from fabric_desktop.depgraph import analyze


def test_open_with_only_a_path_reads_and_is_no_effect():
    out = analyze('text = open("/lakehouse/default/Files/a.txt").read()\n')
    assert not any("writes the file" in e["text"] for e in out["effects"])
    out = analyze('open("/lakehouse/default/Files/a.txt", "w").write("x")\n')
    assert any("writes the file" in e["text"] for e in out["effects"])


def test_a_cell_as_deep_as_the_limit_is_read_and_a_deeper_one_is_reported_not_raised():
    """A long sum is a syntax tree as deep as it has terms. Up to the limit the cell is read as before;
    deeper (thousands of terms) it is reported as not read, naming the limit, rather than raising
    RecursionError from the visitor."""
    from fabric_desktop import depgraph
    near = "import os\ny = 1\nx = " + " + ".join(["y"] * (depgraph.MAX_DEPTH - 4)) + "\n"
    out = depgraph.analyze(near)
    assert out["parsed"] and out["defines"] == ["os", "y", "x"] and out["uses"] == []
    deep = "import os\ny = 1\nx = " + " + ".join(["y"] * 5000) + "\n"
    out = depgraph.analyze(deep)
    assert not out["parsed"] and out["defines"] == [] and out["dynamic"] == [
        {"line": 1, "text": f"is nested more than {depgraph.MAX_DEPTH} levels deep, so it isn't read"}]
    assert depgraph.graph([{"type": "code", "source": deep}, {"type": "code", "source": "print(x)"}])
