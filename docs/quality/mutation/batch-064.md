# Mutation testing, batch 64

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The development roadmap and personal goals: tracks, milestones, suggestions and goals in the person's own words (`roadmap.py`).
- Environment profiles: which modules a notebook imports, whether each is optional, and where each stands on this PC and in Fabric (`envprofile.py`).
- The Direct Lake semantic model written as TMDL: table names, relationships, measures and columns (`semantic.py`).

The run was at `main` bcc8076fa (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/`, `examples/` or `app/`, and no `-k` filter was used.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `roadmap.py` | test_profile_goals_impact (+ test_roadmap_mutation) | 68 of 68 (67 + 1) | 49 / 19 | **72.1%** | 48/67 → 67/67 | 1/1 → 1/1 | 68 / 0 | **100%** | none |
| `envprofile.py` | test_envprofile_edges, test_notebook_magics, test_prove_service_edges, test_receipts_env (+ test_envprofile_mutation) | 108 of 108 (108 + 0) | 98 / 10 | **90.7%** | 98/108 → 106/108 | none | 106 / 2 | **98.1%** | 2 equivalent |
| `semantic.py` | test_cloud, test_tmdl, test_model_date_table, test_model_dictionary, test_model_directlake, test_model_ossie (+ test_semantic_mutation) | 100 of 100 (99 + 1) | 89 / 11 | **89.0%** | 88/99 → 99/99 | 1/1 → 1/1 | 100 / 0 | **100%** | none |

One bug was found, in `envprofile.py`, and filed as #2773: a `%pip` line below one or more blank lines was reported on the first blank line rather than on its own line, because `\s*` at the start of the pattern also matches newlines. Only the line number shown was wrong. #2776 fixed it, and `test_a_pip_line_below_blank_lines_is_reported_on_its_own_line` now passes as a plain test. A run on the fixed module gives the same score: 106 of 108, with the same two equivalent mutants.

### What the survivors were, and the tests that now kill them

- **roadmap.py**:
  - The roadmap reports version 1 and up to 12 goals; goal text is kept to 300 characters.
  - The many-notebooks milestone needs five notebooks and the fixes milestone three fixes.
  - A goal in the person's own words gets a `g-` id with 12 hex characters.
  - An empty goal (none, empty or only whitespace) is refused, and the thirteenth goal is refused after twelve.
  - Marking an unknown goal is refused, and an own goal can be marked done and then not done.
  - Cleaning a stored goals list cuts the id to 80 characters and the dates to 40, keeps `done_at` only for own goals, and keeps at most 12 goals.
  - Learn links name only shipped lessons, with their titles (empty when a lesson has none), and a milestone needing an unshipped lesson is coming soon.
  - At most three suggestions, each the next step in a track already started, and never a step already chosen as a goal.
  - Lab and exercise needs are counted against the shipped labs and the exercises across shipped lessons.
  - → `test_roadmap_mutation.py`.
- **envprofile.py**:
  - A cell that isn't Python counts as parsed, with no imports.
  - A dynamic import after a blank first line is on line 2.
  - A bare `except`, or one that catches `ImportError`, `ModuleNotFoundError` or `Exception` (alone or in a tuple), makes the imports in its `try` optional; one that catches anything else doesn't.
  - A module imported at the top level and inside a function is required, whichever comes first; one imported only inside functions stays optional.
  - This PC isn't ready when a required module is missing even though every cell parsed, and is ready when only an optional module is missing.
  - A distribution with no version is left out of the runtime.
  - → `test_envprofile_mutation.py`.
- **semantic.py**:
  - A quoted name is unquoted only when it starts and ends with a quote.
  - Only an `scd1` or `scd2` entity with a surrogate key is a dimension.
  - A lookup joins the dimension's surrogate key; a lookup to a table with no surrogate key, without a column name, or to an unknown entity adds nothing.
  - A dimension never joins itself by its own key, and a key name of a table that isn't a dimension makes no relationship.
  - A column named like a dimension's key joins it, unless a lookup already joined from that column; a lookup from another column doesn't stop it.
  - Only a `date` column (in any case) gets the Short Date format.
  - `definition.pbism` is JSON indented two spaces.
  - → `test_semantic_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `envprofile.py` | 77 | `line = 0` to `line = 1` | Equivalent: the starting value is used only when the node imports nothing, and then there are no names to record it with. |
| `envprofile.py` | 109 | `return False` dropped (returns `None`) | Equivalent: the result is only used as a condition, where `None` and `False` act the same. |

