# Mutation testing, batch 278

One module that had no score before, run with `--kinds all`:
- The profile service: what the app calls for the profile, the hooks that record a verified accomplishment, the report and its files, goals, and the profile picture (`progress_service.py`).

The run was at `main` ea988e99 (2026-11-21), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. From `test_side_by_side.py`, only `test_a_matching_side_by_side_run_earns_proof_forged_and_a_differing_one_nothing` was run, because it is the one test there that records a profile row. None of the tests run reads `docs/`, `examples/` or `app/`. The module has 142 mutants, so all were run. No mutant's run timed out.

The first run used only test_profile, test_learn_fix_checked and test_progress_service_edges, and scored 78 / 142 (54.9%). The "before" figures are from a second run that also has test_ranks (the picture), test_profile_goals_impact, test_learn_service_mutation, test_prove_service_cov3 and the side-by-side test, which reach this module too.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The last two boundary mutants (line 172) were run again with test_progress_service_edges and test_progress_service_mutation after a test for them was added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `progress_service.py` | test_profile, test_learn_fix_checked, test_progress_service_edges, test_ranks, test_profile_goals_impact, test_learn_service_mutation, test_prove_service_cov3, test_side_by_side (1 test) (+ test_progress_service_mutation) | 142 of 142 (137 + 5) | 112 / 30 | **78.9%** | 111/137 → 137/137 | 1/5 → 5/5 | 142 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **progress_service.py**:
  - A picture is judged to the byte. 33 bytes is the shortest header taken and 32 is refused. 512 KiB is taken and one byte more is refused. Every byte of the width and height counts. The check answers `False`, not just something false.
  - A picture that is too large is refused with "at most 512 KB".
  - Removing a picture that isn't there is fine.
  - The proof and Learn hooks return the achievements they earned.
  - The report, its Markdown file and its PDF default to 90 days without achievements. A report of 1 day or of 3,660 days is made.
  - The profile and its report leave draft lessons out.
  - Marking a goal done defaults to done.
  - A backfill runs once: a second one adds nothing, even with new proofs.
  - Evidence is newest first, and a row without XP shows 0.
  - The view says whether someone is signed in.
  - The exported profile is indented by one space.
  - → `test_progress_service_mutation.py`.

### Left

None.
