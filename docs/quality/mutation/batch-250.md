# Mutation testing, batch 250

A re-score of `meta/snapshot.py`, which batch 114 left at **94.9%** with one mutant not killed, on today's `main`.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The module has 39 mutants, all point mutants, so all were run. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. No mutant's run timed out.

Before the "after" run, the new test passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. It also passes on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/snapshot.py` | test_rollback, test_meta_snapshot_mutation, test_snapshot_cov2 (+ test_meta_snapshot_rescore) | 39 of 39 (39 + 0) | 37 / 2 | **94.9%** | 37/39 → 38/39 | none | 38 / 1 | **97.4%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/snapshot.py**:
  - A restore that fails and then can't delete its temporary folder still raises why the restore failed, not the failed delete. Batch 114 left this one, line 91's `ignore_errors=True` to `False`, because a delete can't be made to fail on Linux without patching. The test makes every file delete fail with a permission error, the way a file held open does on Windows.
  - → `test_meta_snapshot_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/snapshot.py` | 105 | `_matches`'s `return False` to `None` | Equivalent, as in batch 114: both callers read it as a condition, and `None` and `False` are both false. |

