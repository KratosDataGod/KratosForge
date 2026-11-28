# Mutation testing, batch 431: academy_exam_service.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`). This is the first mutation run of this module. It has one boundary mutant.
- The engine calls behind Academy > Assessments (`academy_exam_service.py`): the home tab, starting a practice exam, a domain diagnostic or an objective drill, answering, flagging, a drill's check, the honest clock (heartbeat and pause, and the exams running only in this engine's memory), finishing once, review, and the words a stored result is shown with.

It has 99 mutants, and this run ran all 99 of 99, uncapped.

The run was at `main` e49cddbebe (2026-11-24), with the method of batch 379. Each pytest session had its own fresh `--basetemp`, removed at the end of the session; the nesting-check log stayed empty for the run and both re-runs. The kills don't end in a streak of near-identical times. Three kills chosen at random (mutants 0, 57 and 82) were re-run by hand without `-x`: each failed real tests (3, 3 and 1). No mutant timed out.

Sessions are files in the test's `tmp_path`; nothing starts a process or makes a network call. The set is test_academy_exam_service, which runs in about 2 seconds (with the new file, about 3).

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy_exam_service.py` | test_academy_exam_service; after: + test_academy_exam_service_mutation | 99 of 99 (98 + 1), uncapped | 79 / 20 | **79.8%** | 79 / 98 (80.6%) → 98 / 98 (100%) | 0 / 1 → 1 / 1 | 99 / 0 | **100%** | none |

The 20 survivors were re-run with the new file added (`--only`): 19 were killed and 1 was left. That one, mutant 95 (an objective with no questions counted as having 1 on the home tab), was not equivalent: every objective of the bundled packs has questions, so no test had one without. A test was added with a bank that has none for one objective, and mutant 95 was re-run alone with the file: killed. The 27 new tests in `tests/test_academy_exam_service_mutation.py` check:
- `_practice`: the published exam's question count and minutes only when each is a whole number, the defaults (50 questions, 100 minutes) otherwise, and never more questions than the bank has; `_code` for a Fabric pack and for another platform, with and without a title; and `_title` for each kind, a domain the certification doesn't have, and a drill of three objectives against one of four;
- the running set: kept on the service, an exam in it only while it is in progress and running, and a finished exam never marked running;
- the home tab's fields, each objective's question count (none when it has none), only the pack's own exams by status, and the practice length capped by the bank;
- starting: a diagnostic's two questions per objective and its domain kept, a drill's objectives kept and named, a running exam saved, and each refusal's words;
- opening a finished exam (its stored result) and an unfinished one (its clock running again), answering (None clears), flagging (on by default), a saved check, heartbeat and pause with their clock fields, on a finished exam (nothing written) and on an untimed one;
- finishing and review, and the result's words: from the certification version the exam used, none when that version has changed or a domain is gone, and the items left out.

No bugs were found.

### Left

None.
