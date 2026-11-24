# Mutation testing, batch 139

One incident module not covered before, run with `--kinds all`, so its score counts the boundary mutants together with the earlier kinds:
- Incident history for the central lakehouse: timeline and snapshot rows, the record of what this PC already copied, and reports of time to acknowledge, time to resolve and SLAs met (`workloads/incident_history.py`).

The run was at `main` 5865db95 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. The module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed. Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/incident_history.py` | test_incident_desk, test_corrupt_state_reads (+ test_incident_history_mutation) | 86 of 86 (83 + 3) | 68 / 18 | **79.1%** | 67/83 → 83/83 | 1/3 → 3/3 | 86 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/incident_history.py**:
  - This PC remembers 50,000 copied record ids, and reports cover 7, 30, 90 or 365 days.
  - A snapshot of an incident that leaves fields out gets the defaults ("detected" and empty texts), and its "as of" time is when it opened; one that has the fields keeps them, and its newest timeline entry is its "as of" time.
  - A timeline entry without text is copied with empty text.
  - A record of copies that isn't JSON, or a folder where the file should be, sends everything again.
  - A naive time is read as UTC.
  - A report counts incidents opened at exactly either end of its window, and all met is 100 %.
  - → `test_incident_history_mutation.py`.

### Left

None.

