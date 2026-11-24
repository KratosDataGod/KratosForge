# Mutation testing, batch 392

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- The Academy exam engine: the question bank and its checks, practice exam sessions, their clock, scores, review and storage (`academy/exam.py`, ACD-013).

The run was at `main` 9c7201ae4 (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 364 of 364 mutants ran (350 point + 14 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_academy_exam_service and test_academy_exam, about 9 s a session. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used. In both runs three mutants' runs timed out and count as killed: all three make `build`'s pick loop (lines 308 to 310) never end.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/exam.py` | test_academy_exam_service, test_academy_exam (+ test_academy_exam_mutation) | 364 of 364 (350 + 14) | 317 / 47 (3 of the killed timed out) | **87.1%** | 309/350 → 348/350 | 8/14 → 14/14 | 362 / 2 (3 timed out) | **99.5%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/exam.py**:
  - Each question check at its bound: a single answer with 3 and 6 options (7 is too many), a multiple answer with 4 and 8 (9 too many) and with all but one option right, an ordering of 6 steps (7 too many), version 1 (0 and "1" aren't), and four live questions an objective. Duplicate option ids, an option that isn't an object (a problem, not a crash), and also_accept naming the answer itself.
  - The bank: a folder named unlike a pack isn't one, drafts are left out unless asked and retired questions always, cases come from cases.json or are none, unreadable cases are the one problem, and a case needs a proper id and an object.
  - Sessions: a count of 0 still asks one question, 300 minutes is allowed, a random seed is below 2^31, every session call returns the session, the clock keeps milliseconds, and view numbers from 1 and says how many to pick.
  - Scores round to the nearest percent, overall and by objective, and an exam whose questions all left the bank scores 0. A drill can't check a question that left the bank. Review keeps flags and other accepted orders; review and evidence need a finished exam with a result.
  - history skips rows that aren't question records, and the store lists only the pack's finished exams and counts each exam's questions.
  - → `test_academy_exam_mutation.py`.

### Survivors judged equivalent

- `rank = {"wrong": 1, "right": 3}` (line 297): the ranks only order questions for a retake (unseen 0, wrong 1, right 2), and 3 gives the same order.
- `live.get(objective, 1) < MIN_PER_OBJECTIVE` (line 253): the default is read only for an objective with no live question, and 0 and 1 are both under 4. The message reads the count separately, and a test pins it at 0.
