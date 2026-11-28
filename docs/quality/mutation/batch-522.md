# Mutation testing, batch 522

Two modules that had never been scored before, `compare_service.py` and `compare_store.py`. Both ran with `--kinds all`, so the score counts the boundary mutants (`<` to `<=`, `>` to `>=`) as well as the earlier kinds, and the table shows each part.
- Compare platforms for one workload (Preview). This covers the RPC that builds the comparison from what is already on this PC: the workloads in the metadata run log, this PC's runs of the current definition and scope, the Fabric runs, and the Databricks and Snowflake connections and their last reads (`compare_service.py`).
- The links and pricing scenarios kept on this PC. A Databricks job or a Snowflake query tag belongs to one workload. A workload takes at most 20 of each. At most 50 pricing scenarios are kept, and saving under a name that's already kept replaces that scenario (`compare_store.py`).

The run was at `main` be5ef815c (2026-11-28), with `sidecar/tools/mutate.py` and the method of earlier batches (`--cap 150 --seed 7 --kinds all`). Every mutant ran, because each module has fewer than 150. Test files were given to the tool as paths relative to `sidecar/`. The tests passed once on the unmutated copy first. Each pytest session got a new temporary folder, removed when the session ended, and no folder 40 or more levels deep was left. No `-k` filter was used. None of the test files reads `docs/`, `examples/` or `app/`, so nothing was deselected. No mutant's run timed out.

Before the "before" run, the tests passed in a copy of `sidecar/` on its own and against both modules as `ast.unparse` writes them, so the run can't score a false 100%. The new tests pass on DuckDB 2.0.0 and on sqlglot 30.23.0 and 30.27.0 too.

Two survivors of the "after" run in `compare_store.py`, on line 90, were then killed by a test added after that run. Both mutants change which message a clash gives. They were run again on their own with `--only`. Adding a test can't revive a killed mutant, so that re-run gives the final count.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `compare_service.py` | test_compare_service (+ test_compare_service_mut522) | 72 of 72 (72 + 0) | 57 / 15 | **79.2%** | 57/72 → 72/72 | none | 72 / 0 | **100%** | 0 |
| `compare_store.py` | test_compare_service (+ test_compare_service_mut522) | 81 of 81 (79 + 2) | 57 / 24 | **70.4%** | 57/79 → 76/79 | 0/2 → 2/2 | 78 / 3 | **96.3%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **compare_service.py**:
  - A run with a backfill is scoped to that backfill. A run without one is the regular load.
  - The store lives under this PC's data folder.
  - A run with no entity isn't a workload.
  - The newest run on this PC sets the definition and the scope.
  - A workload that has only run in Fabric takes its definition from that run.
  - A Fabric run keeps its own backfill scope.
  - Every run in the log is asked for (limit 100,000), and whatever is unavailable is passed on.
  - Connections list only Databricks connections with a host, and Snowflake connections with an account. They are labelled "profile · connection", falling back to each one's id.
  - The chosen connection is used. An unknown one falls back to the first.
- **compare_store.py**:
  - A link needs a workload written as plain text, up to 300 characters. A job's name is plain text, up to 200 characters.
  - Linking the same job or tag to the same workload again keeps one copy, with the new name.
  - A job id and a tag with the same text don't clash across platforms.
  - A job or tag linked to another workload is named in the message as a job or as a query tag.
  - A workload takes up to 20 jobs and up to 20 tags. The 21st is refused, and twenty jobs don't use up the tags.
  - Unlinking one job or tag keeps the others. Unlinking the last one drops the workload.
  - The store's folder is made together with its parents.
  - A saved scenario without a pricing version isn't offered.
  - Up to 50 scenarios are kept, and a name that's already kept can still be saved.
- These tests are in `test_compare_service_mut522.py`.

### Mutants left, and why

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `compare_store.py` | 48 | `raise` removed for "Say which job or query tag to link." | Equivalent: `job_ids([ref])` and `tags([ref])` either return the one reference or raise their own error, so nothing is ever found empty here. |
| `compare_store.py` | 93 | `platform == "databricks"` negated, and `==` swapped for `!=`, when choosing the limit | Equivalent: `MAX_JOBS` and `MAX_TAGS` are both 20. |
