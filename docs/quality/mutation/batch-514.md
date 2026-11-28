# Mutation testing, batch 514

A re-score of `backup.py` on today's `main`, with `--kinds all`. It backs up the app's folder (and an agent's queue and alert databases) to a zip with a manifest, and restores one after checking it. It was last scored in batch 151 at **90.7%** (`--kinds all`), and before that in batch 4 at **90.0%** (point mutants only). Since batch 151 it has gained the audit trails a restore leaves alone, a refusal by name for a path too long for the PC, and Windows names compared in any case, so the sample is new too.

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 310 mutants, so a sample of 150 was run.

The test set was the files of batches 4 and 151: test_backup, test_backup_edges, test_backup_integrity, test_backup_restore_whole, test_backup_mutation and test_backup_rescore. The newer test_backup_audit_trails wasn't in it, which is why many of the audit trail mutants survived.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. Two of them write a zip whose manifest is 64 MB of spaces (about 64 KB once compressed).

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `backup.py` | test_backup, test_backup_edges, test_backup_integrity, test_backup_restore_whole, test_backup_mutation, test_backup_rescore (+ test_backup_mutation_b) | 150 of 310 (145 + 5) | 112 / 38 | **74.7%** | 109/145 → 135/145 | 3/5 → 5/5 | 140 / 10 | **93.3%** | 10 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **backup.py**:
  - The audit trail a name is in is found by its first two parts (`app/audit/`, `agent/integrations/audit/`), for the folder itself too, and only under `app/` or `agent/`.
  - A preview counts the lines of the audit trails' `.jsonl` files only, without blank lines. One line says "1 audit entry", and more say "entries". The preview names the trail folder, not each file.
  - A manifest of exactly 64 MB is read, and one byte more isn't, with a message that says 64 MB.
  - A manifest that lists something that isn't an entry, or a cleaned note without what was removed, can't be used.
  - Each kind of Windows alias is one: a device name, a short name, and a trailing dot or space.
  - A skipped name in another case (`Debug.LOG`, `ebwebview/`) is refused in a manifest.
  - A name whose staging name (`<name>.restoring`) is exactly 255 characters can be restored, and one of 256 is refused by name. "Too long" is said for the error number or for the file name.
  - A folder where a restored file goes is named, nothing changes, and no `.restoring` file is left.
  - → `test_backup_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `backup.py` | 415 | `no_checkpoint is not None and hasattr(...)` to `or` | Equivalent, as batches 4 and 151 said: Python 3.12 and later have both. |
| `backup.py` | 421 | `_in_use`'s `return False` to `None` | Equivalent: its one caller only tests the result for truth. |
| `backup.py` | 174 | JSON `indent` 2 to 3 for a cleaned file | Equivalent, as batches 4 and 151 said: only the layout of JSON that is read back as JSON changes. |
| `backup.py` | 377, 386, 492, 503 | the copy buffer `1 << 20` changed, to `1 << 21` or to 0 | Equivalent, as batches 4 and 151 said: only the read size changes, and 0 means the default. |
| `backup.py` | 507 | `zip(..., strict=True)` to `False` | Equivalent, as batches 4 and 151 said: both lists come from the same plan. |
| `backup.py` | 556, 597 | `exc_info=True` to `False` | Equivalent, as batches 4 and 151 said: only the log line changes. |

**Main changed after this run (2026-11-28):** #3392 (role filters and members withheld from the AI tools' Ossie export and preview) changed `backup.py` after the run. The score above is for the module at the run's `main`, as named on this page, and it was not run again on the new module. The new tests pass unchanged on today's main.
