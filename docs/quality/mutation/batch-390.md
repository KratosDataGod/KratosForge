# Mutation testing, batch 390

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- The Academy certification registry: each pack's published objectives, read and checked (`academy/registry.py`, ACD-001).

The run was at `main` 293fb38da (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 177 of 177 mutants ran (170 point + 7 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_academy_readiness, test_academy_guide_check, test_academy_registry and test_academy_exam, about 8 s a session. Two tests of test_academy_registry read `docs/academy/` outside `sidecar/` and were deselected (test_the_registry_agrees_with_the_coverage_page, test_the_registry_agrees_with_the_curriculum_both_ways). About a dozen lab and exercise files read packs through the registry; the slow ones (test_academy_exercises, over 120 s; test_academy_quizlab and test_regex_anchors, about 15 s each) and the lab ones were left out to keep each mutant's run short. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/registry.py` | test_academy_readiness, test_academy_guide_check, test_academy_registry (2 deselected), test_academy_exam (+ test_academy_registry_mutation) | 177 of 177 (170 + 7) | 159 / 18 | **89.8%** | 155/170 → 170/170 | 4/7 → 7/7 | 177 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/registry.py**:
  - A pack is a folder with a pack.json, named like a pack id even when a pack.json is there; a lesson is a folder with a lesson id.
  - An unknown platform raises, and is the certification's one problem.
  - A domain with neither skill areas nor objectives has no groups. A domain needs both a title and skill areas, and a skill area both a title and objectives.
  - Weight ranges: 1 and 100 are allowed, 0 and 101 aren't, and a range needs both ends. One planned question is enough.
  - An effective date needs no note. Capstone ids and another platform's check are problems. A certification file named with a folder is a problem.
  - → `test_academy_registry_mutation.py`.
