# Mutation testing, batch 75

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Sorting, filtering and column statistics on a SQL result as the grid shows it (`sql_view.py`).
- Notebook export to Downloads, import from a picked file, the page to print and the read-only report (`notebook_formats_service.py`).
- The semantic model best practices check: its notebook, run parameters, saved errors and findings (`model_bpa.py`).

The run was at `main` 8b8919a18 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_sql_exec.py` reads `app/`, so it was left out of the `sql_view.py` set; no other test in these sets reads `docs/`, `examples/` or `app/`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sql_view.py` | test_sql_view, test_regex_anchors (+ test_sql_view_mutation) | 115 of 115 (107 + 8) | 106 / 9 | **92.2%** | 101/107 → 107/107 | 5/8 → 8/8 | 115 / 0 | **100%** | none |
| `notebook_formats_service.py` | test_notebook_formats_service_edges, test_notebook_report, test_notebook_formats, test_chart_libraries (+ test_notebook_formats_service_mutation) | 124 of 124 (121 + 3) | 100 / 24 | **80.6%** | 100/121 → 120/121 | 0/3 → 3/3 | 123 / 1 | **99.2%** | 1 equivalent |
| `model_bpa.py` | test_model_bpa, test_fips, test_not_set_up (+ test_model_bpa_mutation) | 36 of 36 (36 + 0) | 27 / 9 | **75.0%** | 27/36 → 36/36 | none | 36 / 0 | **100%** | none |

No bug was found.

### What the survivors were, and the tests that now kill them

- **sql_view.py**:
  - At most three sort keys are read, and a fourth is ignored even when it names a column the result lacks.
  - A duration column is filtered as its text, not refused as a date.
  - A filter of exactly 200 characters is read, and 201 is refused.
  - A filter or sort on the position just past the last column is refused as a column the result doesn't have, even when the filter box is empty.
  - The distinct count is never above the rows that have a value, and a missing count reads as 0.
  - → `test_sql_view_mutation.py`.
- **notebook_formats_service.py**:
  - A report is at most 64 MB, and a page exactly at the limit is kept while one byte more is refused, keeping the report from before.
  - An archive exactly at its size limit is read, and one character more is refused.
  - A free notebook name is looked for up to number 9999, both when checking and when importing; a free file name in Downloads up to number 999, for an export and for a report.
  - An imported notebook is written as ipynb JSON indented by one space.
  - Notes are kept under the name of the notebook they belong to.
  - A failed write whose file is already gone keeps the write's own error.
  - The page to print and the report need a list of cells: a tuple, or a list holding text, is refused.
  - The run line is at most 200 characters.
  - → `test_notebook_formats_service_mutation.py`.
- **model_bpa.py**:
  - The check notebook is nbformat 4.5.
  - The notebook hash doesn't depend on key order.
  - A saved error's type is capped at 200 characters.
  - A finding with empty, missing or other empty-valued fields reads them as empty text, and filled fields keep their text.
  - → `test_model_bpa_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `notebook_formats_service.py` | 83 | `strict=True` to `strict=False` in `notebook_formats_import_check` | Equivalent: the names are made one for each notebook of the same list, so both sides always have the same length. |

