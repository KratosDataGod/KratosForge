# Mutation testing, batch 173

One module, run uncapped: every mutant, not a sample of 150. Batch 9 sampled 150 of its then 443 mutants (85.3% before, 96.7% after).
- Notebook import and export in the Fabric, Databricks and Jupyter formats: the export formats, percent-format scripts, the notes on what a Databricks conversion drops, and the limits on a .dbc archive (`notebook_formats.py`).

The run was at `main` ad1ac4cf8 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 468 of 468 mutants ran (459 point + 9 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. pytest was given `--basetemp` in a folder of this run's own, as in batch 172.

`test_snowflake_sql.py` was given as its one test that exports a notebook (`test_exports_keep_the_translation_as_escaped_text`); its other 311 tests don't call this module and take about 7 s. `test_ipynb_round_trip.py` goes through the notebook store, not this module, so it isn't in the set. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. No `-k` filter was used.

A mutant whose run times out counts as killed, as in earlier batches. Three timed out, each a loop in `_trim` that never ends: the two `while` tests negated (lines 80 and 82) and `lines[1:]` to `lines[0:]` (line 81).

Since the run, #3047 changed `notebook_formats.py` (Databricks notebook magics), so the score is for `notebook_formats.py` at ad1ac4cf8. The new tests pass unchanged on `main` bdf2c1775.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `notebook_formats.py` | test_notebook_formats_mutation, test_notebook_formats, test_notebook_formats_edges, test_notebook_formats_roundtrip, test_notebook_formats_service_mutation, test_notebook_formats_service_edges, test_deep_nesting, test_debugger_saved, test_notebook_report, test_chart_libraries, test_snowflake_sql (one test) (+ test_notebook_formats_uncapped) | 468 of 468 (459 + 9) | 434 / 34 | **92.7%** | 431/459 → 452/459 | 3/9 → 9/9 | 461 / 7 | **98.5%** | 7 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **notebook_formats.py**:
  - A .dbc archive holds at most 500 files and makes at most 200 notebooks, and one over 64 MiB isn't read; exactly at each limit is still read.
  - An archive may unpack to exactly its total cap. An entry that unpacks to more than its header says is refused by what was read, on its own and in the total.
  - A long entry name is cut at 120 characters in each message that names it (too big, bigger than its header, damaged).
  - An .ipynb of exactly the size cap is read.
  - Each export format writes its own kind of file, and an .ipynb export is indented by one space.
  - Only a `plotly` output with a figure becomes a plotly display.
  - A Jupytext header is only skipped at the top of a script.
  - Commands in a .dbc notebook that aren't objects are skipped.
  - An unknown cell magic is exported to Databricks as written.
  - `_safe_entry` answers `False` for an unsafe path, not just something falsy.
  - A notebook name is cut at 120 characters.
  - → `test_notebook_formats_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `notebook_formats.py` | 220 | the options start 2 → 3 characters after the magic name | Equivalent: the character after the name is always a space or the line's end, which `strip()` removes either way (as in batch 9). |
| `notebook_formats.py` | 224 | `[1:]` → `[0:]` for the lines after a line magic | Equivalent: the first line starts with `%`, which the same list leaves out anyway. |
| `notebook_formats.py` | 345 | `zip(strict=True)` to `False` | Equivalent: `to_ipynb` makes one Jupyter cell per cell, so both sides always have the same length. |
| `notebook_formats.py` | 463 | `lines[1:]` → `lines[0:]` | Equivalent: the header line comes before any marker, so it is never added to a block (as in batch 9). |
| `notebook_formats.py` | 504, 519 | `n` default 0 → 1 | Equivalent: every caller passes `n` (as in batch 9). |
| `notebook_formats.py` | 571 | `m.group(1)` → `m.group(0)` | Equivalent: the heading is only searched for `[markdown]` and `parameters`, which `# %%` doesn't change (as in batch 9). |

