# Mutation testing, batch 457

One module not scored before, run with `--kinds all`:
- Realized savings (measure E) and net financial benefit (measure F): what a capacity's bill shows was not spent, from Azure Cost Management rows over two periods of the same length and a person's confirmation, and what is left of it after the costs of making the change (`savings/realized.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 346 mutants, so a sample of 150 was run. `main` hasn't changed this module since.

The test set was test_savings_realized.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. One survivor was left by the first "after" run that wasn't equivalent. It got a test and was run again; the table counts that second run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `savings/realized.py` | test_savings_realized (+ test_savings_realized_mutation_b) | 150 of 346 (138 + 12) | 119 / 31 | **79.3%** | 115/138 → 135/138 | 4/12 → 9/12 | 144 / 6 | **96.0%** | 6 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **savings/realized.py**:
  - A billing row that ends when it starts is refused. One key with two amounts is refused, even on another slot. One key twice with the same amounts counts once.
  - A row without its own end takes the cost period's end. The source is the export's own name. A row without a key is named by its place, counting from 1.
  - Mixed currencies name each row with its currency.
  - A SKU change without the capacity is refused with its own reason. A history row for another setting is refused.
  - A bill of exactly the old SKU's hours plus the tolerance fits, before the change and after it.
  - An "after" period that starts when the "before" period ends is allowed.
  - Missing days: one is named without "and more"; 11 list ten and say "and more"; 12 still list only ten.
  - A period under one CU hour still gives its rate.
  - A drop of exactly the tolerance is no drop, and a claim of exactly the tolerance isn't realized.
  - Net benefit: a cost without a figure is refused, and a bad category names the cost, counting from 1. A negative, NaN, infinite or True cost is refused with its own reason. A cost in another currency names the bill's currency.
  - An estimated net carries the cost's own assumptions. A net from 0 up to 1 is a benefit.
  - → `test_savings_realized_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `savings/realized.py` | 157 | `other is not row and other["key"] != name` to `or` for two rows on one slot | Equivalent: when `other` is the row itself its key is the name, so both are false. When it is another row with the same key, the amounts are the same (different ones were refused just before), so the mutant only reaches `continue`, and the row was already counted once. |
| `savings/realized.py` | 217 | `len(days) <= 10` to `<= 11` when collecting missing days | Equivalent: only ten days are shown, and "and more" needs more than ten. Collecting a 12th day changes neither. |
| `savings/realized.py` | 337 | `round(f["cu"], 6)` to `round(..., 7)` for the billed CU hours | Equivalent: each row's CU is a Measured figure, which `measurement.make` rounds to 6 places. So their sum has 6 places, less a float error far under the 7th, and rounding it to 7 places gives the same number. |
| `savings/realized.py` | 207 | boundary of `b > a` (to `b >= a`) when adding covered seconds | Equivalent: when `b == a` the span adds 0 seconds and moves the cursor to where it already is. |
| `savings/realized.py` | 203 | boundary of `r["start"] < end` (to `<=`) when picking rows that cover a period | Equivalent: a row that starts at the end gives a span of zero length, which adds nothing. |
| `savings/realized.py` | 203 | boundary of `r["end"] > start` (to `>=`) when picking rows that cover a period | Equivalent: a row that ends at the start gives a span of zero length, which adds nothing. |
