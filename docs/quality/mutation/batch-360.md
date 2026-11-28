# Mutation testing, batch 360

One module not scored before, run with `--kinds all`:
- The proof runtime copied into both sides of a proof: pinned reads, sandboxed writes, the read-only guard, recorded file reads, fixed seed and time zone, and the saved log (`proof_runtime.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 75 mutants, so all were run; none was a boundary mutant. The Spark tests that also import this module (test_prove_spark, test_spark_writer, test_spark_merge and others) were left out to keep each mutant's run short; the three files run cover the module directly.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. The "after" figures come from `--only` runs of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `proof_runtime.py` | test_proof_runtime_edges, test_clock_changes_mid_run, test_prove (+ test_proof_runtime_mutation) | 75 of 75 (75 + 0) | 59 / 16 | **78.7%** | 59/75 → 74/75 | none sampled | 74 / 1 | **98.7%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **proof_runtime.py**:
  - A plain table is read with the pyarrow reader. Deletion vectors (reader or writer feature) or column mapping (feature or mode) are read through DataFusion. Stand-in tables only.
  - Storage options come from the side the runtime is bound to.
  - A flat lakehouse keeps tables without a schema folder; keys and shared paths default to dbo.
  - An overwrite isn't seeded from the pinned table unless it keeps rows.
  - The seed defaults to 0, seconds are rounded to milliseconds, and a file read records its SHA-256 and its size.
  - → `test_proof_runtime_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `proof_runtime.py` | 181 | the read size `1 << 20` to `1 << 21` | Equivalent: only how much is read at a time changes, not the hash or the size. |
