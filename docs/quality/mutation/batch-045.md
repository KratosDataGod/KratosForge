# Mutation testing, batch 45

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Windows time zones: the UTC offset read from a zone's display name, and the order the zones are listed in (`time_zones.py`).
- Team guidance: which of two policies at the same level is named, the order of team notes, and the note count, note length and unknown-id limits at their edges (`learn/team.py`).
- Copy job definitions: the reason a job can't run on this PC, naming each end's connection type (`orchestration/copyjob.py`).

The run was at `main` 2d26dcabb (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. None of the tests run reads `docs/` or `examples/`, so none was left out.

The new tests are pure functions over values they build themselves: they write no files and use no network, registry or timing. The time zone tests pass their own reader, so they run the same on any OS.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `time_zones.py` | test_time_zones, test_schedule_locks_and_lists (+ test_time_zones_mutation) | 31 of 31 (31 + 0) | 28 / 3 | **90.3%** | 28/31 → 31/31 | none | 31 / 0 | **100%** | none |
| `learn/team.py` | test_learn_team (+ test_learn_team_mutation) | 47 of 47 (44 + 3) | 41 / 6 | **87.2%** | 41/44 → 44/44 | 0/3 → 3/3 | 47 / 0 | **100%** | none |
| `orchestration/copyjob.py` | test_workspace_copy_jobs, test_landing_copy_requests (+ test_copyjob_mutation) | 34 of 34 (34 + 0) | 33 / 1 | **97.1%** | 33/34 → 34/34 | none | 34 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **time_zones.py**:
  - An offset is hours times 60 plus minutes, for a half-hour, a three-quarter-hour and a negative zone.
  - Plain "(UTC)" and a name with no offset are offset zero.
  - Zones sort by offset, with UTC between the minus and plus zones and a one-minute difference keeping its order.
  - → `test_time_zones_mutation.py`.
- **learn/team.py**:
  - Two policies setting the same level keep the first one's name.
  - Exactly 200 notes are kept with no problem, and 201 are cut to 200 with one problem.
  - A note of exactly 2,000 characters is kept whole, and one more character cuts it.
  - An unknown id is quoted to 40 characters in its problem.
  - Notes given as text are a problem, never read as a definition's `{"value": ...}`.
  - Notes for one id follow policy name order, then policy id order.
  - → `test_learn_team_mutation.py`.
- **orchestration/copyjob.py**:
  - The reason names the end's connection type, then its type, then "not set".
  - → `test_copyjob_mutation.py`.

