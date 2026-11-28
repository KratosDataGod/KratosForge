# Mutation testing, batch 459

One module not scored before, run with `--kinds all`:
- PySpark exercises: the exercise's Delta tables built from its setup SQL, its notebooks and data files put in the workspace, a learner's answer run in the Academy learner process (the pack's own code in a plain `python -I` process), the tables it left read back only once checked, and the result, shape and rerun checks graded in words, with a failure on hidden data never echoing the learner's error (`academy/pyspark_lab.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. test_academy_exercises was left out of the test set because each of its runs takes about 875 seconds, and with it test_academy_exercises_mutation_b and test_prod_write_paths, which touch this module only in passing; that can only lower the "before" score. The module has 245 mutants, so a sample of 150 was run. `main` has changed this module since (#3334, #3335); the new tests pass against today's version.

The test set was test_academy_replace_where, test_academy_limits, test_academy_pyspark_streaming and test_academy_pyspark_lab.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. No learner process runs in the new tests: grading gets a stand-in runner that writes the tables a run would leave, and only one test starts the pack's plain `python -I` process. The first "after" run left two survivors that tests could reach after all; they got tests and were run again, and the table counts that second run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/pyspark_lab.py` | test_academy_replace_where, test_academy_limits, test_academy_pyspark_streaming, test_academy_pyspark_lab (+ test_academy_pyspark_lab_mutation_b) | 150 of 245 (148 + 2) | 107 / 43 | **71.3%** | 107/148 → 147/148 | 0/2 → 0/2 | 147 / 3 | **98.0%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/pyspark_lab.py**:
  - Each kind of failed run reads as what happened: stopped after 120 seconds, out of memory, not started, a native crash with or without its exit code, the last error line, or no message at all.
  - An exit code reads as POSIX or Windows reports it: nothing for 0 or a non-number, a signal for a negative code, decimal up to 255 and hexadecimal above.
  - A count says "1 row" or "0 rows", with thousands separators. The first letter alone is lowered.
  - What is wrong with a table's rows is told in counts only, in each of its four wordings, singular and plural.
  - Good assets have no problems. Notebooks or files that aren't lists are one problem. A notebook not named like one, and a files folder not under Files/, are named.
  - Assets go to `notebooks/` and `Files/`, or their files folder.
  - A workspace file or an asset whose place is a folder is refused. Prepare writes its assets and tables, and can be run again in the same workspace.
  - A table that the log lists a data file for that isn't a file is refused. A table over the byte limit names the limit in MB. Tables read back are at most 256 MB. Loading a table that wasn't written says False; one that was says True.
  - Cleanup removes the built package once, and a package folder already gone is no error.
  - A reference run keeps the last 4,000 characters of its output and its errors.
  - Grading: a failed first run fails every check; a failure on hidden data fails its check with the hidden-data sentence; a reference solution that fails is an error of the exercise; the score is rounded to two places; wrong rows are told in counts; a second run that fails says it was the second; a rerun that writes the table first, or removes it, counts from or to zero.
  - → `test_academy_pyspark_lab_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `academy/pyspark_lab.py` | 112 | `getattr(data, "to_arrow_table", None) or data.arrow` to `and` | Equivalent here: DuckDB 1.5.6 and 2.0.0 both have `to_arrow_table`, so the mutant calls `arrow()` instead, which gives the same rows as a record batch reader, and delta-rs writes the same table from either. |
| `academy/pyspark_lab.py` | 334 | `code < 0` to `code <= 0` | Equivalent: a code of 0 already returned "" on the line before. |
| `academy/pyspark_lab.py` | 315 | `i > start` to `i >= start` when looking for "Current thread" | Equivalent: the line at `start` begins with "Fatal Python error:" or "Windows fatal exception:", so it never also begins with "Current thread". |
