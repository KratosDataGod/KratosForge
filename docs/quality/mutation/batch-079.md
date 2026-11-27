# Mutation testing, batch 79

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Your own SQL templates on the Warehouse page: reading and writing a template file, the places a template is kept, and saving, importing, exporting and deleting one (`sql_templates_service.py`).
- Comments on notebook cells: adding, replying, editing, deleting and resolving, line ranges, and who wrote a comment (`comments_service.py`).
- Metadata runs on Fabric capacity: the bundle the app uploads, the runner notebook, its run parameters, and the run inside Fabric (`fabric_compute.py`).

The run was at `main` 1b1e47552 (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839, so each mutant ran without a bytecode cache, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run. One `sql_templates_service.py` mutant and two `comments_service.py` mutants loop for ever and are stopped at the time limit; as in earlier batches, they count as killed, before and after.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sql_templates_service.py` | test_sql_templates, test_sql_templates_edges, test_sql_queries_edges, test_sql_queries_mutation, test_file_replace (+ test_sql_templates_mutation) | 140 of 140 (132 + 8) | 112 / 28 | **80.0%** | 112/132 → 132/132 | 0/8 → 8/8 | 140 / 0 | **100%** | none |
| `comments_service.py` | test_notebook_comments, test_notebook_comments_edges, test_regex_anchors (+ test_notebook_comments_mutation) | 138 of 138 (131 + 7) | 121 / 17 | **87.7%** | 118/131 → 130/131 | 3/7 → 6/7 | 136 / 2 | **98.6%** | 2 equivalent |
| `fabric_compute.py` | test_fabric_compute, test_fabric_compute_edges, test_landing_zones, test_fips (+ test_fabric_compute_mutation) | 59 of 59 (59 + 0) | 49 / 10 | **83.1%** | 49/59 → 59/59 | none | 59 / 0 | **100%** | none |

No bug was found.

### What the survivors were, and the tests that now kill them

- **sql_templates_service.py**:
  - A title of 100 characters, a group or description of 200, and a template file of exactly 64 KB are taken; one more is refused, with the limit in the message.
  - A place with exactly 200 templates lists them all with no note; with 201 the note says so.
  - A file that is only header lines has no SQL, and a line of spaces right after the header is dropped.
  - A template saved without saying so isn't an endpoint template, and a file that can't be read is never listed as one.
  - Editing a template returns the file it wrote.
  - An import that isn't text, or is over 64 KB, is refused with its own message.
  - A repository place is labelled by its name, or by its folder when it has none, and one that no longer exists isn't a place.
  - A link inside a place to another template there is still refused.
  - An export is named after the template, then numbered from 2; new names are tried up to 999 and no further.
  - → `test_sql_templates_mutation.py`.
- **comments_service.py**:
  - A comment of 4,000 characters, a notebook's 500th thread and a thread's 200th comment are taken; one more is refused, with the limit in the message.
  - A thread keeps the first 500 characters of its lines, and ids are 12 hexadecimal characters.
  - A range of one line, or of all of them, is taken; one from line 0, one that runs backwards, or one of `true` or text is refused.
  - Signed out with nobody known to the sign-in, a comment is written as this PC.
  - Resolve resolves unless told otherwise.
  - This PC's id is made together with its folder, and a second first comment waits for the first one's id.
  - → `test_notebook_comments_mutation.py`.
- **fabric_compute.py**:
  - The bundle's `config.json` is sorted and indented, so the same settings always give the same bundle hash.
  - The runner notebook is nbformat 4.5.
  - Run parameters send empty text for no entities and no backfill.
  - One entity runs alone unless cascade is asked for, and the backfill asked for reaches the engine.
  - The results folder is made with its parents, and the results file is indented.
  - A temporary copy that can't all be removed never fails a finished run.
  - → `test_fabric_compute_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `comments_service.py` | 110 | `return ""` dropped (returns `None`) | Equivalent: this PC's id is only used as a condition and in `f"pc:{pc}" if pc else ""`, where `None` and `""` act the same. |
| `comments_service.py` | 127 | `time.monotonic() > deadline` to `>=` | Equivalent: the wait stops at the deadline either way. The two differ only if the clock reads exactly the deadline, and then the next read, 20 ms later, is past it. |

