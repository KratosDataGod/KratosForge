# Mutation testing, batch 332

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The savings ledger's corrections, reversals and reconciliation report (`savings/corrections.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902. The module has 317 mutants, and the run ran all 317 of 317 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that calls `savings/corrections.py`: test_savings_corrections. It takes about a second, so none was left out. No `-k` filter or `--deselect` was needed.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (30, 27 and 5).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `savings/corrections.py` | test_savings_corrections (+ test_savings_corrections_mutation) | 317 of 317 (310 + 7), uncapped | 264 / 53 | **83.3%** | 262/310 → 306/310 | 2/7 → 7/7 | 313 / 4 | **98.7%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **savings/corrections.py**:
  - Measured is stronger than estimated, in either order.
  - An event without an id, a run or a layer is refused by name. A scenario event is refused as a scenario, and an unknown classification by name.
  - A correction whose layer afterwards is a scenario, missing or unknown is refused. So is one whose result figure doesn't fit its classification.
  - A correction of another run's event in the same layer, or of the same run in another layer, is reported. Corrections that correct each other in a circle are reported, not a crash.
  - `correct` refuses a new attribution without its run, workload, baseline or layers, events of another workload, a layer of another run, and a layer given twice.
  - The reason says what the layer was and is now ("was 4 CU-hours, now 3 CU-hours", "was removed"). A layer gone from the new attribution is reversed like its first accrual, and the reason ends with the reason given.
  - Figures are kept to six decimal places: the chain total, each correction and its result, and each difference.
  - A layer whose figure was withdrawn gets the new figure less what is left (within the tolerance). A change of exactly the tolerance is no change, in `correct` and in `reconcile`.
  - Zero is a valid accrual and a valid regression.
  - New layers come in layer order, layers without one last, then by name. Reversals come in layer order.
  - `reconcile` names each kind of layer difference (not expected, not in the ledger, Unknown in the ledger) and counts the matched runs. A run only in the ledger keeps the ledger's workload.
  - Problems name the run and the layer.
  - → `test_savings_corrections_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `savings/corrections.py` | 55 | `_STRENGTH` Unknown 2 to 3 | Equivalent: `_STRENGTH` is only used for membership and to find the weakest, and Unknown stays the weakest. |
| `savings/corrections.py` | 117 | `_state` of a chain with no head says removed | Equivalent: a chain has no head only when `_chains` reports a problem. `correct` and `reverse` refuse a ledger with problems, and `reconcile` replaces all three values for such a chain. |
| `savings/corrections.py` | 394 | `a[0] is not None` to `a[1] is not None`, and `b[0] is not None` to `b[1] is not None` | Equivalent: the branch before has already sent on every layer where only one side is Unknown, and the classification (`[1]`) is never None, so the test is still true exactly when both figures are known. |
