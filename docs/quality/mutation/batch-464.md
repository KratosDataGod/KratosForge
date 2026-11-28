# Mutation testing, batch 464

One module not scored before, run with `--kinds all`:
- Ossie version compare: what changed between two versions of one semantic model (Ossie 0.2.0.dev0, TMSL or TMDL), per object, with what changed in each (type, expression through L2's canonical forms, format string, description, a role's permissions, a calculation item), security objects listed to look at, and a redacted copy for anything that leaves the local pane (`ossie_compare.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 221 mutants, so a sample of 150 was run. `main` hasn't changed this module since. The Ossie reader it calls has changed since (#3350 refuses a metric whose name is a number). So the test of a report with both notes now stands in for the reader leaving one item out, rather than using such a metric.

The test set was test_ossie_compare.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The first "after" run missed three survivors that came late in the "before" run, and left two that tests could reach after all; they got tests and were run again (two more runs), and the table counts all of them.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ossie_compare.py` | test_ossie_compare (+ test_ossie_compare_mutation_b) | 150 of 221 (148 + 2) | 107 / 43 | **71.3%** | 107/148 → 143/148 | 0/2 → 1/2 | 144 / 6 | **96.0%** | 5 equivalent, 1 not reached |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **ossie_compare.py**:
  - A JSON Ossie text over the size cap is refused, in whole MB ("2 MB, but ... at most 1 MB"). One exactly at the cap, one under it, and a large model.bim (which has its own cap) are read.
  - A dynamic format string is shown with its definition, and its length counts in the redacted line.
  - A removed description says "removed", and a changed one "changed".
  - A dialect only one version writes is no change when the forms match, and the report is still "compared", not failed. A dialect swapped for another names which went and which came.
  - A relationship whose ends change is matched by its name: one changed row, not one removed and one added.
  - A table that becomes a calculation group is one kind change, listed as a calculation group and flagged for security.
  - Two measures of one name in different tables are each read from their own table. A model-level metric (no home table) is read by its name.
  - The reason an expression wasn't compared names only the version outside the subset. A measure's form reads the measure names of its own version.
  - A compared report reads both sides, with their labels, and has no notes. A refused side isn't read. A failed report has no notes. A comparison that trips on a shape is a failed report with the reason.
  - A report notes both the objects compared by name only and the items the reader left out.
  - A redacted read error keeps only its type. A redacted report keeps a warning that isn't about a role.
  - → `test_ossie_compare_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `ossie_compare.py` | 81 | `return ""` to `return None` in `_refusal` (not an Ossie document) | Equivalent: the only caller tests it with `if refused:` and then returns its own `""`. |
| `ossie_compare.py` | 87 | `return ""` to `return None` in `_refusal` (a version the converter reads) | Equivalent for the same reason. |
| `ossie_compare.py` | 207 | `return out` to `return None` for an object the converter couldn't carry | Equivalent: the caller only tests the changes for truth, and an empty list and None both mean unchanged; nothing is stored. |
| `ossie_compare.py` | 114 | `kind not in (table, calculation group) or not raw` to `and` | Equivalent: the mutant also reads the raw objects of roles, measures and other kinds, but none of them has `columns` or `measures` that a table doesn't already give first (`setdefault` keeps the table's), and a table without a raw object has `{}`, which reads as empty. |
| `ossie_compare.py` | 215 | `safe[i] if i < len(safe)` to `<=` | Equivalent: `semantic_parity._role_changes_redacted` writes one line for each line `model_ossie._role_changes` writes (the same branches, line for line), so `i` is always below `len(safe)`. |
| `ossie_compare.py` | 332 | `a cls unsupported or b cls unsupported` to `and` when counting objects compared by name | Not reached: it needs a pair matched by key where one version's object was carried and the other's wasn't. Within one version a dropped object comes after the kept one of the same key, so it never pairs with the other version's kept object, and no input was found that makes the pair. |
