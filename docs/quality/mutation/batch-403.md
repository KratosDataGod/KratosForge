# Mutation testing, batch 403

One module not scored before, run with `--kinds all`:
- The unattended tenant smoke test: the automated parts of the ten flows, with the platform flows and the warehouse check when asked for, and the summary a person ticks the rest on (`smoketest.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. The module has 137 mutants, so all were run. None of the test files reads `docs/`, `examples/` or `app/`, so nothing was left out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today, which has changed it since ea988e99 (#3312). The "after" figures come from a `mutate.py --only` run of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `smoketest.py` | test_smoketest, test_cli_tenant_commands, test_warehouse_livecheck (+ test_smoketest_mutation_b) | 137 of 137 (137 + 0) | 109 / 28 | **79.6%** | 109/137 → 136/137 | none | 136 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **smoketest.py**:
  - Each step records what it found: the engine's versions, Microsoft's client, a token received, how many workspaces and items, the support bundle's check and the live test's outcome. A workspace with no items says "none".
  - Each step belongs to its flow (the SQL and Kusto tokens to flow 2, the item list to flow 3, the bad name to flow 4, the platform outcome to flow 11).
  - A run doesn't run in Fabric unless asked, and gets a 12-character id.
  - An empty token fails its step, and a name check that lets the bad name through fails flow 4.
  - The warehouse check runs only when it is asked for and Fabric signed in. Its step records the cleanup, and any case that is wrong or differs fails its findings.
  - The plan names the platform flows only when they are asked for.
  - The summary notes only the steps that don't fail their flow, names the workspace and whether it ran in Fabric, and mentions what was left behind or kept only when something was.
  - → `test_smoketest_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `smoketest.py` | 312 | JSON `indent=2` to `3` | Equivalent, as in earlier batches: only the layout of a JSON file that is read back as JSON changes. |
