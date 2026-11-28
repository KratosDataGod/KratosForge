# Mutation testing, batch 237

A re-score of `spark_local.py`, which batch 8 left at **92.0%**, on today's `main`. Batch 8 ran before boundary mutants, and the module has grown since then: replaceWhere, global temp views, the time zone statements, the change feed and the text reader. So this run uses `--kinds all` and a new sample. Batch 205 has already re-scored its boundary mutants alone.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The module now has 1,352 mutants, so 150 were sampled, all of them point mutants. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. `test_spark_read_options.py::test_the_docs_page_is_up_to_date` reads `docs/`, so it was deselected.

`test_spark_compat.py` is `spark_compat.py`'s own test and takes about 110 s of the set's 140 s, so the "before" run left it out. It went back into the "after" run, together with existing tests that cover the module's newer paths but weren't in its set: test_delta_write_integrity, test_spark_maint, test_spark_views_text, test_spark_timezone_state and test_delta_change_feed. The machine was shared with other mutation runs, so each mutant's timeout was raised from 120 s to 600 s (`mutate.TIMEOUT`); no run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_local.py` | test_spark_local, test_spark_local_edges, test_spark_local_more_edges, test_spark_merge, test_spark_read_options, test_spark_refusals, test_spark_write_types, test_append_conversions, test_spark_compat_edges, test_duckdb_try_cast, test_spark_writer, test_spark_catalog_names, test_spark_csv_options, test_spark_json_parquet_options, test_spark_json, test_spark_local_mutation, test_spark_local_cov2, test_query_profile, test_spark_local_boundary (+ test_spark_local_rescore, and the existing test_spark_compat, test_delta_write_integrity, test_spark_maint, test_spark_views_text, test_spark_timezone_state, test_delta_change_feed) | 150 of 1,352 (150 + 0) | 122 / 28 | **81.3%** | 122/150 → 146/150 | none | 146 / 4 | **97.3%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_local.py**: most survivors are killed once the existing files above join the set. They cover replaceWhere on an overwrite of a table that exists, a `global_temp` view that isn't there, maintenance while previews are off, time zone statements beside others, a change feed read with an alias, and the paths `test_spark_compat.py` reaches.
- **spark_local.py**, killed by new tests:
  - A call that sets the time zone is split at its semicolons only, and one statement isn't split.
  - `reset()` closes the session it replaces.
  - A type string that is a whole struct (`"struct<a:int,b:string>"`) is that struct.
  - `pyspark` isn't aliased when real PySpark can be imported.
  - A schema stays in a MERGE, UPDATE or DELETE target when the attached lakehouse has schemas.
  - Ints beside a value that `spark_infer` leaves to DuckDB, such as a datetime, are still bigint.
  - Named columns holding a tuple or a dict are inferred as PySpark infers them (a struct of `_1`, `_2`, and a map).
  - A text file's one column takes the schema's name.
  - → `test_spark_local_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_local.py` | 244 | `zip(..., strict=True)` to `False` for partition folders | Equivalent: both halves come from the same row, split at the partition count. |
| `spark_local.py` | 1543 | `_statement`'s `evolve=False` default to `True` | Equivalent: its one caller always passes `evolve`. |
| `spark_local.py` | 1670 | the "Only UPDATE and DELETE" raise removed | Equivalent, as in batch 8 (its line 1185): an Update or Delete tree always parses to a change. |
| `spark_local.py` | 1696 | `and` to `or` in "a schema of names" | Equivalent: a list schema is a list of names. Spark itself refuses one holding anything else, and a StructType isn't a list or tuple. |

