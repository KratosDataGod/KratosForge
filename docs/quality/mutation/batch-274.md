# Mutation testing, batch 274

Two meta modules not covered before, run with `--kinds all`:
- Promoting a project's metadata from one environment to the next, the releases an environment has, and rolling back to one (`meta/promote.py`).
- Running the project's own tests, the gate that custom code passes before it is promoted (`meta/testing.py`).

The run was at `main` ea988e99 (2026-11-21), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6, with each test set run with `-x`. Each module has fewer than 150 mutants (46 and 39), so all were run. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None reads `docs/`, `examples/` or `app/`, so nothing was left out. The per-mutant timeout was raised from 120 to 600 seconds with a wrapper that sets `mutate.TIMEOUT` (the tool is unchanged). No mutant's run timed out.

The first `meta/promote.py` run used test_meta only. Each survivor was run again with test_meta_service_cov3 and test_meta_service_edges added, which promote and roll back through the meta service. They killed none, so the "before" figures are from the first run.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/promote.py` | test_meta (+ test_meta_promote_mutation) | 46 of 46 (46 + 0) | 23 / 23 | **50.0%** | 23/46 → 46/46 | none | 46 / 0 | **100%** | none |
| `meta/testing.py` | test_meta_testing_cov2, test_meta (+ test_meta_testing_mutation) | 39 of 39 (39 + 0) | 26 / 13 | **66.7%** | 26/39 → 38/39 | none | 38 / 1 | **97.4%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/promote.py**:
  - Releases are this environment's only, newest first. Each says whether it is current, whether its files are kept here and whether its receipt checks out. No deployments means no releases.
  - A rollback needs something promoted first. A rollback to the current version, or to one never released, is refused, and so is one to a release whose files aren't kept. Nothing is written when it is refused.
  - A rollback records a new release that names the one it replaced, and returns it.
  - Test or Prod must be on the project's version before it is promoted further, and an environment with no deployments isn't.
  - → `test_meta_promote_mutation.py`.
- **meta/testing.py**:
  - A test gets `tmp_path` and keeps its own defaults.
  - Only references to a file that isn't there are missing. A missing function in a file that runs isn't.
  - Old project test modules are forgotten before a file is loaded.
  - The seconds for each test and for the whole run are the time taken, rounded to 3 and 2 places.
  - A failure with no test frame is placed at its deepest line.
  - → `test_meta_testing_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/testing.py` | 24 | `if TYPE_CHECKING:` negated | Equivalent: it only imports `Project` at run time too, for a type hint. |
