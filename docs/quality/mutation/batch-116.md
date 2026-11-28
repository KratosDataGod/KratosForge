# Mutation testing, batch 116

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Excellence assessment profiles: the severity weights, a profile's fingerprint, what a profile must hold, and a stored profile read back (`excellence/profiles.py`).
- The rule guides beside a best-practice finding: reading a best-practices page into guides, its examples, building them from every page, and the guide for a pipeline warning code (`learn/guides.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter was used. `test_learn_guides.py::test_the_shipped_guides_are_the_docs` reads `docs/`, so it was left out with `--deselect`.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/profiles.py` | test_excellence, test_excellence_catalog, test_excellence_scoring_mutation (+ test_excellence_profiles_mutation) | 102 of 102 (96 + 6) | 88 / 14 | **86.3%** | 87/96 → 96/96 | 1/6 → 5/6 | 101 / 1 | **99.0%** | 1 not killed |
| `learn/guides.py` | test_learn_guides (+ test_learn_guides_mutation) | 56 of 56 (54 + 2) | 40 / 16 | **71.4%** | 40/54 → 50/54 | 0/2 → 2/2 | 52 / 4 | **92.9%** | 4 not killed |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/profiles.py**:
  - The severity weights are 10, 5, 3, 1 and 0, and the baseline uses them.
  - A fingerprint is 16 hex digits, changes with the profile, and is the same for a profile read back.
  - A profile can't be changed once made.
  - A dimension may weigh 0, or less than 1.
  - The coverage thresholds at their edges: a provisional coverage of 0 and a rated one above 1 are refused; equal thresholds, and a rated coverage of 1, are fine.
  - A stored profile keeps its severity overrides and disabled controls.
  - → `test_excellence_profiles_mutation.py`.
- **learn/guides.py**:
  - Text before the first rule heading isn't a guide.
  - A title line with one or two tags leaves the area or the importance empty, and a later bold line changes nothing.
  - A note on a Before or After mark is kept as a comment.
  - Building reads every page and gives the guides in rule order.
  - The shipped guides are read only when none are given.
  - → `test_learn_guides_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `excellence/profiles.py` | 59 | weights' `abs(sum - 100) > 1e-9` to `>=` | Not killed: it differs only for a sum exactly 1e-9 away from 100, which the slack is there to absorb; no test was written for that one value. |
| `learn/guides.py` | 121 | four mutants of the `__main__` block (`+ "\n"`, `sys.argv[1]`, `indent=1`, `ensure_ascii=False`) | Not killed: this is the build script run by hand to write the shipped `rule_guides.json` (as `test_state_writes.py` notes); a test would have to run it and rewrite that file. |

