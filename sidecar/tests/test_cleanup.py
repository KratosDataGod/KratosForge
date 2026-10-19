"""Clean up (Validate's second tab): tidy-ups the person applies, and suggestions they decide on.
Nothing runs and nothing is written; a tidy-up never changes what the code does."""

from __future__ import annotations

import json

from fabric_desktop import cleanup
from fabric_desktop.server import handle
from fabric_desktop.service import Service


def code(source, cid=None, **meta):
    return {"type": "code", "source": source, "meta": meta, "cell_id": cid or f"c{abs(hash(source))}"}


def tidy(cells):
    return {t["id"]: t for t in cleanup.plan(cells)["tidy"]}


def applied(cells, ids):
    return [c["source"] for c in cleanup.apply(cells, ids)["cells"]]


def test_unused_and_repeated_imports_are_removed_and_used_ones_kept():
    cells = [code("from fabric_desktop import lakehouse\nimport os\nimport numpy as np, polars as pl\nimport json"),
             code("import polars as pl\nprint(json.dumps({}))\ndf = pl.DataFrame()")]
    t = tidy(cells)
    assert t["unused_imports"]["text"] == "Cell 1: no cell uses np, os." and t["unused_imports"]["on"]
    assert t["repeated_imports"]["text"] == "Cell 2: cell 1 already imports pl."
    assert t["unused_imports"]["diff"] == [{"cell": 1, "removed": ["import os", "import numpy as np, polars as pl"]}]
    assert applied(cells, ["unused_imports", "repeated_imports"]) == [
        "from fabric_desktop import lakehouse\nimport polars as pl\nimport json", "print(json.dumps({}))\ndf = pl.DataFrame()"]
    # Each can be chosen on its own.
    assert applied(cells, ["repeated_imports"])[0] == cells[0]["source"]


def test_an_import_that_may_do_something_when_imported_is_offered_unticked():
    cells = [code("import janitor\nimport os"), code("df = df.clean_names()")]
    t = tidy(cells)
    assert t["unused_imports"]["text"] == "Cell 1: no cell uses os." and t["unused_imports"]["on"]
    other = t["unused_package_imports"]
    assert other["on"] is False and "pyjanitor adds DataFrame methods" in other["text"]
    assert other["diff"] == [{"cell": 1, "removed": ["import janitor"]}]
    # What Apply makes with the tidy-ups ticked at first keeps it.
    on = [x["id"] for x in cleanup.plan(cells)["tidy"] if x["on"]]
    assert applied(cells, on)[0] == "import janitor"


def test_imports_are_left_alone_when_names_can_be_made_at_run_time_or_the_cell_is_frozen():
    assert "unused_imports" not in tidy([code("import os\nexec('x = 1')")])
    assert "unused_imports" not in tidy([code("import os\n%run other_notebook")])
    assert "unused_imports" not in tidy([code("import os", frozen=True), code("x = 1")])
    assert "unused_imports" not in tidy([code("import os  # needed by the shim")])  # a comment on the line stays
    assert "unused_imports" not in tidy([code("import os; x = 1")])
    assert "unused_imports" not in tidy([code("import json"), code("del json")])  # del needs the name
    assert "unused_imports" not in tidy([code("from typing import List"), code("def h(x: 'List[int]'): return x")])
    # A repeated import after the name was rebound isn't repeated.
    assert "repeated_imports" not in tidy([code("import polars as pl"), code("pl = None"), code("import polars as pl\npl.DataFrame()")])
    # An earlier frozen cell doesn't run, so the later import is needed.
    assert "repeated_imports" not in tidy([code("import polars as pl", frozen=True), code("import polars as pl\npl.DataFrame()")])


def test_spacing_keeps_the_code_identical_and_strings_untouched():
    src = 'x = 1   \n\n\n\n\ny = """a   \n\n\n\nb"""\n\n'
    cells = [code(src)]
    assert "spacing" in tidy(cells)
    [out] = applied(cells, ["spacing"])
    assert out == 'x = 1\n\n\ny = """a   \n\n\n\nb"""'
    assert "spacing" not in tidy([code("x = 1\ny = 2")])


def test_print_lines_are_off_at_first_and_empty_cells_go():
    cells = [code("df = 1\nprint(df)\nprint(\n  df)"), code("   "), code("", tags=["parameters"])]
    t = tidy(cells)
    assert t["print_lines"]["on"] is False and t["print_lines"]["diff"] == [{"cell": 1, "removed": ["print(df)"]}]
    assert t["empty_cells"]["cells"] == [2]  # the parameter cell stays, even empty
    out = cleanup.apply(cells, ["print_lines", "empty_cells"])
    assert out["removed"] == [cells[1]["cell_id"]] and out["cells"][0]["source"] == "df = 1\nprint(\n  df)"


def test_suggestions_point_at_a_cell_and_change_nothing():
    cells = [code("run_date = '2026-10-18'", tags=["parameters"]),
             code("from fabric_desktop import lakehouse\norders = lakehouse.read_table('dbo.orders')"),
             code("recent = lakehouse.read_table('dbo.orders').to_pandas()\nrecent = recent[recent.d == '2026-10-18']\n"
                  "for i, r in recent.iterrows():\n    pass"),
             code("lakehouse.write_table(orders, 'dbo.orders')"), code("again = lakehouse.read_table('dbo.orders')")]
    s = {(x["code"], x["cell"]) for x in cleanup.plan(cells)["suggestions"]}
    assert s == {("read_again", 3), ("whole_table_to_pandas", 3), ("row_by_row", 3), ("parameter_value", 3)}  # cell 5 reads after a write
    read = next(x for x in cleanup.plan(cells)["suggestions"] if x["code"] == "read_again")
    assert "uses capacity" in read["text"] and read["fix"] == "Reuse the DataFrame from cell 2."


def test_a_change_that_would_break_a_cell_is_dropped():
    cells = [code("if True:\n    import os\nx = 1")]  # not top level: not touched
    assert applied(cells, ["unused_imports", "spacing"]) == [cells[0]["source"]]


def test_the_engine_answers_without_writing(tmp_path):
    s = Service(client_id="x", root=tmp_path)
    cells = [code("import os\nx = 1   ", cid="a"), code("", cid="b")]
    plan = handle(s, json.dumps({"id": 1, "method": "notebook_cleanup_plan", "params": {"cells": cells}}))["result"]
    assert {t["id"] for t in plan["tidy"]} == {"unused_imports", "spacing", "empty_cells"} and plan["code"]
    out = handle(s, json.dumps({"id": 2, "method": "notebook_cleanup_apply",
                                "params": {"cells": cells, "ids": ["unused_imports", "spacing", "empty_cells", "nope"]}}))["result"]
    assert out == {"cells": [{"cell_id": "a", "source": "x = 1"}], "removed": ["b"],
                   "applied": ["unused_imports", "spacing", "empty_cells"]}
    assert not list(tmp_path.iterdir())  # nothing written
    from fabric_desktop import audit
    assert {"notebook_cleanup_plan", "notebook_cleanup_apply"} <= audit.READ_ONLY
