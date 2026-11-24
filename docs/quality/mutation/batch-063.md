# Mutation testing, batch 63

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Charts from bokeh, altair and plotly in a cell's output: how each library's own show function is replaced, the Vega-Lite size limit, and the import hook that patches a library as it loads (`chart_output.py`).
- Which Fabric capacity each environment's workspace is on, how a failed read is classified, and how old a cached reading is (`capacity.py`).
- The accomplishments report as a PDF: font widths, line wrapping, page breaks, layout positions and the file structure (`pdfreport.py`).

The run was at `main` 41988f99a (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `pdfreport.py` has 317 mutants, so 150 were sampled; the other two modules were run in full.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/`, `examples/` or `app/`, and no `-k` filter was used.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `chart_output.py` | test_chart_libraries (+ test_chart_output_mutation) | 73 of 73 (72 + 1) | 41 / 32 | **56.2%** | 41/72 → 72/72 | 0/1 → 1/1 | 73 / 0 | **100%** | none |
| `capacity.py` | test_capacity, test_retrying (+ test_capacity_mutation) | 88 of 88 (87 + 1) | 74 / 14 | **84.1%** | 74/87 → 87/87 | 0/1 → 1/1 | 88 / 0 | **100%** | none |
| `pdfreport.py` | test_profile_goals_impact (+ test_pdfreport_mutation) | 150 of 317 (146 + 4) | 37 / 113 | **24.7%** | 37/146 → 145/146 | 0/4 → 4/4 | 149 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **chart_output.py**: the tests load a separate copy of the module and use stand-in library modules, so the engine's own copy and its import hook are not changed.
  - Before `install()`, nothing is drawn and no cell counts as running; `install()` puts its import hook first in the import path, and only once.
  - An Altair spec exactly at the 20,000,000-character limit is drawn, and one character more gives the "too large" text with its size.
  - A plotly figure dict with a property plotly doesn't know is still drawn, unchanged.
  - A bokeh figure is recognised and gives an HTML page titled "Chart"; a number, or an object from a bokeh module that isn't a bokeh model, is not.
  - An object with `to_plotly_json` from outside plotly doesn't count as a plotly figure.
  - Drawing outside any cell returns False.
  - bokeh's show is replaced once, keeps its docstring, and is replaced only in the modules where it is bokeh's own; the replacement draws in a cell without calling bokeh, refuses in a thread a cell started, and otherwise calls bokeh's show with the same arguments and returns its result.
  - Altair's show is replaced once and keeps its docstring; outside a cell it calls Altair's show with the same arguments, and in a cell it draws the spec; an Altair module without the mixin is left alone.
  - The browser stand-in for plotly opens nothing, and `plotly.offline.plot` is replaced once and keeps its docstring.
  - The loader wrapper returns the module its loader creates.
  - → `test_chart_output_mutation.py`.
- **capacity.py**:
  - A 401 or 404 status with no error code, and an error code with no status, are each classified on their own; 402 and 405 are plain errors.
  - A capacity without a state is "unknown", with its text.
  - Without a time given, a reading is stamped with the current UTC time.
  - A long error message is cut to 300 characters.
  - The workspace name Fabric returns is used, and the configured name when Fabric gives none.
  - With no capacity list reader, an environment on a capacity reads as signed out.
  - A reading is partial only when an environment is neither read nor unassigned.
  - Freshness counts whole seconds, never below 0, and a reading is stale only when it is more than an hour old.
  - → `test_capacity_mutation.py`.
- **pdfreport.py**:
  - The regular and bold width tables match the Helvetica and Helvetica-Bold AFM files shipped with matplotlib, and oblique has the regular widths.
  - A width uses the table for characters 32 to 126, both included, and 556 for anything else.
  - Text that starts with emphasis has no empty run before it, and an escape gives its character.
  - Only neighbouring runs in the same font are joined.
  - A word exactly a line wide starts its own line unbroken; a longer word breaks into full lines; two words that fit exactly share a line, and one point less splits them.
  - The space after a word takes the font of its last letter.
  - A new page starts only when a line would go below the bottom margin and footer, and the new page starts at the top margin.
  - Pages are US Letter, and the first line sits at the left margin, one line height below the top margin.
  - Title, section heading, paragraph and blank line sit at their exact heights, in their fonts and sizes.
  - A paragraph wraps at the width between the margins.
  - A wrapped bullet has one bullet mark, and its text lines are indented.
  - A section heading moves to the next page unless three body lines fit under it.
  - The footer is right-aligned at the right margin, at the bottom margin less 18 points.
  - The trailer's size counts every cross-reference entry.
  - → `test_pdfreport_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `pdfreport.py` | 190 | `zip(..., strict=True)` to `strict=False` | Equivalent: three object numbers are zipped with the three fonts, so the lengths always match. |

