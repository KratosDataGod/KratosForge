# Mutation testing, batch 181

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Running a notebook in Fabric: the shim a notebook runs with, and the check that blocks or flags a cell before it runs, with its SQL, magics, `%run` order, Spark reads and writes, Preview-only Delta calls and schedules (`fabric_run.py`).

Batch 17 sampled 150 of this module's 764 mutants then (78.7% before, 98.0% after). It now has 782, after #3047 changed it, and this run ran all 782 of 782.

The run was at `main` bdf2c1775 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902 and the method of batch 182: tests first passed on the unmutated copy, and paths were relative to `sidecar/`. Each pytest session got a fresh `--basetemp` folder of its own, from a small run-only plugin that isn't committed, as in batch 183: a fixed per-lane `--basetemp` isn't enough, because pytest deletes it at the start of every session, so a folder nested too deep by one mutant would fail every later session and score each later mutant as killed. After the runs, no folder in that tree was nested 40 levels deep, and the last kills of the full run don't have near-identical times. Three kills picked at random were checked by hand, as in batch 182: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (1, 91 and 24).

The set is the files that test this module's own functions: its check, magics, notebookutils and dbutils, Spark reads, writes, merges and refusals, and schedules. It runs in about 30 seconds on this busy machine. More than 40 other files name the module, most of them to reach it through other services (risk, X-ray, workloads, estate and more) or to replace a part of it. They were left out to keep each mutant's run short, among them test_cloud (about 15 s) and test_validate (about 19 s), which batch 17 ran. The score counts only the files given, as in earlier batches. One test was deselected: `test_notebook.py::test_notebook_reads_local_files_and_writes_tables` reads `examples/`, which the tool doesn't copy, so it failed on the unmutated copy.

Before the "after" runs, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They passed both ways. The new tests pass with DuckDB 1.5.6 and 2.0.0.

The "after" run of the 88 survivors was done in three passes, because tests were still being added while the first ran: it killed 69, a second pass killed 5 of the 19 left, and a third killed the last boundary mutant after one more assertion. Between passes the new test file only gained tests, so a kill in an earlier pass still holds. No mutant timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `fabric_run.py` | test_compute_check_ast, test_conversions_everywhere, test_databricks_magics, test_dbutils, test_fabric_run_edges, test_fabric_run_mutation, test_notebook (one test deselected), test_notebook_comments, test_notebook_magics, test_notebookutils_helpers, test_prove_spark, test_schedule_config, test_spark_api_fails_cleared, test_spark_csv_options, test_spark_json_parquet_options, test_spark_map, test_spark_merge, test_spark_refusals, test_spark_udf (+ test_fabric_run_uncapped) | 782 of 782 (768 + 14), uncapped | 694 / 88 | **88.7%** | 690/768 → 755/768 | 4/14 → 14/14 | 769 / 13 | **98.3%** | 13 equivalent |

### Bugs found

- **fabric_run.py**: a read with an empty format, `spark.read.format('').load('f')`, is refused with "reads format 'parquet', which this PC's Spark doesn't have", though this PC reads Parquet. The empty text isn't one of the formats this PC reads, so the read is refused, but the message names it with `fmt_text or "parquet"` (line 680), which turns the empty format into Parquet. #3118 has since fixed it on `main`, so `test_an_empty_format_is_not_refused_as_parquet` is now a plain test (it was a strict expected failure until then).

### What the survivors were, and the tests that now kill them

- **fabric_run.py**:
  - SQL that changes another lakehouse with a value known only at run time runs in Fabric only. The shim cell has schemas on unless told otherwise. A built-in file over a million bytes isn't read.
  - A comment counts only from where it starts, also on line 2 after a blank first line, and code before it on the same line isn't in it.
  - SQL text is followed through five names and no more, on either side of a `+` and in an f-string, with at most 16 readings. `sql` on any name of the session is SQL, and so is the `sqlQuery` keyword.
  - Names for notebookutils and for the Spark session are followed five assignments deep. A name first imported from another module and then set to a notebookutils part is that part. Relative imports aren't notebookutils, and a from-import names its part.
  - Only `option` and `options` set write options, also by keyword or as a dict. Only a `saveAsTable` off a writer is checked for write options, and its own arguments and keyword splats aren't options.
  - Read options come only from `option`, `options` and the reader's own keywords, and one without a value is read as none. A CSV read in a strict mode needs a schema, and a `schema` link counts as one. A schema on a table read names a table.
  - A load of one path reads Parquet, not a format named by the path. A load with a path and a format reads that format, and a format this PC doesn't read is named.
  - A name with no values, or with a value the check can't see, isn't a Delta table; a Delta table by its full module path is one. Delta maintenance runs here when the preview setting isn't set or the settings can't be read.
  - A followed notebook shares this one's lakehouse unless told otherwise; `runMultiple` and a DAG's activities run without it unless their args say so. A notebook or built-in file already on the way is a loop, and notebooks are followed only to the depth limit. What the check can't read in a followed notebook is passed on, a followed notebook already blocked in Fabric keeps its own reason, and a loader that gives cells without a lakehouse is still read.
  - A `%%fs` magic blocks only a Python cell not already blocked. A SQL cell this PC may refuse is unknown on its first line. A SQL line magic is quoted by its own line, and a SQL cell that names `_sqldf` itself is left as a SQL cell.
  - A quoted line is cut after 60 characters; it is that line even when empty, the last line is the last line, and line 0 quotes the first. Python the check can't read and that has no line is on line 1.
  - A Python notebook keeps Fabric's kernel and its own other metadata.
  - A weekly schedule needs both a day and a time. A schedule ends ten years after it starts by default. A cron schedule runs every 1 to the longest interval of minutes, and one under a minute is refused.
  - → `test_fabric_run_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `fabric_run.py` | 287 | `max(..., default=0)` to `default=1` for the last line of a cell with `%sql` | Equivalent: the cell has a `%sql` line, so it has a line that isn't blank and the default is never used. |
| `fabric_run.py` | 631, 641 | `"write" in names[1:]` to `names[0:]` | Equivalent: `names[0]` is the call's own method (`option`, `options`, `saveAsTable` or `insertInto`), never `write`. |
| `fabric_run.py` | 644, 685 | `links[1:]` to `links[0:]` when collecting options | Equivalent: `links[0]` is the call itself, a save or a read, never `option` or `options`. |
| `fabric_run.py` | 661 | `"read" not in names[1:]` to `names[0:]` | Equivalent: if the method itself is `read`, the check goes on only to return at the next test, since `read` isn't a read end. |
| `fabric_run.py` | 672, 690, 694 | `links[1:]` / `names[1:]` to `[0:]` when finding the format or a schema | Equivalent: by then the method is a read end (`load`, `table`, `csv`, `json`, `parquet` or `text`), never `format` or `schema`, as batch 17 found for its lines 659 and 677. |
| `fabric_run.py` | 707 | `getattr(node, "lineno", 1)` to `0` | Equivalent: every kind of node the loop reports on has a line number; only nodes it skips lack one. |
| `fabric_run.py` | 777 | a DAG activity that isn't a dict, `(None, False)` to `(None, True)` | Equivalent: its path is `None`, so the whole list is given up before any flag is read. |
| `fabric_run.py` | 817 | `source.rfind("\n", 0, ...)` to `rfind("\n", 1, ...)` in `_snippet` | Equivalent: it differs only for a match on line 2 of a source that starts with a blank line, where the quote starts at the blank first line and so still quotes line 2, as batch 17 found for `_in_comment`. |
| `fabric_run.py` | 1056 | `replace(microsecond=0)` to `microsecond=1` | Equivalent: the start and end are written with `%Y-%m-%dT%H:%M:%S`, which has no microseconds. |

