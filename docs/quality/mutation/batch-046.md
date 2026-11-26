# Mutation testing, batch 46

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Opportunity records: what an opportunity must have to be made, its id, and what `problems` reports (`opportunities/model.py`).
- Landing Copy job requests: the checks on what a team asks to copy, and the Copy job definition built from it (`orchestration/copyjob_build.py`).
- Keeping a table family from DefaultReader: which role definitions are changed automatically and which are refused (`optimize/roles.py`).

The run was at `main` 043423b6d (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. None of the tests run reads `docs/` or `examples/`, so none was left out.

The new tests are pure functions over values they build themselves: they write no files and use no network, registry or timing.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `opportunities/model.py` | test_opportunities (+ test_opportunities_model_mutation) | 40 of 40 (40 + 0) | 29 / 11 | **72.5%** | 29/40 → 40/40 | none | 40 / 0 | **100%** | none |
| `orchestration/copyjob_build.py` | test_landing_copy_requests (+ test_copyjob_build_mutation) | 51 of 51 (50 + 1) | 44 / 7 | **86.3%** | 44/50 → 50/50 | 0/1 → 1/1 | 51 / 0 | **100%** | none |
| `optimize/roles.py` | test_optimize_roles, test_optimize_service_edges (+ test_optimize_roles_mutation) | 66 of 66 (66 + 0) | 65 / 1 | **98.5%** | 65/66 → 66/66 | none | 66 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **opportunities/model.py**:
  - Each refusal in `make` is checked on its own: an unknown category, action or confidence, an effort or a risk outside low, medium and high, and no evidence, artifacts, validation steps or rollback.
  - The id is `opp-` and 16 hex digits.
  - Evidence without a source is reported by `problems`.
  - → `test_opportunities_model_mutation.py`.
- **orchestration/copyjob_build.py**:
  - A request that isn't an object is refused with its own message, not a crash.
  - Exactly 50 tables are accepted, and 51 are refused.
  - A bad table name is quoted at the start of the message, and a blank one says "(blank)".
  - The connection name is trimmed and kept to 200 characters.
  - The job never retries, and its part is JSON indented by two spaces.
  - → `test_copyjob_build_mutation.py`.
- **optimize/roles.py**:
  - A DefaultReader with no Path list, or with two, is refused and left for an admin to change.
  - → `test_optimize_roles_mutation.py`.

