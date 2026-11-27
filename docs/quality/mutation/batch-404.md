# Mutation testing, batch 404

One module not scored before, run with `--kinds all`:
- The central platform flows of the smoke test: the platform made in the test workspace from empty, or from a previous build's, used for a product, keys and a policy exception, and deleted again (`platformtest.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. The module has 127 mutants, so all were run. None of the test files reads `docs/`, `examples/` or `app/`, so nothing was left out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today, which hasn't changed it since ea988e99. The "after" figures come from a `mutate.py --only` run of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `platformtest.py` | test_platformtest, test_platformtest_cov2, test_cli_tenant_commands, test_smoketest (+ test_platformtest_mutation_b) | 127 of 127 (127 + 0) | 104 / 23 | **81.9%** | 104/127 → 125/127 | none | 125 / 2 | **98.4%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **platformtest.py**:
  - Each check belongs to its flow, 11 to 16, including flow 11's skip on an upgrade and the skipped flows 13 to 15 when the platform isn't ready.
  - The product step says what it registered.
  - A lakehouse's own SQL endpoint isn't a clash, but an endpoint or notebook named like another platform item is.
  - Only a failed "Platform tables" step waits for the connection to be added.
  - A platform that gives no schema version isn't ready.
  - An exception that is never listed as active fails flow 15, and the exception lasts a week.
  - Setup is given the workspace's name.
  - A run makes its results folder with its parents, or reuses it.
  - A run without its own id gets 12 hex characters, and the plan says the platform is kept only with `--platform-keep`.
  - A long delete error is cut to 200 characters.
  - → `test_platformtest_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `platformtest.py` | 204 | JSON `indent=2` to `3` | Equivalent, as in earlier batches: only the ledger's layout changes, and it is read back as JSON. |
| `platformtest.py` | 221 | a missing schema version read as `0` to `1` | Equivalent: this build's schema is version 5, so 0 and 1 both differ from it and the step fails either way. |
