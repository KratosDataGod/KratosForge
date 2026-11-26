# Mutation testing, batch 156

A boundary re-score of two modules first scored in batches 6 and 7, before `mutate.py` made boundary mutants (batch 36), run again with `--kinds boundary` only:
- The warehouse run: a transaction wrapping nothing, retries at the deadline, progress at exactly its time, and what a statement that changes no rows says (`warehouse.py`, batch 6).
- Notebooks outside the kernel's cell thread: the rich output cap, spill files, previews, display ids, nested runs and the waits of a poll and an interrupt (`notebook.py`, batch 7).

The run was at `main` 4ddd3662 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902 (sample cap 150, seed 7, `--kinds boundary`), with the test files of batches 6 and 7, given as paths relative to `sidecar/`. Each module has fewer than 150 boundary mutants, so all of them ran. No test needed `--deselect`, and the tests first passed once on the unmutated copy.

The new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, on DuckDB 1.5.6 and 2.0.0. They fake the clock where a wait ends at exactly its deadline, and stop the wait themselves after a few reads, so a mutant that would spin fails instead of hanging. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `warehouse.py` | test_warehouse_connect, test_warehouse_edges, test_warehouse_precondition, test_warehouse_queries, test_parse_errors_plain, test_safe_parse_readers, test_sql_worker_inprocess, test_warehouse_mutation (+ test_warehouse_boundary) | 22 of 22 | 12 / 10 | **54.5%** | 20 / 2 | **90.9%** | 2 equivalent |
| `notebook.py` | test_notebook_files, test_notebook_files_edges, test_notebook_folders, test_notebook_shelf_edges, test_ipynb_round_trip, test_output_cap, test_outputs_hardening, test_notebook_display_edges, test_notebook_history, test_notebook_places, test_notebook_resources, test_notebook_inputs, test_notebook_run, test_notebook_mutation (+ test_notebook_boundary) | 41 of 41 | 8 / 33 | **19.5%** | 36 / 5 | **87.8%** | 5 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **warehouse.py**:
  - A BEGIN TRAN and COMMIT with only a blank line between are still taken off. A BEGIN and a COMMIT with nothing between are sent as they are.
  - Six digits of a second need no note. A clone named schema.table names no database.
  - A first line of exactly 80 characters is shown whole.
  - A retry whose wait would end exactly at the deadline isn't made.
  - Progress is said at exactly PROGRESS_SECONDS.
  - A statement that changes no rows says it changed 0.
  - → `test_warehouse_boundary.py`.
- **notebook.py**:
  - An update or an output that lands exactly on RICH_CHARS is shown. Spill text that exactly fills the room keeps the file open. A spill file or folder written exactly at the cutoff is kept.
  - Exactly MAX_DISPLAY_IDS display ids are kept. A notebook exactly MAX_FOLDER_DEPTH folders deep is allowed, and a run one short of MAX_RUN_DEPTH runs.
  - A DuckDB result of exactly PREVIEW_ROWS rows, or one whose total isn't known, has a known total.
  - A poll with no wait answers at once, and an interrupt stops waiting at exactly its deadline, with the clock not moving.
  - %timeit with one run has no spread. A duration of exactly 1 s, 1 ms or 1 µs is shown in that unit. Counts of exactly a billion, a million and ten thousand.
  - Previews: six columns have no "...", exactly 100 items are shown from their repr, and a repr of exactly 80 characters is shown whole.
  - A watch of exactly 200 characters, breakpoints on lines 1 and 1,000,000, an attached image of exactly ATTACHMENT_IMAGE_MAX characters, an error chain of exactly 20, and a file with a DEL character (127) that keeps its escapes.
  - %fs head with only a path reads 64 KB, and %fs says "1 path" and "2 paths". %%writefile with one name writes it.
  - → `test_notebook_boundary.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `warehouse.py` | 735 | `0 < int(precision)` to `<=` | Equivalent: a precision of 0 is falsy, so `precision and ...` stops before the comparison. |
| `warehouse.py` | 654 | `rowcount > 0` to `>=` for the affected count | Equivalent: a count of 0 adds 0. |
| `notebook.py` | 1905 | `seconds <= 0` to `<` for a cell's deadline | Equivalent: `not seconds` already takes 0. |
| `notebook.py` | 2050 | `end > 0` to `>=` for a quoted %run name | Equivalent: `find` starts at 1, so it is never 0. |
| `notebook.py` | 2302 | `len(value) > _PREVIEW_ITEMS` to `>=` for text | Equivalent: text of exactly that length, cut to that length, is the same text. |
| `notebook.py` | 2326 | `len(text) <= 76` to `<` for a preview of items | Equivalent: the text ends with "...", so cutting it to 73 characters and adding "..." gives the same text. |
| `notebook.py` | 2715 | `minor > nbformat_minor` to `>=` | Equivalent: an equal minor version is set to the same value. |


