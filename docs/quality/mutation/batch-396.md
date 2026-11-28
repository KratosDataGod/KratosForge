# Mutation testing, batch 396

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The academy's certification change check: reading a vendor's study guide page, comparing it with a pack's registry, and the draft report it gives (`academy/guide_check.py`).

The run and the "after" run were both at `main` 9c7201ae4 (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 145 mutants, and the run ran all 145 of 145 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_academy_guide_check, about 1 second; it is the only test file that uses the module. Its page is a fixture in `sidecar/tests/`, and the new tests use a small made-up guide; nothing fetches a page.

Each pytest session got a fresh temporary folder of its own, as in batch 331. As in batch 387, a check used only for these runs refused any connection to anything but this machine; it wrote down none. After the run no folder in it was nested deeper than 40 levels. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (6, 6 and 3).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/guide_check.py` | test_academy_guide_check (+ test_academy_guide_check_mutation) | 145 of 145 (143 + 2), uncapped | 129 / 16 | **89.0%** | 129/143 → 143/143 | 0/2 → 2/2 | 145 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/guide_check.py**:
  - A page of exactly 4 MB is read, and one byte more is refused as "over 4 MB".
  - A guide without skill areas is refused by name.
  - Objectives exactly 0.6 alike are one reworded objective. The most alike are paired first, and no objective is paired twice.
  - The page's HTML is read as it shows: entities as their characters, a heading after an unclosed item, and a nested item's own words.
  - A removed skill area has no new id.
  - The report says which page and registry it compared, with "not shown" for no date. Its crosswalk marks a removed objective "(removed)".
  - A report cell is cut at 400 characters.
  - → `test_academy_guide_check_mutation.py`.

### Left

None.
