# Mutation testing, batch 480

One module not scored before, run with `--kinds all`:
- The live test against a real Azure Databricks workspace (KF-DBX-LIVE): this PC's checks (Python, the Databricks CLI and its version, the profile), then sign-in, the warehouse and cluster read and refused unless tagged for testing, Unity Catalog, queries, an error and a cancel on the warehouse, a cell and a cancel on the cluster, a missing warehouse and a bad token. It can stop what it started, and writes result.json and summary.md without a token (`dbx_livetest.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 236 mutants, so a sample of 150 was run.

The test set was test_dbx_livetest, test_dbx_livetest_cov2, test_cli_tenant_commands and test_remote_text_lines.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. Every workspace, warehouse, cluster, Databricks Connect session and CLI in the new tests is a fake (test_dbx_livetest's, or the test's own); nothing reaches Databricks or the network.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `dbx_livetest.py` | test_dbx_livetest, test_dbx_livetest_cov2, test_cli_tenant_commands, test_remote_text_lines (+ test_dbx_livetest_mutation_b) | 150 of 236 (148 + 2) | 118 / 32 | **78.7%** | 117/148 → 146/148 | 1/2 → 2/2 | 148 / 2 | **98.7%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **dbx_livetest.py**:
  - Each passed check says what it found: the cluster's runtime, node type, workers, state and auto-termination; the SQL session's warehouse and its state; the catalogs; the query's rows and time; the Databricks Connect version; and the HTTP status of the refused bad token.
  - A cluster without a worker count shows 0 workers. The catalog check names the first five.
  - The missing warehouse asked for has a 16-character id, and a run's own id has 12. result.json is written with two-space indents.
  - Stopping says what it asked for, under "Stop the warehouse" or "Terminate the cluster", and once everything is stopped the summary has no "Started by this run" lines.
  - A session that opens but can't say its warehouse fails the open, and the checks after it are skipped.
  - The slow statement runs on a daemon thread. The wait for the cancel ends at its deadline.
  - The summary names the workspace, says how to stop what is left (or that stopping failed), and cuts titles at 200 and ids at 100 characters.
  - This PC's Python is shown as major.minor.micro. The CLI is asked its version as text, with a 30-second limit, and a missing patch number reads as 0.
  - → `test_dbx_livetest_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `dbx_livetest.py` | 72 | `version[:2] >= MIN_PYTHON` to `version[:3]` | Equivalent: MIN_PYTHON has two parts. A three-part version whose first two equal them compares greater, so `>=` holds either way; when the first two differ they decide. |
| `dbx_livetest.py` | 110 | `got[:2] < MIN_CLI` to `got[:3]` | Equivalent: the same reasoning for MIN_CLI; `<` is false either way when the first two parts are equal. |
