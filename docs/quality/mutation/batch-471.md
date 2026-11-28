# Mutation testing, batch 471

One module not scored before, run with `--kinds all`:
- Reading and writing an environment's manifest with conditional writes (create only when none exists, replace only the copy that was read), the bounded OneLake read of it, and this PC's cache of the environments it connected to (`controlplane/store.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 70 mutants, so all were run.

The test set was test_controlplane, test_controlplane_hardening and test_controlplane_service.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The OneLake read is tested against test_controlplane's fake DFS, never OneLake.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `controlplane/store.py` | test_controlplane, test_controlplane_hardening, test_controlplane_service (+ test_controlplane_store_mutation_b) | 70 of 70 (67 + 3) | 54 / 16 | **77.1%** | 53/67 → 67/67 | 1/3 → 3/3 | 70 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **controlplane/store.py**:
  - A manifest missing from OneLake reads as None. Reads are streamed, in 16 KB chunks.
  - An error status from 400 up raises an error and its body is never read as a manifest.
  - A Content-Length over 64 KB refuses the read before the body is read, with "larger than 64 KB" in the message. A Content-Length of exactly 64 KB, or a small one, is read.
  - A manifest a newer app wrote is not replaced, and the store is left as it was.
  - A Read is frozen.
  - The cache lists environments by name, whatever their case.
  - → `test_controlplane_store_mutation_b.py`.

### Left

None. Every mutant is killed.
