# Mutation testing, batch 83

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Spark's datetime patterns for `date_format`, `from_unixtime`, `to_date`, `to_timestamp` and `unix_timestamp`, as DuckDB SQL (`spark_datetime.py`).
- Databricks `dbutils` for notebooks on this PC: `fs`, widgets, secrets, task values and the notebook context (`dbutils_local.py`).
- Lookup and Get Metadata in local pipeline runs (`pipeline_data.py`).

The run was at `main` 70da27aad (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839, so each mutant ran without a bytecode cache, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `dbutils_local.py` has fewer than 150 mutants, so all were run; the other two were sampled to 150.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`, with one exception, handled as follows:
- `test_spark_datetime.py::test_what_still_differs_is_listed_in_the_compatibility_docs` reads `docs/`, so it was left out with `--deselect`.
- From `test_spark_compat.py`, only its four date and time tests were run, by node id. The rest of that file runs no datetime pattern and takes about 30 s per mutant.

A mutant whose run times out counts as killed, as in earlier batches. So does one whose run used up memory and was stopped.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_datetime.py` | test_spark_datetime (one test deselected), test_spark_local, test_sqlcompat_rules_mutation, the four date and time tests of test_spark_compat (+ test_spark_datetime_mutation) | 150 of 291 (142 + 8) | 120 / 30 | **80.0%** | 115/142 → 141/142 | 5/8 → 6/8 | 147 / 3 | **98.0%** | 3 equivalent |
| `dbutils_local.py` | test_dbutils, test_dbutils_parity, test_sql_cell_widgets, test_databricks_notebook_helpers_mutation (+ test_dbutils_local_mutation) | 118 of 118 (117 + 1) | 104 / 14 | **88.1%** | 103/117 → 114/117 | 1/1 → 1/1 | 115 / 3 | **97.5%** | 3 equivalent |
| `pipeline_data.py` | test_pipeline_data, test_pipeline_data_edges, test_pipeline_script, test_pipeline_runner_edges, test_pipeline_gotchas, test_orchestration_runner_mutation (+ test_pipeline_data_mutation) | 150 of 211 (149 + 1) | 134 / 16 | **89.3%** | 134/149 → 149/149 | 0/1 → 1/1 | 150 / 0 | **100%** | none |

No bug was found.

### What the survivors were, and the tests that now kill them

- **spark_datetime.py**:
  - Each pattern letter takes up to the most letters java.time takes (X, x and Z five; S nine; D three; d, h, K, k, H, m and s two; a one) and is refused by name past that. G, M, L, Q, q and E take four and a year six; past that Spark stops too ("Too many pattern letters"), and so does this PC (since #2868; before it they took five and a year 99).
  - The localized offset is written O or OOOO only, and A, the first letter, is read as a letter Spark refuses.
  - The day and the day of the year are padded to the letters given.
  - One to four era letters give the short or full era (five gave the narrow era until #2868 refused them, as Spark does).
  - The localized and ISO offsets come out right in every width, with Z for a zero offset where java.time writes it.
  - An empty pattern gives empty text, and formatting is lenient unless asked to be strict.
  - Reading takes LLLL and yyyy, and refuses LLLLL and an offset of four letters.
  - → `test_spark_datetime_mutation.py`.
- **dbutils_local.py**:
  - A `%fs` drive path is this PC's disk, and other paths are checked as `dbutils.fs` checks them.
  - A widget's label is its name unless one is given, including a combobox.
  - A multiselect value set in the app is kept only when every item is a choice; a dropdown value is one choice, even with a comma in it.
  - A task value is kept under the task running now.
  - The context's user is the Windows user first, then `USER`, then empty.
  - A notebook run without a timeout passes 0.
  - Listing secrets asks each page with a 30-second timeout, sends the API version on the first page only, and stops when Key Vault repeats a page.
  - → `test_dbutils_local_mutation.py`.
- **pipeline_data.py**:
  - A Lookup output of exactly 4 MB is returned, and one byte more fails.
  - A connection type known only at run time passes the check, and `@@` text is not an expression.
  - The check's message names the activity's kind, "a connection" when the connection has no type, and "a dataset" when the dataset has none.
  - Only runs with a Lookup, a Get Metadata or a Script on a lakehouse sign in first.
  - Get Metadata's `exists` and `itemName` don't read the table, and `structure` and `columnCount` each read its schema.
  - A missing table without a schema is named in `dbo`, and a missing file fails unless `exists` is asked.
  - → `test_pipeline_data_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_datetime.py` | 100 | `n <= 1` to `n <= 0` in `_pad` | Equivalent: `_pad` is only called with more than one letter. |
| `spark_datetime.py` | 145 | `n > 1` to `n >= 1` for `d` | Equivalent: one letter then goes through `_pad` with 1, which gives the same unpadded text. |
| `spark_datetime.py` | 162 | `n > 1` to `n >= 1` for `m` and `s` | Equivalent, for the same reason. |
| `dbutils_local.py` | 76 | default `recurse=False` to `True` in `fs.mv` | Equivalent: `recurse` is not used, because folders move whole. |
| `dbutils_local.py` | 292 | default `precise=False` to `True` in `data.summarize` | Equivalent: `precise` is not used. |
| `dbutils_local.py` | 86 | `cp` checks its first 2 arguments as paths, to 3 | Equivalent in practice: the third argument of `cp` is `recurse`, a flag, which the path check lets through. |

