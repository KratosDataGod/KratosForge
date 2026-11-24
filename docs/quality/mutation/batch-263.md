# Mutation testing, batch 263

One module that had no score before, run with `--kinds all`:
- The SQL page's runs: starting a lakehouse, warehouse or local project run and refusing what it may not do, the run's process and what it reports, its snapshot, pages, statistics and exports, warehouse changes and their confirmation, local runs' errors, and the clean-up of old results (`sql_exec.py`).

The run was at `main` ea988e99 (2026-11-21), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Each pytest session got its own fresh temporary folder, so a mutant that leaves a folder behind can't fail the runs after it. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because the machine was shared with two other mutation runs. The module has 938 mutants, so 150 were sampled (149 point and 1 boundary). `test_sql_exec.py::test_service_calls_are_wired_and_audited_as_intended` reads `app/src-tauri/src/watchdog.rs`, so it was left out with `--deselect`. The warehouse test files (test_warehouse_queries and others) were not run, to keep each mutant's run short; the new tests cover the warehouse paths the sample reached. No mutant's run timed out. A first "before" run stopped part way when the machine restarted; the figures here are from a whole run started again from the beginning.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sql_exec.py` | test_sql_exec (one test deselected), test_sql_exec_cov3, test_sql_exec_edges, test_sql_exec_more_edges, test_sql_params, test_sql_statements, test_sql_page_hardening, test_export_exact, test_local_sql_stop (+ test_sql_exec_mutation) | 150 of 938 (149 + 1) | 114 / 36 | **76.0%** | 114/149 → 141/149 | 0/1 → 1/1 | 142 / 8 | **94.7%** | 8 equivalent |

No bugs were found. The score stays below 95% because each mutant left is equivalent.

### What the survivors were, and the tests that now kill them

- **sql_exec.py**:
  - A snapshot that holds fewer rows than the run wrote asks for the query to run again, for the whole result and for a page.
  - A warehouse run refused before anything is sent keeps its reason and the refused statement, and the refusal is audited without the change and batch fields.
  - A warehouse dry run that only reads judges a change as refused. A read-only script keeps its BEGIN TRAN and COMMIT, so it is refused at statement 1.
  - A warehouse change reads the writes setting, and one turned off by policy says so. A warehouse run that asks for more than reads and confirmed changes says what it may do. A precondition is only for a warehouse change.
  - A change confirmed for another warehouse's name is never sent. A change whose worker gives no count changed no rows, and its audit records the count and the commit.
  - A worker that can't start fails the run with the reason. A failed lakehouse run names no batch.
  - A local run out of memory names the project's limit, or DuckDB's default share when there is none. A full disk names the free space where the file was written.
  - A run's parameter values and statements stay out of its `repr`.
  - A completed run whose snapshot is gone has none.
  - An export tries 999 names before it gives up.
  - A PC whose memory can't be read is taken to have 8 GB.
  - An earlier session's result younger than an hour's retention is kept.
  - EXPORT and IMPORT DATABASE are found past an empty comment, and not inside an E'' string (also on the text's second character) or a dollar-quoted string. A `$` or an `e` that ends a name starts no string.
  - A JSON export notes only whole numbers past 9,007,199,254,740,991, not that number itself.
  - → `test_sql_exec_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `sql_exec.py` | 189, 190 | `proc` and `timer` fields' `repr=False` to `True` | Equivalent: only a run's `repr` changes, and these hold no SQL or values. |
| `sql_exec.py` | 432 | `_cleaned_at_start = True` to `False` | Equivalent: every start then repeats the earlier-sessions sweep, which only finds folders this session doesn't know, and a run's folder is made after the sweep. |
| `sql_exec.py` | 705 | the call thread's `daemon=True` to `False` | Equivalent: the thread ends once it has answered. |
| `sql_exec.py` | 776, 853 | `ignore_errors=True` or `missing_ok=True` to `False` when a failed run's files are removed | Equivalent: the files were just listed, or the folder was made when the run started, so they are there to remove. |
| `sql_exec.py` | 927 | `zip(..., strict=True)` to `False` over a batch's columns | Equivalent: a record batch's columns all have its number of rows. |
| `sql_exec.py` | 1215 | `j < 0` to `j < 1` after looking for a comment's `*/` | Equivalent: the search starts two characters past the `/*`, so it never finds `*/` at 0. |
