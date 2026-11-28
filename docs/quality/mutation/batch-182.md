# Mutation testing, batch 182

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Reading and writing TMDL on this PC: the reader's indents, objects and properties, the writer's layout, the read-back check and how two models are compared (`tmdl.py`).

Batch 12 sampled 150 of this module's 484 mutants then (82.7% before, 96.7% after). The module has grown to 504 mutants, and this run ran all 504 of 504.

The run was at `main` ad1ac4cf8 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that calls `tmdl.py`. Together they take about 5 seconds, so none was left out. No `-k` filter or `--deselect` was needed: no test in the set reads `docs/`, `examples/` or `app/`.

Every run passed `--basetemp` with a folder of this lane's own to pytest. During this push, one mutant in another lane left a folder in the shared `/tmp/pytest-of-root` nested too deep for pytest's cleanup. While that folder was there, every pytest session on the machine ended with exit code 1 even when all its tests passed, so each mutant would have counted as killed. A first run of this module overlapped that time. It was thrown away and the module run again from the start with its own `--basetemp`. Three kills of the new run, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (2, 7 and 1).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%.

A mutant whose run times out counts as killed, as in earlier batches. Nine timed out, each a loop that no longer moves on: the reader's line counter `+= 1` to `+= 0` (lines 152, 167, 177, 189, 198 and 203), and `file_name`'s loop for a free name, whose test was negated or flipped (line 569) or whose `n += 1` became `+= 0` (line 570).

**Rechecked on 2026-11-24.** On a machine this loaded a mutant can time out without hanging, so each of the nine was applied again, alone, to `sidecar/` as it was at the batch's commit and the set run with a 1,500-second limit instead of 120. All nine really never end, and the numbers don't change: seven were stopped by the test suite's own 300-second limit for one test, with the stack in the loop the mutant breaks (`file_name` for 37, 116 and 219, the reader for 249, 257, 259 and 331), and two (254 and 327) were stopped by the machine as their loops kept growing; run again under a 6 GB memory limit, each failed with a MemoryError in the reader within a minute.

Since the run, #3064 changed `tmdl.py` (its file order now also sorts by a case-folded name), so the score is for `tmdl.py` at ad1ac4cf8. One new test, on the file order, was updated to the new order before this change went up; all the new tests pass on `main` bdf2c1775.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `tmdl.py` | test_tmdl, test_tmdl_edges, test_tmdl_write, test_model_ossie_service_edges, test_tmdl_mutation, test_boundary_mutation_2 (+ test_tmdl_uncapped) | 504 of 504 (485 + 19), uncapped | 467 / 37 | **92.7%** | 449/485 → 477/485 | 18/19 → 18/19 | 495 / 9 | **98.2%** | 9 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **tmdl.py**:
  - The 20 MB limit on reading: one character over is refused before anything is read, and exactly 20 MB is read.
  - Fewer than four spaces are no indent.
  - In the canonical form, numbers become text and booleans stay booleans. An object that became text is a difference at its path, and a key only in the second model is "(added)".
  - A model without a compatibility level gets 1604. Files other than the known ones are read last (the order keys 1, 10, 20 and 30).
  - A value with a quote at only one end is written as it is. `unquote` needs a quote at both ends.
  - An object whose name isn't text, or is empty, is left out and reported. So is a table entry that isn't an object, and a description that isn't text.
  - Database and model properties are one tab deep.
  - A partition source with more than an expression is written as a block.
  - A relationship end without a column keeps its table, and a table that isn't text is kept as a property. A relationship table with a line break, and a format string expression with a fence line, are refused by name.
  - A default that isn't text is written as text. Only a plain one-line changed property is written; the others are reported.
  - A format string definition on one line keeps its properties apart from its expression.
  - A line the reader doesn't understand is reported by its first 80 characters. A description right after `///` is read whole.
  - → `test_tmdl_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `tmdl.py` | 110 | `end < 0` to `end < 1`, and to `end <= 0` | Equivalent: `ref.find("'", end)` starts at 1, so it gives -1 or a position of 1 or more. |
| `tmdl.py` | 111 | the `raise` removed for a quote that isn't closed | Equivalent: with `end` at -1 the column part is the whole reference, which doesn't start with `.`, so line 117 raises the same `ValueError` with the same message. |
| `tmdl.py` | 122 | the `raise` removed for a reference with no dot | Equivalent: the column is then empty, so line 124 raises the same `ValueError` with the same message. |
| `tmdl.py` | 169, 180 | `min(..., default=0)` to `default=1` | Equivalent: the default is used only when the body has no lines that aren't blank. Then the body is empty, so the pad is never used. |
| `tmdl.py` | 244, 246 | `m.group(1)` to `m.group(0)` | Equivalent: `_WORD` is `^(\w+)$`, so the whole match is the group. |
| `tmdl.py` | 640 | `str(bim.get("name") or "")` to `and` | Equivalent: it changes only the name of the read-back model, and `differences` compares only `model` and the compatibility level. |

