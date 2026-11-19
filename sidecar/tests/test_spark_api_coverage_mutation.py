"""Kill tests for spark_api_coverage.py (docs/quality/mutation-testing.md, batch 111).

The tests that compare the docs page are left out of mutation runs (they read docs/), so these check
what makes the page: what counts as a refusal, one example run as a cell (on a stand-in kernel), a
method's status and note, rows compared in any order, short sources, the page itself, where it is
written, and that importing the module writes nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fabric_desktop import spark_api_coverage as sac

VALUE = sac.VALUE


class Kernel:
    """A notebook kernel stand-in: each source gives a status, outputs and (for an expression) a value."""

    def __init__(self, answers):
        self.answers = answers
        self.namespace = {}
        self.codes = []

    def run(self, code):
        self.codes.append(code)
        for source, (status, outputs, *value) in self.answers.items():
            if source in code:
                if value and code.startswith(f"{VALUE} = "):
                    self.namespace[VALUE] = value[0]
                return {"status": status, "outputs": outputs}
        raise AssertionError(f"unexpected cell {code!r}")


# ---- what counts as a refusal

def test_each_refusal_phrase_counts_on_its_own_in_any_case():
    assert sac._refusal("NotImplementedError: no")
    assert sac._refusal("ValueError: not on This PC")
    assert sac._refusal("ValueError: it Runs in Fabric only")
    assert not sac._refusal("ValueError: bad input")


# ---- one example as a cell

def test_an_expression_is_kept_as_the_value_and_text_and_stream_are_printed():
    k = Kernel({"df.count()": ("ok", [{"type": "text", "text": "a\n"}, {"type": "stream", "text": "b"},
                                      {"type": "display", "text": "not printed"}], 3)})
    k.namespace[VALUE] = "left over"
    got, error = sac._run(k, "df.count()")
    assert k.codes == [f"{VALUE} = (df.count())"]
    assert got == {"printed": "a\nb", "value": 3} and error == ""
    assert VALUE not in k.namespace


def test_a_statement_runs_as_it_is_and_has_no_value():
    k = Kernel({"x = 1": ("ok", [], 5)})
    k.namespace[VALUE] = 9
    assert sac._run(k, "x = 1") == ({}, "")
    assert k.codes == ["x = 1"]


def test_source_python_cannot_read_runs_as_it_is():
    k = Kernel({"df.select(": ("error", [{"type": "error", "ename": "SyntaxError", "evalue": "never closed"}])})
    assert sac._run(k, "df.select(") == (None, "SyntaxError: never closed")
    assert k.codes == ["df.select("]


def test_an_error_is_its_name_and_first_line_cut_to_160():
    long = "x" * 200
    k = Kernel({"a()": ("error", [{"type": "text", "text": "p"},
                                  {"type": "error", "ename": "ValueError", "evalue": long + "\nmore"}])})
    got, error = sac._run(k, "a()")
    assert got is None and error == ("ValueError: " + long)[:160]


def test_an_error_without_its_text_is_the_status():
    k = Kernel({"a()": ("timeout", [{"type": "text", "text": "p"}])})
    assert sac._run(k, "a()") == (None, "timeout")


class _Broken:
    def collect(self):
        raise RuntimeError("cannot collect\nsecond line")

    columns = ["a"]

    def select(self):
        ...


def test_a_value_this_pc_cannot_collect_is_its_error_first_line():
    k = Kernel({"df": ("ok", [], _Broken())})
    assert sac._run(k, "df") == (None, "RuntimeError: cannot collect")


def test_a_long_collect_error_is_cut_to_160():
    class Long(_Broken):
        def collect(self):
            raise RuntimeError("y" * 300)

    got, error = sac._run(Kernel({"df": ("ok", [], Long())}), "df")
    assert got is None and error == ("RuntimeError: " + "y" * 300)[:160]


def test_only_one_expression_is_an_expression():
    assert sac._expression("df.count()")
    assert not sac._expression("df.count()\ndf.show()")
    assert not sac._expression("x = 1")
    assert not sac._expression("")


# ---- a method's status

def _method(*examples, **over):
    return {"class": "DataFrame", "name": "m", "examples": list(examples), **over}


def test_an_example_spark_stops_on_must_stop_here_too():
    stops = {"source": "df.bad()"}
    k = Kernel({"df.bad()": ("ok", [], 1)})
    assert sac.check(_method(stops), k) == ("differs", "`df.bad()` runs here; Spark stops on it")
    k = Kernel({"df.bad()": ("error", [{"type": "error", "ename": "E", "evalue": "x"}]),
                "df.count()": ("ok", [], 3)})
    assert sac.check(_method(stops, {"source": "df.count()", "outcome": {"value": 3}}), k) == ("runs", "")


def test_a_stop_is_refused_or_fails_with_the_source_and_error():
    def k(evalue):
        return Kernel({"df.x()": ("error", [{"type": "error", "ename": "E", "evalue": evalue}])})

    ex = {"source": "df.x()", "outcome": {"value": 1}}
    assert sac.check(_method(ex), k("runs in Fabric")) == ("refused", "`df.x()`: E: runs in Fabric")
    assert sac.check(_method(ex), k("boom")) == ("fails", "`df.x()`: E: boom")


TABLE_A = "+---+\n|  x|\n+---+\n|  b|\n|  a|\n+---+"
TABLE_B = "+---+\n|  x|\n+---+\n|  a|\n|  b|\n+---+"


def test_rows_without_a_set_order_compare_in_any_order_and_ordered_ones_do_not():
    k = Kernel({"df.show()": ("ok", [{"type": "text", "text": TABLE_A}]),
                "df.orderBy('x').show()": ("ok", [{"type": "text", "text": TABLE_A}])})
    assert sac.check(_method({"source": "df.show()", "outcome": {"printed": TABLE_B}}), k) == ("runs", "")
    status, note = sac.check(_method({"source": "df.orderBy('x').show()", "outcome": {"printed": TABLE_B}}), k)
    assert status == "differs"


def test_a_different_value_says_both_cut_to_70():
    k = Kernel({"df.v()": ("ok", [], "a" * 100)})
    status, note = sac.check(_method({"source": "df.v()", "outcome": {"value": "b" * 100}}), k)
    assert status == "differs"
    here, there = json.dumps({"value": "a" * 100})[:70], json.dumps({"value": "b" * 100})[:70]
    assert note == f"`df.v()`: {here} here, {there} in Spark"


# ---- rows in any order

def test_a_result_without_printed_text_is_kept():
    assert sac._any_order({"value": 1}) == {"value": 1}


def test_each_table_s_rows_are_sorted_and_its_header_stays():
    text = "-\n" + TABLE_A + "\n" + "+---+\n|  y|\n+---+\n|  d|\n|  c|\n+---+"
    got = sac._any_order({"printed": text, "value": 1})
    assert got == {"printed": "-\n" + TABLE_B + "\n" + "+---+\n|  y|\n+---+\n|  c|\n|  d|\n+---+", "value": 1}


def test_a_stray_border_after_the_tables_is_left_alone():
    text = TABLE_A + "\n+--+\n+--+"
    assert sac._any_order({"printed": text}) == {"printed": TABLE_B + "\n+--+\n+--+"}


# ---- short sources

def test_a_source_is_one_line_and_cut_after_60():
    assert sac._short("df.select(\n   'a')") == "df.select( 'a')"
    assert sac._short("x" * 60) == "x" * 60
    assert sac._short("x" * 61) == "x" * 57 + "..."


# ---- the page

def test_the_page_counts_each_status_and_lists_each_class_that_has_methods():
    rows = [{"class": "DataFrame", "name": "select", "status": "runs", "note": ""},
            {"class": "DataFrame", "name": "join", "status": "differs", "note": "a | b\nc"},
            {"class": "Column", "name": "alias", "status": "runs", "note": ""}]
    page = sac.markdown(rows)
    lines = page.split("\n")
    assert lines[0] == "# PySpark DataFrame API on this PC"
    assert "| Runs | 2 |" in lines and "| Differs | 1 |" in lines and "| Refused | 0 |" in lines
    assert "| Not checked | 0 |" in lines and "| **All** | **3** |" in lines
    assert "## DataFrame" in lines and "## Column" in lines and "## GroupedData" not in lines
    assert "| `select` | Runs |  |" in lines
    assert "| `join` | Differs | a \\| b c |" in lines
    i = lines.index("## Column")
    assert lines[i:i + 6] == ["## Column", "", "| Method | Status | Note |", "| --- | --- | --- |",
                              "| `alias` | Runs |  |", ""]
    assert page.endswith("| `alias` | Runs |  |\n")


def test_the_page_is_written_to_the_repository_s_docs():
    assert sac.DOC == Path(__file__).resolve().parents[2] / "docs" / "spark-dataframe-api.md"


def test_importing_the_module_writes_nothing():
    sidecar = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-c", "import fabric_desktop.spark_api_coverage"], cwd=sidecar,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout == ""


def test_a_short_error_of_many_lines_is_its_first_line():
    k = Kernel({"a()": ("error", [{"type": "error", "ename": "ValueError", "evalue": "first\nsecond"}])})
    assert sac._run(k, "a()") == (None, "ValueError: first")
