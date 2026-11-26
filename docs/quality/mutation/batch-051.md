# Mutation testing, batch 51

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Engine capabilities: the levels, evidence and rules every engine descriptor must meet, and whether an engine can run a cell (`engines/capabilities.py`).
- Capability registry: where each platform-neutral capability runs on every engine, and the table in the capabilities page (`capability_registry.py`).
- Model data dictionary: the semantic model's tables, columns and measures as an Excel CSV, and the preview of an edited one (`model_dictionary.py`).

The run was at `main` 7c92b98b5 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. `test_capability_registry.py` has one test that compares `docs/capabilities.md` with the code. That page isn't in the copy the tool runs in, so the two capability runs leave it out with `-k "not docs_page"`. The new registry tests check the same table against a fixed set of engines instead. No other test reads `docs/` or `examples/`. `test_model_practices.py` was not used, because it takes over a minute.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/capabilities.py` | test_engines, test_capability_registry, test_engine_conformance (+ test_engine_capabilities_mutation) | 69 of 69 (69 + 0) | 57 / 12 | **82.6%** | 57/69 → 69/69 | none | 69 / 0 | **100%** | none |
| `capability_registry.py` | test_capability_registry, test_engines (+ test_capability_registry_mutation) | 39 of 39 (39 + 0) | 25 / 14 | **64.1%** | 25/39 → 39/39 | none | 39 / 0 | **100%** | none |
| `model_dictionary.py` | test_model_dictionary, test_model_dictionary_service_edges (+ test_model_dictionary_mutation) | 81 of 81 (79 + 2) | 57 / 24 | **70.4%** | 57/79 → 78/79 | 0/2 → 2/2 | 80 / 1 | **98.8%** | 1 equivalent |

One real bug was found: #2728. A data dictionary under the size limit with one cell over 131072 characters raises `csv.Error` from Python's `csv` module, instead of the `ValueError` every other bad file gets. A strict xfail in `test_model_dictionary_mutation.py` pins it until it's fixed.

### What the survivors were, and the tests that now kill them

- **engines/capabilities.py**:
  - `Support.as_dict` lists the level, note and evidence.
  - Unknown evidence, an unknown language, and asking for an unknown capability are each refused.
  - A bad location, platform or billing is refused on its own, one at a time.
  - `Support` and `EngineDescriptor` are frozen.
  - A live-verified remote engine may list an unsupported language without being Preview.
  - `check_cell` names what is planned only when something is, and gives the right reason for native, simulated and translated.
  - → `test_engine_capabilities_mutation.py`.
- **capability_registry.py**:
  - With two fixed engines, the capabilities table is checked line by line: the "(Preview)" mark, and which notes go under "Known differences". A partial remote claim's note is listed. A native note isn't, and neither is a partial claim without a note.
  - A table with no notes has no "Known differences" part.
  - `listing()` returns every capability.
  - Requirements are listed only where the capability runs.
  - `Capability` is frozen.
  - → `test_capability_registry_mutation.py`.
- **model_dictionary.py**:
  - The size limit is exactly 5,000,000 characters: a file of that size is read, and one more character is refused.
  - Only the first line decides the delimiter, and a tie between commas and semicolons means comma.
  - Contract types map to Delta types, and a missing type is a string.
  - Each of the counts is checked.
  - A hidden column goes out as "Yes".
  - Import numbers rows as Excel numbers them and counts rows that don't change anything.
  - A table row is found by its kind or by the "(table)" marker.
  - Tables and measures are never hidden, column types are kept, and a measure over an undescribed column says only "Sum of" the column.
  - → `test_model_dictionary_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `model_dictionary.py` | 43 | `c["type"] or e.columns.get(...)` to `and` | Equivalent: `mesh.merged_catalog` fills each column's `type` from `e.columns.get(name, "")`, so the fallback reads the same value the catalog already holds. |

