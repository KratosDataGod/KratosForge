# Mutation testing, batch 456

One module not scored before, run with `--kinds all`:
- From assessment findings and Optimization Engine opportunities to optimization records, and back: each finding or opportunity becomes one record keyed by its own fingerprint, a detection of a record already stored is linked to it, and a finding and an opportunity on the same item are matched through the matches table (`savings/links.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 252 mutants, so a sample of 150 was run. `main` hasn't changed this module since.

The test set was test_savings_links.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. One survivor was left by the first "after" run that wasn't equivalent. Its test was made stricter and the mutant was run again; the table counts that second run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `savings/links.py` | test_savings_links (+ test_savings_links_mutation_b) | 150 of 252 (150 + 0) | 137 / 13 | **91.3%** | 137/150 → 149/150 | 0/0 → 0/0 | 149 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **savings/links.py**:
  - An opportunity that isn't a dict is refused with a ValueError, and so is a stored link row that isn't one.
  - A record's items leave out links without an artifact. So a workspace finding doesn't match a record whose links have no artifact.
  - A detection without a rule id is new and matches nothing. An opportunity doesn't match a finding's record on another item.
  - A matched link says the rule version wasn't checked and that the record didn't come back.
  - The latest stored row is the one with the highest version, whatever its time. A row without a version counts as version 0. On the same version the later row is the latest, and on the same version and time the higher record id.
  - The stored rule version comes from the highest-version row that names one, by the same order.
  - → `test_savings_links_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `savings/links.py` | 305 | `return []` dropped (returns None) in `_counterpart` when the detection has no items or no rule id | Equivalent: its one caller only tests the result for truth (`if hits:`), and None and [] are both false. |
