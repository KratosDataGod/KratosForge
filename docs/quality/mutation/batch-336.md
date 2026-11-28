# Mutation testing, batch 336

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`). The module has no `<` or `>` comparisons, so it has no boundary mutants.
- What a local pipeline run does with each Data Factory activity type and expression function, and the page made from it (`orchestration/coverage.py`).

The run was at `main` 3424d8f33 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902. The module has 84 mutants, and the run ran all 84 of 84 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that calls `orchestration/coverage.py`: test_pipeline_coverage. It takes about a second, so none was left out. Two of its tests read files outside `sidecar/` (`docs/orchestration/coverage.md` and `zensical.toml`), so they fail in the tool's copy and were left out with `--deselect`: `test_pipeline_coverage.py::test_the_page_is_up_to_date` and `test_pipeline_coverage.py::test_the_page_is_in_the_docs_site`.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (1, 4, and the shared fixture for 5).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways, and with the two left-out tests in the repository. No mutant's run timed out.

The low "before" score is the left-out page test: it is what notices a changed example value (the function then "differs" instead of "runs") or a changed page. The new tests check the same facts without reading `docs/`: which functions don't give the reference's value and why, the status counts, and the page's rows and notes. When the evaluator changes a function's status, `NOT_RUNNING` in the new tests changes with the page.

## Results

| Module | Test files run | Mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `orchestration/coverage.py` | test_pipeline_coverage (two tests left out) (+ test_orchestration_coverage_mutation) | 84 of 84, uncapped | 19 / 65 | **22.6%** | 84 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **orchestration/coverage.py**:
  - Every reference example but `convertFromUtc` gives the reference's value. The functions that don't run are listed by status: 10 not checked, 8 refused, 1 differs. This catches a changed example value (45 of the survivors) and a function put in the wrong status.
  - A checked row keeps its example, the value it expected and what it gave. An example that stops with an error "fails".
  - `_canonical` writes whole floats as ints, inside lists and dicts too, with sorted keys. A page cell is JSON with `|` escaped, and empty for None.
  - The page counts each status, lists each category apart, shows "Value here" only for a function that differs or fails, and prints each note once under its function.
  - → `test_orchestration_coverage_mutation.py`.

### Left

None.
