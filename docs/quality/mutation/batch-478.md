# Mutation testing, batch 478

One module not scored before, run with `--kinds all`:
- CSV text for the eight savings reports (executive summary, workspace, capacity, developer contribution, monthly statement, audit history, estimated versus verified, capacity readiness), each built from the ledger's layers and the realized, pricing, projection and validation results. Classifications are never added together, Unknown is an empty cell, and a text cell that starts like a formula is protected (`savings/exports.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 307 mutants, so a sample of 150 was run.

The test set was test_savings_exports.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The realized results in the new tests are the smallest dicts the module's checks take, so a report's capacity, period and currency can be set for each case.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `savings/exports.py` | test_savings_exports (+ test_savings_exports_mutation_b) | 150 of 307 (149 + 1) | 98 / 52 | **65.3%** | 98/149 → 143/149 | 0/1 → 1/1 | 144 / 6 | **96.0%** | 6 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **savings/exports.py**:
  - A missing value sorts last, and the rest sort as text. A layer is marked corrected only once a correction follows it.
  - The workspace report counts runs and layers; two layers of one run are one run and two layers. The audit history sorts two chains of one run time by run and layer, and shows each layer's optimization, "No" for removed and its figure after.
  - Money rows round to 6 places and sort by currency within a classification.
  - A realized result that isn't one is named by its place (result 1, result 2). The after period comes before the figure's period. The capacity is the billing one, then the result's, then the figure's.
  - Capacity value must be a value_runs result with priced runs, and a projection must be a recurring.project result.
  - The latest validation wins whatever order they come in.
  - The summary counts no realized results as 0, and notes a Measured one as from a bill.
  - Capacity rows carry the capacity, the counts and each period, and sort by measure, then period start. An unrealized result notes its reason, or the figure's.
  - The monthly statement notes priced runs with the scenario, and orders currencies of one class by currency.
  - A validation without runs has 0 runs and 0 layers. Sources without an optimization sort by source before classification.
  - Readiness puts a verified optimization after Estimated CU avoided, with its note, and takes the realized result whose after period ends last; of two ending together it keeps the first. Without an after start, its note is only the method.
  - A row of the wrong width is refused.
  - → `test_savings_exports_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `savings/exports.py` | 143 | `root optimization_id or (...)` to `and` | Equivalent: an optimization layer is named by its optimization id, so both give the same id, and other layers have none. |
| `savings/exports.py` | 327 | `_MEASURES` Headroom 5 to 6 | Equivalent: no capacity or monthly row is Headroom, and it stays last anyway. |
| `savings/exports.py` | 433 | the chain depth starts at 1, not 0 | Equivalent: every depth moves by 1, so the order is the same. |
| `savings/exports.py` | 488 | `_EVIDENCE` "not validated" 5 to 6 | Equivalent: it then ties Realized savings. The tie goes by class (Measured first), then by the order rows were added, and both put "not validated" first. |
| `savings/exports.py` | 488 | `_EVIDENCE` Realized 6 to 7 | Equivalent: it then ties Headroom, which is Unknown. A Measured realized row sorts first by class, and an Unknown one keeps the order rows were added, realized first. |
| `savings/exports.py` | 488 | `_EVIDENCE` Headroom 7 to 8 | Equivalent: it stays last. |
