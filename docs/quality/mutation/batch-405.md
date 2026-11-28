# Mutation testing, batch 405

One module not scored before, run with `--kinds all`:
- Academy's certification registry: which folders are packs, and the checks on each pack's certification, domains, weights, objectives and gaps before it can ship (`academy/registry.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. The module has 177 mutants, so 150 were sampled. `test_academy_registry.py::test_the_registry_agrees_with_the_coverage_page` and `::test_the_registry_agrees_with_the_curriculum_both_ways` read `docs/`, so they were deselected.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today, which hasn't changed it since ea988e99. The "after" figures come from a `mutate.py --only` run of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/registry.py` | test_academy_registry (two tests deselected), test_academy_quizlab, test_academy_guide_check, test_academy_dbsql_lab, test_academy_pipelab, test_academy_sqlprojlab (+ test_academy_registry_mutation_b) | 150 of 177 (143 + 7) | 133 / 17 | **88.7%** | 129/143 → 143/143 | 4/7 → 7/7 | 150 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/registry.py**:
  - Only a folder with a `pack.json` is a pack, and an id with no pack folder isn't one.
  - An unknown platform is refused, and a certification on one gives just that problem.
  - A domain with skill areas keeps them even if it lists objectives too.
  - A domain's weight is whole percents from 1 to 100, with min no more than max (0, 101, text and min above max are refused; 1 and 100 are kept).
  - An effective date needs no note.
  - A domain with a title but nothing under it is a problem, and so is a skill area with no objectives. A guide without skill areas isn't asked for their titles.
  - A bad capstone id and another platform's check are problems.
  - An objective plans at least one question.
  - A certification must name a file in the pack's own folder.
  - → `test_academy_registry_mutation_b.py`.

### Left

None.

Lane A scored the same module at the same time, in batch 390 (#3308). The two runs used different tests and neither saw the other's, so this page and batch 390's page each report their own run. Neither is a re-score of the other.
