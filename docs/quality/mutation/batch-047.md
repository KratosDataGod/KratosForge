# Mutation testing, batch 47

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Shared schedules: the request each due time of a schedule several agents share becomes, its slot name and deadline, and the schedule id limit (`shared_schedules.py`).
- Dismissed best-practice findings: what a person chose to hide in their own Best practices panels, the 1,000-row limit and the checks on what is kept (`learn/dismissals.py`).
- Usage records: measured, billed or estimated usage in one unit each, and totals that never mix units (`engines/usage.py`).

The run was at `main` 04c276758 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%.

`test_learn_guides.py` holds the dismissal tests, and also one test that compares the shipped guides with `docs/`, which fails in the tool's copy of `sidecar/`. That file was run with `-k "dismiss or broken_file or policy_names"`, which keeps every dismissal test and leaves out the guide tests, so the docs test can't make every mutant read as killed. No other test run reads `docs/` or `examples/`. The `engines/usage.py` runs use `-k usage`, which keeps the usage tests of `test_engines.py` and `test_kudp_guardrails.py` and every test in the two usage files.

`sql_function_help.py` was also run (test_sql_function_help, 32 mutants) and scored 100% with its existing tests, so it needs no new test and isn't in the table.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `shared_schedules.py` | test_shared_schedules, test_agent_silent_failures (+ test_shared_schedules_mutation) | 32 of 32 (31 + 1) | 28 / 4 | **87.5%** | 28/31 → 31/31 | 0/1 → 1/1 | 32 / 0 | **100%** | none |
| `learn/dismissals.py` | test_learn_guides (dismissal tests) (+ test_learn_dismissals_mutation) | 39 of 39 (38 + 1) | 35 / 4 | **89.7%** | 35/38 → 38/38 | 0/1 → 1/1 | 39 / 0 | **100%** | none |
| `engines/usage.py` | test_usage_finite, test_kudp_guardrails, test_engines (usage tests) (+ test_engine_usage_mutation) | 35 of 35 (34 + 1) | 29 / 6 | **82.9%** | 29/34 → 34/34 | 0/1 → 1/1 | 35 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **shared_schedules.py**:
  - The schedule id limit is the one `agent.toml` enforces: an id of `MAX_ID` characters is accepted, and one more is refused.
  - Only an every-N-minutes schedule has an interval. A daily schedule that also carries `every_minutes` has none.
  - An agent at the deadline itself still creates the slot, and one past it doesn't.
  - The request file is the request as two-space-indented JSON, byte for byte.
  - → `test_shared_schedules_mutation.py`.
- **learn/dismissals.py**:
  - 1,000 dismissals are kept, and the next one pushes out the oldest.
  - An id is the first 16 hex characters of the SHA-256 of the rule, scope, place and line.
  - Rows that aren't dismissals (text, numbers, lists, an unknown scope) are left out, and the dismissals among them are kept.
  - A place name of exactly 512 characters is kept.
  - → `test_learn_dismissals_mutation.py`.
- **engines/usage.py**:
  - An unknown unit is refused and the message names the known ones.
  - Billed usage with no source is refused.
  - Zero usage is a real amount, not refused as negative.
  - A record as a dict has every field.
  - Totals round to six places.
  - A record can't be changed after its checks.
  - → `test_engine_usage_mutation.py`.

