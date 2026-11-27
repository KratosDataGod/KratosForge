# Mutation testing, batch 96

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Naming the program that holds a file open: the Restart Manager session, its sizes and retries, the error paths, and the message for exactly three programs (`file_locks.py`).
- Platform support levels: each level's name and what the next one needs, the evidence a strength may ask for, which evidence counts as a source, and the docs table's rows (`platforms.py`).
- Run receipts: the store's messages, the list's order and limit, cell defaults, number formats, and the Markdown and HTML reports (`receipts.py`).

The run was at `main` 5f0b593e2 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `file_locks.py` has 74 mutants and `platforms.py` 81, so all of them ran; `receipts.py` has 177, so 150 were sampled.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_platforms.py::test_the_docs_table_is_the_codes` reads `docs/` and `test_side_by_side.py::test_examples_in_the_repository_are_the_generated_notebooks` reads `examples/`, so both were deselected. `test_prove.py`, `test_release.py`, `test_rollback.py`, `test_regex_anchors.py` and `test_policy_fail_closed.py` also call `receipts.py`; they were left out of its set to keep each mutant's run short.

The Restart Manager is only on Windows, so the `file_locks.py` tests answer its calls with a stand-in for `rstrtmgr.dll` that reads and writes the same ctypes objects, and they swap the module's `sys`, not the process's.

No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `file_locks.py` | test_file_locks (+ test_file_locks_mutation) | 74 of 74 (73 + 1) | 28 / 46 | **37.8%** | 28/73 → 71/73 | 0/1 → 1/1 | 72 / 2 | **97.3%** | 2 equivalent |
| `platforms.py` | test_platforms (+ test_platforms_mutation) | 81 of 81 (77 + 4) | 62 / 19 | **76.5%** | 61/77 → 76/77 | 1/4 → 2/4 | 78 / 3 | **96.3%** | 3 equivalent |
| `receipts.py` | test_receipts_edges, test_receipts_env, test_prove_service_edges, test_scorecard_cost, test_advisor, test_side_by_side, test_wizard (+ test_receipts_mutation) | 150 of 177 (148 + 2) | 107 / 43 | **71.3%** | 107/148 → 147/148 | 0/2 → 2/2 | 149 / 1 | **99.3%** | 1 equivalent |

One defect was found and is recorded as a strict `xfail` in `test_receipts_mutation.py`: the export's redaction note lowercases each choice, so "This PC's name and hardware" reads "this pc's name and hardware". Fixed: the note now lowercases only each choice's first letter, and the `xfail` marker is gone.

### What the survivors were, and the tests that now kill them

- **file_locks.py**:
  - The library is loaded from System32 only.
  - The session key buffer, the process info's name fields and its field order match Windows' sizes and layout.
  - The one file is registered with no applications or services, and the session is ended with the id it started with.
  - A list that grows is asked for again with room for it, an empty need still leaves room for one, and it is asked three times at most.
  - A failed start is neither used nor ended; a failed registration or listing names no one and ends the session.
  - A program without a name is named by its process id, and each name is given once, in order.
  - A missing Restart Manager names no one, and off Windows the list is empty even when asked directly.
  - Exactly three programs are all named with nothing after them, and four end with "and 1 more".
  - → `test_file_locks_mutation.py`.
- **platforms.py**:
  - Each level's number and name, what the next one needs, and level 5 with nothing next.
  - The evidence each strength asks for is never more than a platform can verify.
  - Platforms and features can't be changed once made, and a feature without evidence names the evidence that counts.
  - Only real evidence is listed as a source, evidence for an area that doesn't apply is ignored, and weaker evidence doesn't lower stronger.
  - The SQL targets are those the SQL profiles know, and pipeline activities are counted by classification.
  - The docs table's rows, including "nothing" when no level is next.
  - → `test_platforms_mutation.py`.
- **receipts.py**:
  - The Markdown and HTML reports line by line, including the title, both tables, the code block and the 12 packages shown.
  - A receipt without a notebook is titled "Notebook", and a verdict without text or level is shown alone.
  - Inputs with only files are listed, and a missing file hash is blank.
  - Cells without ids or kinds are numbered from one and count as code.
  - Canonical JSON keeps text as UTF-8.
  - Whole numbers get thousands separators, and 1e15 is written as an exponent.
  - The store's messages name a receipt by its first 12 characters.
  - The list is newest first with undated receipts last, gives each verdict, and shows 50 unless asked for more.
  - → `test_receipts_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `file_locks.py` | 76 | the needed count's start value 0 to 1 | Equivalent: Restart Manager only writes this value, and it is read only after a call has set it. |
| `file_locks.py` | 76 | the returned count's start value 0 to 1 | Equivalent: it is set to the buffer's length before every call. |
| `platforms.py` | 170 | `>` to `>=` in keeping the strongest evidence | Equivalent: equal evidence replaces itself with the same value. |
| `platforms.py` | 190 | the levels walked from index 1 to 0 | Equivalent: level 0 needs no area, so it is always met and is already the starting level. |
| `platforms.py` | 201 | `<=` to `<` in capping a strength | Equivalent: at equal rank both branches return the same evidence. |
| `receipts.py` | 54 | `allow_nan=False` to `True` | Equivalent: the check before it already refuses NaN and infinity in every dict, list and tuple, and keys that aren't text. |


