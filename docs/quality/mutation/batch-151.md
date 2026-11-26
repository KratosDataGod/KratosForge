# Mutation testing, batch 151

A re-score of `backup.py`, which batch 4 left at **90.0%**, on today's `main`. Batch 4 ran before batch 36 added boundary mutants (`<` to `<=`, `>` to `>=`), so its score had none; this run uses `--kinds all` and counts them. Backing up and restoring the app's folder has changed since then, so the sample is new too.

The run was at `main` 4ddd3662 (2026-11-18), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The module now has 213 mutants, so 150 were sampled. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. No mutant's run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `backup.py` | test_backup, test_backup_edges, test_backup_integrity, test_backup_restore_whole, test_backup_mutation (+ test_backup_rescore) | 150 of 213 (147 + 3) | 132 / 18 | **88.0%** | 129/147 → 133/147 | 3/3 → 3/3 | 136 / 14 | **90.7%** | 14 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **backup.py**:
  - A member inside a skipped folder (`app/logs/...`) is refused: the skip rules apply to the path under `app/` or `agent/`.
  - A restore makes every nested folder it needs.
  - A database's stray `-wal`, moved aside when nothing else is replaced, still gets its copy in restore-safety and the result names it.
  - A restore that stops part way logs no warning about the `.restoring` files it already swapped in.
  - → `test_backup_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `backup.py` | 311, 340 | the suffix check in `_is_database` swapped; its `return False` to `None` | Equivalent, as in batch 4: the header check decides, and the caller only tests the result for truth. |
| `backup.py` | 334 | `no_checkpoint is not None and hasattr(...)` to `or` | Equivalent, as in batch 4: Python 3.12 and later have both. |
| `backup.py` | 134, 150, 220 | JSON `indent` changed | Equivalent, as in batch 4: only the layout of JSON that is read back as JSON changes. |
| `backup.py` | 305, 398 (×2), 409 | the copy buffer `1 << 20` changed | Equivalent, as in batch 4: only the read size changes, and 0 means the default. |
| `backup.py` | 413 | `zip(..., strict=True)` to `False` | Equivalent, as in batch 4: both lists come from the same plan. |
| `backup.py` | 463 | `dest.unlink(missing_ok=True)` to `False` when putting a restore back | Equivalent: that branch runs only for a file already swapped into place, which exists. |
| `backup.py` | 467, 472 | `exc_info=True` to `False` | Equivalent, as in batch 4: only the log line changes. |

