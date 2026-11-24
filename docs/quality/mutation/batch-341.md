# Mutation testing, batch 341

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- The proof runtime: pinned inputs, contained outputs, recorded reads and a fixed seed, for a proof's run on this PC and in Fabric (`proof_runtime.py`).

The run was at `main` 46ed1509b (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 75 of 75 mutants ran (75 point + 0 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_proof_runtime_edges, test_prove, test_clock_changes_mid_run, test_lakehouse_cov3, test_prove_spark and test_table_uris, about 40 s a session. The Spark writer, merge and maintenance files and test_state_writes, which also touch the runtime, were left out to keep each mutant's run short. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `proof_runtime.py` | test_proof_runtime_edges, test_prove, test_clock_changes_mid_run, test_lakehouse_cov3, test_prove_spark, test_table_uris (+ test_proof_runtime_mutation) | 75 of 75 (75 + 0) | 60 / 15 | **80.0%** | 60/75 → 74/75 | none | 74 / 1 | **98.7%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **proof_runtime.py**:
  - A pinned table with deletion vectors, or a column mapping mode other than none (in any case), is read through delta-rs's DataFusion reader; one with neither, or mode `none` or empty, through pyarrow. Tested with stand-in tables and query builder.
  - The defaults: seed 0, and schema dbo for a table's key and its shared path.
  - The storage options handed out are the side's own.
  - An overwrite of a pinned table starts empty (nothing is copied in or logged as read), and a later append to a table this side wrote isn't seeded either.
  - A file read records its size.
  - The seconds a side took are kept to a thousandth.
  - → `test_proof_runtime_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `proof_runtime.py` | 181 | a file read in chunks of `1 << 21` bytes instead of `1 << 20` | Equivalent: the SHA-256 and the byte count don't depend on the chunk size. |
