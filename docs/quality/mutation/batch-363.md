# Mutation testing, batch 363

One module not scored before, run with `--kinds all`:
- Gathering what the excellence assessment reads from a workspace: its items, role assignments, capacity and usage, up to 50 lakehouses and 30 semantic models, and the reads that failed, then handing them to the engine (`excellence_service.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because several runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 118 mutants, so all were run. The container restarted during the batch; runs cut short were resumed with `--only` on the indexes not yet run, which keeps the same numbering.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. `main` has changed this module since ea988e99 (#3165 adds 77 lines of Git reads, then #3222, #3227, #3264 and #3275); the new tests pass against today's version.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence_service.py` | test_excellence_architecture, test_excellence_artifacts, test_excellence_capacity, test_excellence_catalog, test_excellence_models (+ test_excellence_service_mutation) | 118 of 118 (115 + 3) | 76 / 42 | **64.4%** | 75/115 → 114/115 | 1/3 → 3/3 | 117 / 1 | **99.2%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence_service.py**:
  - Fifty lakehouses are read and the 51st is not; exactly 50, all read, is an ok read. Thirty models are read and the 31st is not.
  - No lakehouse means no lakehouse read is listed, and the fourth unreadable lakehouse is counted as "and 1 more", with the error's type and text in the problem.
  - A `model.bim` that is missing raises, a BOM is decoded, and a model that can't be read is marked not read.
  - A failed item listing gives no items; fixture usage keys and times; the demo's SM-006 finding, assessment date and GOV-001 pass.
  - A workspace with no id keeps its own; `days` defaults to 30; no capacity means no capacity or usage read, and a capacity that isn't visible is a failed read; the usage evidence is named after the capacity when the id differs in case; the item inspector is passed to the engine; a workspace whose settings were read carries them as evidence, and one whose settings couldn't be read carries none.
  - → `test_excellence_service_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `excellence_service.py` | 53 | constant flipped in the demo model's `Customer.customer_sk` hidden flag | Equivalent: it is on the one side of the relationship, and SM-006 looks only at the fact side. |
