# Mutation testing, batch 473

One module not scored before, run with `--kinds all`:
- Academy readiness: where a learner stands on each exam objective, worked out from the profile's evidence rows as six separate measures (knowledge, guided, independent, troubleshooting, platform verified, retention), with counts per domain and the exercises to do next (`academy/readiness.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. test_academy_pyspark_lab (about 95 seconds a run) and test_academy_lab (which only calls readiness through the service) were left out of the test set; that can only lower the "before" score. The module has 89 mutants, so all were run.

The test set was test_academy_readiness and test_academy_databricks_pack.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The tests for what comes next use exercises of their own, because the dp-700 pack publishes only three, so a limit of 3 or 4 can't be told apart with it.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/readiness.py` | test_academy_readiness, test_academy_databricks_pack (+ test_academy_readiness_mutation_b) | 89 of 89 (86 + 3) | 66 / 23 | **74.2%** | 66/86 → 82/86 | 0/3 → 2/3 | 84 / 5 | **94.4%** | 5 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/readiness.py**:
  - Retention counts a pass again exactly 7 days after the first, and not 6.
  - A challenge passed with one hint is still independent.
  - A row for another certification is left out.
  - Only a platform-verified pass counts as platform verified, and it is the best class.
  - Knowledge points round to two places (1/3 is 0.33), and a missing count is 0, not 1.
  - Each domain counts its shown and practised objectives, and the other domains count none.
  - What comes next: three by default; the domain with the fewest shown objectives first, even when its id sorts later; only exercises for that objective; and an exercise already passed (its evidence id is `pack/exercise`) is skipped.
  - → `test_academy_readiness_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `academy/readiness.py` | 108 | `int(detail.get("hints_used") or 0)` to `or 1` | Equivalent: 0 and 1 hints are both within INDEPENDENT_MAX_HINTS (1), so no hints read as 1 is still independent. |
| `academy/readiness.py` | 123 | `exercises.STRENGTH.get(c, -1)` to `get(c, 0)` for the best class | Equivalent in practice: a class outside STRENGTH never reaches a row (exercise classes are checked), and even then the only change is a tie with `knowledge_only`, which the set's order decides, so no test can pin it. |
| `academy/readiness.py` | 146 | `shown_by_domain.get(..., 0)` to `get(..., 1)` | Equivalent: each domain starts one higher, so the order by count is the same. |
| `academy/readiness.py` | 147 | `MEASURES[1:]` to `MEASURES[0:]` for the exercises already done | Equivalent: it adds the knowledge measure, whose keys are `correct` and `answered`, never an exercise id. |
| `academy/readiness.py` | 156 | `len(picks) >= limit` to `>` | Equivalent: the loop may add more picks after the limit, but they go at the end and `picks[:limit]` drops them. |
