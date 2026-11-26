# Mutation testing, batch 126

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Learn content: listing modules and labs, and the checks that say what is wrong with the content before it ships (`learn/content.py`).
- Entra ID groups per domain: the plan, the scripts shown and saved, linking a group by object id, and the directory reads (`central/entra_service.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter was used. `test_learn_content.py::test_docs_pages_a_module_names_exist` reads `docs/`, so it was left out with `--deselect`. Of `test_regex_anchors.py`, which scans the whole engine, only `test_academy_and_learn_ids_ending_in_a_line_break_are_refused` calls `learn/content.py`, and of `test_estate.py` only `test_a_graph_error_is_one_short_line_without_a_token` calls `central/entra_service.py`, so only those tests were given, by their node ids.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. The new tests pass with DuckDB 1.5.6 and 2.0.0. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `learn/content.py` | test_learn_content, test_learn_content_edges, test_learn_labs, test_profile_goals_impact, test_profile_learn, test_ranks, one test of test_regex_anchors (+ test_learn_content_mutation) | 114 of 114 (111 + 3) | 105 / 9 | **92.1%** | 104/111 → 111/111 | 1/3 → 3/3 | 114 / 0 | **100%** | none |
| `central/entra_service.py` | test_entra_graph, test_entra_groups, one test of test_estate (+ test_entra_service_mutation) | 121 of 121 (120 + 1) | 104 / 17 | **86.0%** | 104/120 → 118/120 | 0/1 → 1/1 | 119 / 2 | **98.3%** | 2 not killed on Linux |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **learn/content.py**:
  - Drafts are listed unless left out, and a folder not named like a module isn't listed.
  - Raw HTML is reported on its own line number, counted from 1.
  - Minutes run from 1 to 120: 0 and 121 are refused, 1 and 120 are not.
  - A `faster_than` check without a baseline says it needs one, and names no file.
  - An exercise without an id is named with a question mark.
  - → `test_learn_content_mutation.py`.
- **central/entra_service.py**:
  - An unknown domain is refused before the catalog is asked.
  - Subdomains are left out of the groups and the saved script unless asked for.
  - A script shown is never saved.
  - CSV and PowerShell files carry a byte order mark, and a shell script doesn't.
  - Protected cells come with a note, and no note comes without them.
  - Linking refuses a group id that isn't an object id, before the catalog is called.
  - Workspaces are sorted by name.
  - Two characters are enough to search, and each Graph read gets 30 s.
  - Graph's error keeps 200 characters of its text.
  - Every suggested name is checked, fifteen at a time.
  - → `test_entra_service_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `central/entra_service.py` | 124 | `newline="\n" if ext == "sh" else None`: the test negated, and `==` to `!=` | Not killed on Linux: there `newline=None` also writes `\n`, so the file is the same. On Windows the files differ, and `test_csv_and_powershell_files_carry_a_byte_order_mark_and_shell_scripts_unix_line_ends` checks each file's line ends there. |

