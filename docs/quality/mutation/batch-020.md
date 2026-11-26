# Mutation testing, batch 20

Three modules that no open pull request is changing:
- The proof's Spark side: the names and paths it accepts, the versions it reads, the SQL it rewrites, and the readers it patches so that a proof only changes its own folder (`proof_spark.py`).
- Structured streaming on this PC: what each sink reports as written, the query's progress and status, and the files a folder stream reads (`spark_stream.py`).
- Surrogate key tables: how many times a key commit is retried and how long it waits, the check on the keys a batch just added, the key service's answers, key counts, and source systems (`meta/keys.py`).

`engines/fabric_spark.py` was planned but left out, because #2417 is changing it. `spark_stream.py` took its place. Batch 19's modules were left to batch 19.

The run was at `main` 352188c0d (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 19 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. No test that reads `docs/`, `examples/`, `app/` or the module's own source was in these sets, so none was left out.

`test_prove.py` also uses `proof_spark.py`, but it is slow, so the sampled run left it out. It was run on the 10 survivors and killed none. In the same way, the other test files that use streams were run on the 22 `spark_stream.py` survivors and killed none, and `test_estate`, `test_meta_service_edges`, `test_platform_service_paths` and `test_platform` were started on the 34 `meta/keys.py` survivors and had killed none of the first two when they were stopped, so the new tests were written for all 34.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `proof_spark.py` | test_prove_spark, test_prove_spark_install (+ test_proof_spark_mutation) | 150 of 397 | 140 / 10 | **93.3%** | 149 / 1 | **99.3%** | 1 equivalent |
| `spark_stream.py` | test_spark_stream, test_spark_stream_edges (+ test_spark_stream_mutation) | 150 of 259 | 128 / 22 | **85.3%** | 147 / 3 | **98.0%** | 3 equivalent |
| `meta/keys.py` | test_keys, test_meta_keys_edges, test_shared_keys, test_meta_service_keys_edges, test_meta_atomic_save, test_meta_source_systems_edges (+ test_meta_keys_mutation) | 150 of 241 | 116 / 34 | **77.3%** | 149 / 1 | **99.3%** | 1 equivalent |

There were no timeouts. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **proof_spark.py**:
  - `dbo` naming a table on a lakehouse without schemas, in any case, and another schema being refused.
  - A `file:` path without a slash after the colon being the lakehouse mount too.
  - A `Tables/` folder being one name without schemas and two with them, and a deeper folder being refused.
  - A table pinned without a version still existing.
  - A frame reading the pinned version, and a table the proof made reading its latest.
  - Dropping a view the notebook didn't make being refused, alone or listed with one it did make.
  - A table name with no place in the SQL being refused rather than left as it was.
  - A load outside the lakehouse handing back Spark's own frame.
  - Every path given to `parquet` being checked, but only the first argument of the other readers, whose second argument is not a path.
  - → `test_proof_spark_mutation.py`.
- **spark_stream.py**:
  - A table sink reporting its rows, its query name (or none) and a trigger time in milliseconds.
  - A console sink reporting its rows, and `foreachBatch` reporting no row count.
  - A folder sink making missing folders, writing again, and naming its files `part-NNNNN-xxxxxxxx.parquet`.
  - A Delta sink at a `Tables/` path writing that table.
  - The batch message naming the versions read.
  - A folder stream reading only files of its format, not other files or a folder named like one.
  - Two streams joined being refused as something that runs in Fabric.
  - The query's last progress, its error and its stopped status; `queryName` and `options` returning the writer; `False` not being a set flag; and a file without partitions matching every row.
  - → `test_spark_stream_mutation.py`.
- **meta/keys.py**:
  - Making a key table and adding keys each trying 10 times, waiting longer each time, before giving up.
  - The unknown member being dated 1900-01-01, and a key table that has it not being written again.
  - A batch of keys already there adding none, and reporting so in full.
  - Keys following a 0 key, and reading a table that isn't there finding none.
  - A key that another run gave the same business key meanwhile stopping the batch.
  - Key counts for a missing table, a table with only its unknown member, and a table without it.
  - Only a store with both `issue` and a client being the platform's key service, and that service's lookups, counts and new keys, with unknown members and empty values left out.
  - A one-column business key in Spark SQL being just trimmed.
  - Source systems being active and id 0 unless they say otherwise, from a dict and from the file.
  - Files being written into a project that has no keys folder yet, and the path coming back.
  - → `test_meta_keys_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `proof_spark.py` | 196 | `tree.this is not None` → `is None` in the DROP VIEW fallback | Equivalent: sqlglot always sets `tables` on a DROP, so the fallback after `or` is never read. |
| `spark_stream.py` | 101 | `history(now - last)` → `history(now + last)` | Equivalent: reading more history only adds older versions to a dict looked up by the versions after `last`. |
| `spark_stream.py` | 104 | `range(last + 1, …)` → `range(last + 0, …)` | Equivalent: version `last` is the one already loaded, so it has no removed and no new files. |
| `spark_stream.py` | 332 | `explain(extended=False)` → `True` | Equivalent: `explain` prints the same sentence whatever `extended` is. |
| `meta/keys.py` | 432 | `else 0` → `else 1` when no unknown member row comes back | Equivalent: `read_where` gives `None` only for a table that isn't there, and `key_stats` has already answered for a missing table, so only a table deleted between the two reads could reach it. |

### Real bugs found

None.

### Not in this batch

`engines/fabric_spark.py`, which #2417 is changing, and `integrations/services.py`.

