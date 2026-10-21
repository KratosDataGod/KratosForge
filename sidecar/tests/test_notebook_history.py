"""IPython's history in a notebook: In, Out, _/__/___, _N, _i/_ii/_iii, _iN and %history (history.py)."""

from fabric_desktop import history
from fabric_desktop.notebook import Kernel


def _text(out):
    return "".join(o.get("text", "") for o in out["outputs"])


def test_in_and_out_hold_each_cells_source_and_result():
    k = Kernel()
    k.run("1 + 1")
    k.run("x = 5")
    k.run("x * 10")
    assert _text(k.run("In[1], In[2]")) == "('1 + 1', 'x = 5')"
    assert _text(k.run("Out")) .startswith("{1: 2, 3: 50")
    assert _text(k.run("_1 + _3")) == "52"
    assert _text(k.run("_i4")) == "'In[1], In[2]'"


def test_underscores_hold_the_last_three_results():
    k = Kernel()
    for code in ("'a'", "'b'", "'c'"):
        k.run(code)
    k.run("x = 1")  # no result: the underscores don't move
    assert _text(k.run("(_, __, ___)")) == "('c', 'b', 'a')"


def test_i_is_the_cell_before_this_one():
    k = Kernel()
    k.run("first = 1")
    k.run("second = 2")
    assert _text(k.run("(_i, _ii, _iii)")) == "('second = 2', 'first = 1', '')"


def test_history_magic_lists_earlier_cells():
    k = Kernel()
    k.run("a = 1")
    k.run("a + 1\n")
    k.run("for i in range(2):\n    pass")
    assert _text(k.run("%history")).splitlines() == ["a = 1", "a + 1", "for i in range(2):", "    pass"]
    assert _text(k.run("%history -n 1-2")).splitlines() == ["  1: a = 1", "  2: a + 1"]
    assert _text(k.run("%history -o -l 1 2")).splitlines() == ["  2: a + 1", "  2> 2"]
    out = k.run("%history 3-1")
    assert out["status"] == "error" and "backwards" in out["outputs"][-1]["evalue"]
    out = k.run("%history -g")
    assert out["status"] == "error" and "isn't supported" in out["outputs"][-1]["evalue"]


def test_reset_clears_results_but_keeps_sources():
    k = Kernel()
    k.run("40 + 2")
    k.run("%reset -f")
    assert _text(k.run("(Out, '_1' in globals(), In[1])")) == "({}, False, '40 + 2')"


def test_restart_starts_history_again():
    k = Kernel()
    k.run("40 + 2")
    k.restart()
    assert _text(k.run("(len(In), Out)")) == "(2, {})"


def test_a_notebook_run_with_percent_run_isnt_in_this_notebooks_history():
    k = Kernel(load_notebook=lambda name: [{"type": "code", "source": "helper = 7\nhelper * 6"}])
    k.run("%run Helpers")
    assert _text(k.run("(Out, len(In))")) == "({}, 3)"


def test_out_keeps_only_the_latest_results(monkeypatch):
    monkeypatch.setattr(history, "OUT_KEEP", 2)
    ns = {"In": [""], "Out": {}}
    for n in (1, 2, 3):
        history.record_output(ns, n, n * 10)
    assert ns["Out"] == {2: 20, 3: 30} and "_1" not in ns and ns["_3"] == 30


def test_history_and_variables_pane_dont_mix():
    k = Kernel()
    k.run("x = 1")
    k.run("x")
    assert [v["name"] for v in k.variables()] == ["x"]
