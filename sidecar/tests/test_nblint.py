"""nblint: the lint warnings the notebook editor underlines (parity N09)."""

from fabric_desktop import nblint, notebook_practices
from fabric_desktop.service import Service


def _undefined(cells, cell):
    return [(p["message"].split(" ", 1)[0], p["line"], p["col"], p["end_col"])
            for p in nblint.lint(cells[cell], cells, cell) if p["rule_id"] == "undefined-name"]


def test_a_name_no_cell_defines_is_a_warning_at_its_exact_place():
    cells = ["x = 1", "naïve = 'é'; print(naïve, x, totl)\nif x:\n    y = totl + 1"]
    problems = nblint.lint(cells[1], cells, 1)
    assert [(p["line"], p["col"], p["end_col"]) for p in problems] == [(1, 29, 33), (3, 8, 12)]
    assert {p["severity"] for p in problems} == {"warning"}
    assert problems[0]["message"].startswith("totl isn't defined: no cell of this notebook sets it")


def test_names_from_any_cell_builtins_and_the_kernel_are_defined():
    cells = [
        "import pandas as pd\nfrom os import path as p\ndef f(a, *rest, **kw):\n    return a\nclass K: pass",
        "for i in range(3):\n    j = i\nwith open('f') as fh: pass\ntry:\n    pass\nexcept ValueError as err:\n    pass",
        "print(later)",  # bound by the next cell: cells run in any order
        "later = [k for k in range(2)]\nif (w := 1): pass\nmatch w:\n    case {'a': aa, **more}: pass\n    case [first, *others]: pass",
        ("print(pd, p, f, K, i, j, fh, err, later, k, w, aa, more, first, others, len, Exception, __name__)\n"
        "spark.sql('x'); display(df := spark.range(1)); notebookutils.fs.ls('/'); mssparkutils; dbutils; sc; displayHTML\n"
        "_sqldf; _private"),
    ]
    for n in range(len(cells)):
        assert _undefined(cells, n) == [], n


def test_magics_and_other_languages_bind_names_and_are_never_flagged():
    cells = [
        "%%sql -o fromsql\nSELECT * FROM t WHERE nothing_here = 1",
        "%%capture captured\nprint(1)",
        "%pip install polars==1.0\n!ls\nrows = %sql SELECT 1\nfiles = !ls",
        "%%configure\n{\"conf\": {\"k\": \"v\"}}",
        "%%pyspark\nprint(fromsql, captured, rows, files, spark)",
        "print(fromsql, captured, rows, files)\n%time print(rows)",
    ]
    for n in range(len(cells)):
        assert _undefined(cells, n) == [], n


def test_a_pyspark_cell_counts_lines_from_the_top_of_the_cell():
    cells = ["%%pyspark\nprint(missing)"]
    assert _undefined(cells, 0) == [("missing", 2, 6, 13)]


def test_a_notebook_that_can_define_names_another_way_gets_no_undefined_name_warnings():
    for other in ["%run helpers", "from helpers import *", "exec('z = 1')", "globals()['z'] = 1",
                  "import builtins\nbuiltins.z = 1", "%store -r z"]:
        cells = [other, "print(z)"]
        assert _undefined(cells, 1) == [], other


def test_a_cell_that_does_not_parse_counts_every_word_as_defined():
    cells = ["def broken(:\n    zz = 1", "print(zz)"]
    assert _undefined(cells, 1) == []


def test_annotations_and_name_error_probes_are_not_flagged():
    cells = [("def f(a: Missing) -> Other:\n    v: Lazy = 1\n    return a\n"
             "try:\n    maybe_there\nexcept NameError:\n    pass\n"
             "try:\n    also_maybe\nexcept (ImportError, NameError):\n    pass")]
    assert _undefined(cells, 0) == []


def test_best_practice_findings_carry_positions_and_are_warnings_or_info():
    cell = "df = spark.read.table('t')\nrows = df.collect()\nfor r in rows:\n    df.write.saveAsTable('x')\n%pip install requests"
    found = notebook_practices.check([cell])["findings"]
    by_rule = {f["rule_id"]: f for f in found}
    assert {k: by_rule["NB003"][k] for k in ("line", "col", "end_line", "end_col")} == \
        {"line": 2, "col": 7, "end_line": 2, "end_col": 19}
    assert (by_rule["NB009"]["line"], by_rule["NB009"]["col"], by_rule["NB009"]["end_col"]) == (5, 0, 21)
    problems = {p["rule_id"]: p for p in nblint.lint(cell, [cell], 0)}
    assert problems["NB003"]["severity"] == "warning" and problems["NB003"]["col"] == 7
    assert problems["NB003"]["message"].startswith("NB003 collect() or toPandas() without a limit.")
    assert problems["NB010"]["line"] == 4


def test_practice_underlines_follow_the_panel_settings():
    cell = "rows = df.collect()"
    assert any(p["rule_id"] == "NB003" for p in nblint.lint(cell, ["df = 1", cell], 1))
    off = {"NB003": {"enabled": False}}
    assert not any(p["rule_id"] == "NB003" for p in nblint.lint(cell, ["df = 1", cell], 1, off))
    info = {"NB003": {"severity": "Info"}}
    assert [p["severity"] for p in nblint.lint(cell, ["df = 1", cell], 1, info) if p["rule_id"] == "NB003"] == ["info"]


def test_a_big_cell_gets_only_the_syntax_check():
    cell = "print(nope)\n" + "x = 1\n" * (nblint.MAX_CELL // 6 + 1)
    assert nblint.lint(cell, [cell], 0) == []


def test_notebook_syntax_keeps_its_old_answer_and_adds_lint_only_when_given_the_cells():
    service = Service.__new__(Service)
    assert service.notebook_syntax("print(nope)") == []
    lint = service.notebook_syntax("print(nope)", cells=["print(nope)"], cell=0)
    assert [p["rule_id"] for p in lint] == ["undefined-name"]
    broken = service.notebook_syntax("print(nope", cells=["print(nope"], cell=0)
    assert len(broken) == 1 and "severity" not in broken[0]  # a syntax error alone, red as before
