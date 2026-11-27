# Mutation testing, batch 430: academy/pyspark_shim.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`). This is the first mutation run of this module. It has one boundary mutant.
- The `spark` an Academy PySpark exercise gets (`academy/pyspark_shim.py`): DuckDB's PySpark API reading and writing Delta tables only under the exercise's `tables/`, `df.write` with its modes and options (`overwriteSchema`, `mergeSchema`, `replaceWhere`), the session's connect-time settings, `import pyspark` and `from delta.tables import DeltaTable` resolving to the exercise's own, and the `notebookutils` a notebook starts with.

It has 87 mutants, and this run ran all 87 of 87, uncapped.

The run was at `main` e49cddbebe (2026-11-24), with the method of batch 379. Each pytest session had its own fresh `--basetemp`, removed at the end of the session; the nesting-check log stayed empty for the run and both re-runs. The kills don't end in a streak of near-identical times. Three kills chosen at random (mutants 30, 65 and 77) were re-run by hand without `-x`: each failed real tests (1, 1 and 2). No mutant timed out.

The module runs in the learner process, copied into the generated `kf_learner` package. The new tests build `AcademySession` in the test's own process on Delta tables in `tmp_path`; the tests that call `session()`, which changes DuckDB's DataFrame class and `sys.modules` for the whole process, put both back afterwards. No learner process starts for them and nothing makes a network call. The set is test_academy_replace_where and test_academy_pyspark_lab without its 9 tests (13 with their parameters) that grade exercise E19 or a learner's answer in the learner process: those take about 95 of the file's 138 seconds and reach this module only through the learner process's copy. The set runs in about 22 seconds under load.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/pyspark_shim.py` | test_academy_replace_where, test_academy_pyspark_lab (13 deselected); after: + test_academy_pyspark_shim_mutation | 87 of 87 (86 + 1), uncapped | 53 / 34 | **60.9%** | 53 / 86 (61.6%) → 86 / 86 (100%) | 0 / 1 → 1 / 1 | 87 / 0 | **100%** | none |

The 34 survivors were re-run with the new file added (`--only`): 33 were killed and 1 was left. That one, mutant 15 (the `raise` removed from `table_path`'s second guard, that the resolved path is under `tables/`), was not equivalent: the first guard refuses every link it sees, so only a link it misses reaches the second. A test was added that hides the link from the first guard and checks the second still refuses it, and mutant 15 was re-run alone with the file: killed. The 35 new tests in `tests/test_academy_pyspark_shim_mutation.py` check:
- `table_path`: lowercase paths under `tables/`, name parts of up to 63 characters, a `tables/` folder or a table folder that is a link refused, a plain folder on the way accepted, and the second guard on its own;
- `Writer`: every mode name and the unknown one, `format`, `option` and `options` with their keys lowercased, which values turn a flag on, and `partitionBy`;
- `saveAsTable`: a new table in any mode, an existing one refused in `error` mode and left alone in `ignore` mode, append and overwrite, `mergeSchema` only on an append and `overwriteSchema` only on an overwrite, the plain local path and the mode and schema mode handed to delta-rs, and `replaceWhere` on an append (a note, then the rows appended), on a new table (every row, quietly), with `overwriteSchema` (a note, the schema kept), a row outside it, a write that fails delta-rs's own check, and any other Delta error raised as it is;
- `Reader` and the session: `format`, a missing table and a folder with no `_delta_log`, `spark.table`, the settings by DuckDB major version, connecting in memory with them (and without the time zone when the ICU extension can't load), `read_delta` at a version, plain string and binary instead of views with each field's nullability and metadata kept, `_arrow`'s fallback, `readStream` and the builder;
- `session()` binding `df.write`, `df.writeStream`, `df.isStreaming`, `import pyspark` and its submodules, `SparkSession.builder` and `delta.tables`, and `namespace()` installing `notebookutils` once.

No bugs were found.

### Left

None.
