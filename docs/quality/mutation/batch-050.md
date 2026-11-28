# Mutation testing, batch 50

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. None of these modules has a `<` or `>` compare, so every mutant is a point mutant.
- App Studio's starting templates: blank, sales overview and operations status (`appstudio/templates.py`).
- App Studio's local preview, made-up numbers for a specification (`appstudio/sample.py`).
- The local store of imported usage files (`usage/store.py`).

The run was at `main` aac8ff6ba (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. None of the tests run reads `docs/` or `examples/`, so none was left out, and no `-k` filter was used.

The new tests build their own values and write only under pytest's `tmp_path`: no network, registry or timing.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `appstudio/templates.py` | test_appstudio_service (+ test_appstudio_templates_mutation) | 69 of 69 (69 + 0) | 23 / 46 | **33.3%** | 23/69 → 69/69 | none | 69 / 0 | **100%** | none |
| `appstudio/sample.py` | test_appstudio_service, test_appstudio_connections (+ test_appstudio_sample_mutation) | 70 of 70 (70 + 0) | 52 / 18 | **74.3%** | 52/70 → 70/70 | none | 70 / 0 | **100%** | none |
| `usage/store.py` | test_usage_reader_store_edges, test_usage_import (+ test_usage_store_mutation) | 25 of 25 (25 + 0) | 23 / 2 | **92.0%** | 23/25 → 25/25 | none | 25 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **appstudio/templates.py**:
  - The existing test checks that every template is a valid specification for every target. Valid layouts can still overlap or leave holes, so 42 of the survivors were grid positions and sizes.
  - Every page of the sales and operations templates now has to tile the 12-column grid with no overlap and no gap, and the card and chart rows are pinned.
  - An unknown target is refused by name.
  - For a KratosForge app, interactions the target can't express are dropped, and a Power BI report keeps them. No shipped template has such an interaction yet, so the test adds a drillthrough to a test-only template.
  - → `test_appstudio_templates_mutation.py`.
- **appstudio/sample.py**:
  - A sample number is the first 4 bytes of the sha256 of its parts, in the range 1,000 to 99,999. A slicer on another field shrinks it by 0.15 to 0.39. Both are checked against the formula, not against stored numbers.
  - A field's column is the text inside its brackets, and an unknown column gets five numbered categories.
  - A card without a field shrinks once by the selection. A card with a field sums its categories and shrinks by a slicer on another field. A card without measures shows 0 and no measure name.
  - A chart without a field shows one value, like a card.
  - A category list longer than 12 is cut to 12 rows. No built-in list is longer than 12, so the test adds one.
  - → `test_appstudio_sample_mutation.py`.
- **usage/store.py**:
  - Imports are listed newest first, and one without an import time sorts last instead of breaking the list.
  - → `test_usage_store_mutation.py`.

