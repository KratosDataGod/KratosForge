# Mutation testing, batch 509

A re-score of `notebook.py` on today's `main`, with `--kinds all`. It is the notebook kernel and what is around it: a cell's run and its outputs, the magics, the Variables pane, completion and hover, the debugger's `breakpoint()`, shared sessions, and reading and writing `.ipynb` files. It was last scored in batch 7 at **100%** of the 80 mutants it ran outside the kernel and the debugger (point mutants only), and its boundary mutants alone in batch 156 at **87.8%**. This run samples the whole module, the kernel too.

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 2,009 mutants, so a sample of 150 was run.

The test set was the files of batches 7 and 156: test_notebook_files, test_notebook_files_edges, test_notebook_folders, test_notebook_shelf_edges, test_ipynb_round_trip, test_output_cap, test_outputs_hardening, test_notebook_display_edges, test_notebook_history, test_notebook_places, test_notebook_resources, test_notebook_inputs, test_notebook_run, test_notebook_mutation and test_notebook_boundary. Many other test files use the kernel (170 in all) and weren't in the set, which is why so many mutants survived.

The new tests run cells in a real `Kernel` in the test process, as test_notebook_run.py does, and `%sx` and `%sh` run `echo`. A clock that moves a fixed step for each read stands in where a time is rounded. Before the "after" run, they passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `notebook.py` | the 15 files of batches 7 and 156 (+ test_notebook_mutation_b) | 150 of 2,009 (149 + 1) | 81 / 69 | **54.0%** | 81/149 → 141/149 | 0/1 → 1/1 | 142 / 8 | **94.7%** | 8 equivalent |

One bug was found:

- `breakpoint()` in a `%%capture` cell calls the hook that was there before the kernel's (pdb, which waits for typed commands that never come), instead of carrying on with the note as any other cell does. `%%capture` makes its run with count 0, which is how the hook tells a watch after a cell. The test `test_breakpoint_in_a_capture_cell_carries_on` pins it as a strict xfail; the mutant that makes that run with count 1 makes it pass, so it is killed.

### What the survivors were, and the tests that now kill them

- **notebook.py**:
  - Values and displays: a query that names `_sqldf` reads the view made for it, once for each result; an Arrow table is its own table; a plotly figure shows as plotly; a Spark frame says it has a query to profile; the size of text and lists is their length; an update past the rich cap names it in MB; a `%%capture` result says "1 output" and "2 outputs".
  - A cell's run: each print moves its version; an update replaces the output shown with its display id; a finished run's seconds are to the hundredth; `clear_output()` clears at once by default; text printed with no cell goes to the fallback; a cell knows a cell is running; `breakpoint()` pauses a cell under the debugger; `%%capture` leaves no output target behind; two places showing one display id are both kept; a local-first kernel keeps one write queue; a watch runs after the cell, and its seconds are to the thousandth; with no time left, a bounded read starts neither the read nor a timer.
  - The shared session: a guest's namespace is its own and kept; forgetting a guest cancels its own running cell, not another's, and with no cell run yet cancels nothing; a second cell of the same notebook is told a cell is running; a control changed while a handler runs is refused.
  - Magics: `%timeit -n 0` is refused; `%%writefile` says Writing, then Overwriting, then Appending to; `%env` hides secret-looking names only, refuses the app's own settings and sets others; `%sx` returns the lines and `%sh` prints them; `%pip install` in a guest notebook is refused; `%autoreload 2` is taken; `%run -c` inside a notebook run is refused; a run's arguments are set first in a notebook without a parameters cell; an empty quoted `%run` name is no name; `%%configure` takes `-f`, refuses a list and says when the same lakehouse is attached; `%fs` needs a command and `rm` needs a path; the Runs on check lets `%fs mounts` run and names an unknown command.
  - The Variables pane, completion and hover: a missing variable is missing; a set's items are sorted; a secret read as bytes is hidden; completion names a function and a class; hover reads the name at the cursor from the namespace, with where it starts and ends; a call's signature is given.
  - Paths and files: a `builtin/` path with a method on it is rewritten; a name too many folders deep says how many; a cell's chart and its other charts are kept apart; settings that aren't settings are dropped; a new notebook takes the next free number.
  - → `test_notebook_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `notebook.py` | 98 | `itertools.count(1)` to `count(0)` for `_sqldf` view names | Equivalent: only the number in the view's name changes, and each view still gets its own. |
| `notebook.py` | 279 | `module.startswith("bokeh") and hasattr(value, "document")` to `or` | Equivalent: Bokeh's own objects are shown by the `is_bokeh` check before it, and for anything else `file_html` fails inside `suppress(Exception)`, so the display goes on to the same next step. |
| `notebook.py` | 701, 723 | `_routers()[0]` to `_routers()[1]` | Equivalent: every place that sets or removes a thread's target sets stdout's and stderr's together, so both routers hold the same run. |
| `notebook.py` | 2260 | `return False` to `None` for a path in a list given to `%` | Equivalent: both callers only test the result for truth. |
| `notebook.py` | 3055 | `dumps_like`'s first `indent = 1` to `0` | Equivalent: the indent is used only for a file of more than one line, and then it is set again from the file's second line (or 1). |
| `notebook.py` | 3084 | `zip(cells, ids, strict=True)` to `False` | Equivalent: the ids are made from the same cells, one each. |
| `notebook.py` | 3475 | `dest.unlink(missing_ok=True)` to `False` after a failed resources copy | Equivalent: the notebook was copied to `dest` just before. |

**Main changed after this run (2026-11-28):** #3373 (seeded fuzz of the .ipynb round trip) and #3235 (MCP server hardening) changed `notebook.py` after the run. The score above is for the module at the run's `main`, as named on this page, and it was not run again on the new module. The new tests pass unchanged on today's main.
