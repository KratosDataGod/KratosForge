# Mutation testing, batch 337

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- Mastery levels for each skill, talent and class, from a profile's verified evidence (`mastery/levels.py`, CM-03).

The run was at `main` 3424d8f33 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 244 of 244 mutants ran (229 point + 15 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_mastery and test_mastery_catalog_cov3, about 3 s a session. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mastery/levels.py` | test_mastery, test_mastery_catalog_cov3 (+ test_mastery_levels_mutation) | 244 of 244 (229 + 15) | 151 / 93 | **61.9%** | 148/229 → 222/229 | 3/15 → 14/15 | 236 / 8 | **96.7%** | 8 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **mastery/levels.py**:
  - test_mastery checks levels reached, not the record `counts` gives, so most survivors were fields of those records. Each row type's whole record is now checked: a lesson, a lab finished or passed (measured by a measurement or a `faster_than` check), an Academy exercise by class and mode (knowledge-only, local, verified on the platform; a challenge is advanced), one counted through its objectives with the pack from its id or its certification, a practice exam objective at 2 answers and at exactly 70%, Validate passes and checked fixes by name or row id, and Prove matches only where the skill names them.
  - Independence: at most 1 hint, the explanation unopened.
  - A cloud skill with a row verified on the platform can reach Master.
  - Each level at its edge: Adept at exactly 7 days between two sources' first passes (6 days is Journeyman); Expert needs two of your own artifacts (two practice sources don't count); Master at exactly 30 days from the first practice day, counted from the first and not the second.
  - A class's mastery needs 10 talents and 2 in every specialization, at the edges (9, 1 in one, none in a third); each talent's level, state (mastered from Adept), banked level and lock; the mastered and Journeyman counts.
  - Each row type's XP category, and the rules version given with every answer.
  - → `test_mastery_levels_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `mastery/levels.py` | 65 | `GRANDMASTER_DAYS = 180` → 181 | Equivalent: nothing reads it yet; Grandmaster can't be reached (no row carries a reviewer). |
| `mastery/levels.py` | 111 | `hints_used or 0` → `or 1` | Equivalent: 1 hint is still at most `INDEPENDENT_MAX_HINTS` (1). |
| `mastery/levels.py` | 123 (2) | `answered or 0` → `or 1`, `correct or 0` → `or 1` | Equivalent: an objective needs 2 answers, and 1 correct of 2 or more is under 70% either way. |
| `mastery/levels.py` | 170 | `d < first[...]` → `<=` | Equivalent: an equal date replaces a source's first pass with the same date. |
| `mastery/levels.py` | 172 | one source's spread `-1` → `-0` | Equivalent: Adept needs a spread of 7 days, which neither reaches. |
| `mastery/levels.py` | 179 | Master `lv = 6` → 7 | Equivalent: the level is capped at the ceiling, which is 6 at most. |
| `mastery/levels.py` | 226 | class mastery from level 0 | Equivalent: at level 0 it can only set the mastery it starts with, 0. |
