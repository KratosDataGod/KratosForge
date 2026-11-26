# Mutation testing, batch 60

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Whether a notebook should run on this PC or in Fabric, and the reasons given for it: the compatibility check, the proof, a side-by-side run and the Fabric job CU (`advisor.py`).
- Pairing a VS Code extension with the app: the code shown to the person, how long a request waits, how many may wait, and the token a paired extension gets (`editor_pairing.py`).
- Help while typing in a notebook cell: the name under the cursor, the call being typed and the first syntax error in a cell (`intellisense.py`).

The run was at `main` a1f7002ff (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `intellisense.py` has 188 mutants, so 150 were sampled; the other two modules were run in full.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/` or `examples/`, and no `-k` filter was used. The `editor_pairing.py` "after" run left out `test_editor_channel.py` (about 27 seconds) to keep the run short; adding a test file can only kill more mutants, so the score with it is the same or higher.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `advisor.py` | test_advisor, test_compute_check_ast, test_risk_service_edges (+ test_advisor_mutation) | 127 of 127 (126 + 1) | 101 / 26 | **79.5%** | 101/126 → 126/126 | 0/1 → 1/1 | 127 / 0 | **100%** | none |
| `editor_pairing.py` | before: test_editor_channel, test_editor_pairing_edges, test_editor_service_edges; after: test_editor_pairing_edges, test_editor_service_edges (+ test_editor_pairing_mutation) | 99 of 99 (96 + 3) | 83 / 16 | **83.8%** | 81/96 → 96/96 | 2/3 → 3/3 | 99 / 0 | **100%** | none |
| `intellisense.py` | test_intellisense, test_intellisense_edges (+ test_intellisense_mutation) | 150 of 188 (140 + 10) | 112 / 38 (4 timeouts counted as killed) | **74.7%** | 108/140 → 134/140 | 4/10 → 8/10 | 142 / 8 (4 timeouts counted as killed) | **94.7%** | 8 equivalent |

Two bugs were found, both in `intellisense.py`, and both are now fixed. When a syntax error covers more than one line, the end column reported for it was clamped as if the error ended on its first line (#2743). And a `%%sql` cell whose first line holds only spaces was checked as Python, because only empty lines were skipped before looking for the cell magic, so it got a false syntax error (#2746; the line 165 mutant, first listed as equivalent, found it in review). Both are fixed, in #2745 and #2747, and `test_intellisense_mutation.py` checks the right answer for each; the #2746 test also kills the line 165 mutant.

### What the survivors were, and the tests that now kill them

- **advisor.py**:
  - Up to three cells are listed by number, more are summed up as "and N more", and a reason with no cell has no cell prefix.
  - When the check can't read a cell, the reason says it is unknown whether the notebook runs on that side, names the cell, and is marked unknown.
  - The Fabric job CU is unknown when its classification is unknown or it has no value, with the recorded reason or "not recorded"; the label is shown in lower case, or the classification when there is no label.
  - The proof's text falls back from its text to its verdict to "Proved.".
  - The exact wording of a side-by-side run for one table, for two, for none and for a failed run.
  - A side-by-side run of other code, or of code that wasn't recorded, is shown as context with its verdict or "no verdict"; when a proof exists, no side-by-side run is used.
  - A failed proof asks the person to look at its result and prove the notebook again.
  - → `test_advisor_mutation.py`.
- **editor_pairing.py**:
  - The three ways to keep a pairing and their lengths in seconds, how long a request waits, and how many may wait at once.
  - The code shown to the person is two groups of three letters or digits, and request, pairing and token ids have their exact form and length.
  - A folder that is missing, empty, not text, or holds a control character is refused.
  - The name an extension gives is cut to 80 characters and cleaned of control characters, with a default when it gives none.
  - A fourth request is refused while three wait, and a denied request frees a place.
  - A request is still waiting at exactly 120 seconds and gone one second later.
  - Each answer to a request is collected once; a keep that isn't one of the three, and a request that is unknown or already answered, are refused.
  - A token that is missing, not text or of the wrong form is refused, and a token works until the moment it expires.
  - The saved pairings must be a list, and entries that aren't records with a text hash are dropped.
  - Ending every pairing writes no file when none existed.
  - → `test_editor_pairing_mutation.py`.
- **intellisense.py**:
  - The name under the cursor at the start and end of a line; a doc is cut at 1500 characters; a callable object is shown as a function.
  - Comments and triple-quoted strings are skipped when finding the call being typed; the innermost open call and the keyword being typed are found.
  - Past the positional parameters, a `*args` parameter is the active one; the return type is shown when there is one; a doc in call help is cut at 400 characters.
  - Line numbers after a `%%python` cell magic, on the first line or after blank lines, and a line magic replaced so the error is reported on the right line.
  - The exact span of an error on one line and over several lines, of a null byte, and of an error at column 0.
  - The check is compiled without the module's own future imports, so `yield` in an annotation is an error.
  - A message is capitalised, with "Invalid syntax" when there is none.
  - → `test_intellisense_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `intellisense.py` | 87 | `nl < 0` to `nl <= 0` | Equivalent: the search for the end of a comment starts at the `#`, so it can't find a line break at index 0. |
| `intellisense.py` | 171 | `count("\n") + 1` to `+ 0` | Equivalent: the cell magic line is then kept and replaced by `pass`, so the reported line numbers come out the same. |
| `intellisense.py` | 178 | `max(0, ...)` to `max(1, ...)`, `- 4` to `+ 4`, `- 4` to `- 5` | Equivalent (3 mutants): these change only the spaces after `pass` at the end of a line, which never change a syntax error. |
| `intellisense.py` | 192 | `col + 2` to `col - 2` | Equivalent: with no end offset, the larger of the two is `col + 1` either way. |
| `intellisense.py` | 193 | `end_line == err_line` to `!=`, `end_col <= col` to `end_col < col` | Equivalent (2 mutants): the end column is always past the start column, so that part of the test is never true. |

These rows describe the code before #2745 and #2747; on today's code line 171 is gone and the line 193 mutants are killed by the existing tests.

