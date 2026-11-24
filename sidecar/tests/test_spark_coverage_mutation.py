"""spark_coverage.py: what mutation testing showed no test checked. A nested Row stays a struct, long notes
are cut to their lengths, coverage() fills a missing group and a probe's example, the counts and each group's
rows in the page, and where the page is written."""

from __future__ import annotations

from pathlib import Path

from fabric_desktop import notebook, spark_coverage as sc


class Row(tuple):
    """A pyspark Row: asDict(recursive=True) turns nested rows into dicts too."""

    def __new__(cls, **fields):
        row = super().__new__(cls, fields.values())
        row.fields = fields
        return row

    def asDict(self, recursive=False):
        return {k: (v.asDict(True) if recursive and isinstance(v, Row) else v) for k, v in self.fields.items()}


class Spark:
    def __init__(self, rows=None, error=None):
        self.rows, self.error = rows or [], error

    def sql(self, query):
        if self.error:
            raise self.error
        return self

    def collect(self):
        return self.rows


def test_a_nested_row_stays_a_struct():
    assert sc.canonical(Row(a=Row(x=1))) == {"struct": [["a", {"struct": [["x", 1]]}]]}


def test_a_long_difference_and_a_long_error_are_cut():
    got = [("x" * 200,)]
    status, note = sc.check({"name": "f", "example": "SELECT 1", "value": [["y" * 200]]}, Spark(got))
    assert status == "differs"
    here, spark = note.split(" here, ")
    assert len(here) == 80 and spark == '[["' + "y" * 77 + " in Spark"
    status, note = sc.check({"name": "f", "example": "SELECT 1", "value": [[1]]}, Spark(error=RuntimeError("e" * 300)))
    assert status == "fails" and note == "e" * 160


def test_coverage_fills_a_missing_group_and_a_probes_example(monkeypatch):
    class Kernel:
        namespace = {"spark": Spark([("ADD", False)])}
    monkeypatch.setattr(notebook, "Kernel", Kernel)
    monkeypatch.setattr(sc, "load", lambda: [{"name": "sql_keywords"},
                                             {"name": "abs", "group": "math_funcs", "example": "SELECT abs(-1)",
                                              "value": [["ADD", False]]}])
    rows = sc.coverage()
    assert rows == [
        {"name": "sql_keywords", "group": "other", "example": sc.PROBES["sql_keywords"], "status": "not checked",
         "note": ""},
        {"name": "abs", "group": "math_funcs", "example": "SELECT abs(-1)", "status": "runs", "note": ""},
    ]


def test_the_page_counts_each_status_and_lists_each_groups_own_rows():
    rows = [{"name": "abs", "group": "math_funcs", "example": "SELECT 1", "status": "runs", "note": ""},
            {"name": "ceil", "group": "math_funcs", "example": "", "status": "runs", "note": ""},
            {"name": "upper", "group": "string_funcs", "example": "", "status": "differs", "note": "n"}]
    text = sc.markdown(rows)
    for line in ("| Runs | 2 |", "| Differs | 1 |", "| Refused | 0 |", "| Fails | 0 |", "| Not checked | 0 |",
                 "| **All** | **3** |"):
        assert line in text
    math = text.split("## Math\n", 1)[1].split("## ", 1)[0]
    assert "`abs`" in math and "`ceil`" in math and "`upper`" not in math
    string = text.split("## String\n", 1)[1]
    assert "`upper`" in string and "`abs`" not in string


def test_the_page_is_written_to_the_repositorys_docs_folder():
    assert sc.DOC.name == "spark-functions.md" and sc.DOC.parent.name == "docs"
    assert (sc.DOC.parent.parent / "sidecar" / "fabric_desktop" / "spark_coverage.py").resolve() == \
        Path(sc.__file__).resolve()
