# Mutation testing, batch 29

Three modules, none touched by an open pull request:
- App Studio's patch operations, the one way a specification changes: the 200-operation limit, field paths, the inverses that undo each operation, the refusals and the names cut short in their messages (`appstudio/patch.py`).
- Meaningful diffs of a table's settings and of a notebook: how values are written, which changes are logic, the order changes are listed in, list items matched by identity, and a notebook cell's position and diff (`meta/diff.py`).
- Schema drift review: what a run expects from its source, the added, dropped and retyped columns, the config edits that settle them, Delta's type-widening rules and the schema-change kinds a write reports (`meta/drift.py`).

The run was at `main` aa6b5668a (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 28 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. `test_meta.py` was left out of the `meta/drift.py` run because it takes about 40 seconds a mutant and only one of its tests mentions drift.

None of these modules writes files, and the new tests use stand-in entities, no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `appstudio/patch.py` | test_appstudio, test_appstudio_patch_edges, test_appstudio_edit, test_appstudio_edit_edges, test_appstudio_pbir (+ test_appstudio_patch_mutation) | 135 of 135 | 113 / 22 | **83.7%** | 132 / 3 | **97.8%** | 3 equivalent |
| `meta/diff.py` | test_meaningful_diffs, test_dataops_edges (+ test_meta_diff_mutation) | 134 of 134 | 108 / 26 | **80.6%** | 133 / 1 | **99.3%** | 1 equivalent |
| `meta/drift.py` | test_meta_drift_edges, test_schema_evolution, test_dataops, test_dataops_edges (+ test_meta_drift_mutation) | 150 of 214 | 122 / 28 | **81.3%** | 143 / 7 | **95.3%** | 7 equivalent |

One `meta/drift.py` mutant timed out before and after, and was counted as killed. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **appstudio/patch.py**:
  - A change of exactly 200 operations being applied, and one more refused.
  - A field that is empty or not text needing a field, and a page setting its own fields.
  - An id already in use being refused by the operation itself, before the whole specification is checked.
  - The inverse running last operation first, and each touched id listed once.
  - A set recording the old value, or none for a field that wasn't there, and a value copied rather than shared.
  - A page whose components are only used inside it being removable, and one whose component is used from outside not.
  - A move to another page, within a page and within a list, each with its inverse, and a move to a bad index putting the entry back.
  - Long operation names, field paths, page ids and ids cut at 20, 80 and 64 characters in messages.
  - → `test_appstudio_patch_mutation.py`.
- **meta/diff.py**:
  - Values written the way the file writes them: booleans in lower case, lists in brackets.
  - A contract with no keys reading as an empty list, and keys or columns not repeated as settings.
  - A setting set, removed or changed, nested settings by their full path, and a table replaced by a value.
  - Operational settings not being logic changes, only in their own section.
  - Logic changes listed before descriptive ones, and column changes in column order.
  - List items matched by name, column, python and expression, by table and business key, by lookup name or dimension, and a dedupe step, with numbered items for ones without an identity.
  - A reorder reported only when nothing else changed.
  - Entities in name order, unchanged ones left out, and unreadable ones said to be unreadable.
  - A notebook cell's id or position, its joined source, its one-line context diff, its old type, and a reorder reported once.
  - → `test_meta_diff_mutation.py`.
- **meta/drift.py**:
  - The source named by its entity, then its files, then its table, and the layer's policy used when the table has none.
  - A step, or a table without a source, having no differences.
  - Added, dropped and retyped columns, a dropped key flagged, a renamed column read by its contract name, and a column a derived expression reads not being new.
  - Reading from another entity only reporting a missing column this entity uses.
  - Section bounds, columns removed, retyped and added in place, comments kept, and a policy or rename replacing the old line.
  - Widening for unsigned integers to decimals and doubles, and 16-bit integers to decimals.
  - Every kind of schema change listed once, in order, with its breaking flag.
  - → `test_meta_drift_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `appstudio/patch.py` | 36 | `entry_kind == "page"` → `!=` | Equivalent: `components` is already refused for every kind by the first test. |
| `appstudio/patch.py` | 97 | `[:64]` → `[:65]` for an id already in use | Equivalent: an id in use passed validation, so it is never longer than 64 characters. |
| `appstudio/patch.py` | 144 | `or` → `and` | Equivalent: the only entry with no list is the application, whose kind isn't in the list either. |
| `meta/diff.py` | 92 | sort default `0` → `1` | Equivalent: only changes without a column use the default, and they all get the same one. |
| `meta/drift.py` | 30 | `base_type()` used the other way round | Equivalent: a Polars type and its instance find the same entry. |
| `meta/drift.py` | 107 | the new columns section's start moved by one | Equivalent: the lines written are the same. |
| `meta/drift.py` | 128, 140 | a new section's end moved by one | Equivalent: an empty slice at either end inserts at the same place. |
| `meta/drift.py` | 186, 188 | `or 38` → `or 39` | Equivalent: Polars gives a decimal with no precision 38, so the fallback is never read. |

### Real bugs found

None.

### Not in this batch

Batch 28's modules.

