# Mutation testing, batch 172

One module, run uncapped: every mutant, not a sample of 150. Batch 11 sampled 150 of its then 519 mutants (84.7% before, 97.3% after).
- The ipywidgets stand-in that notebook cells import: the widget registry, number and range controls, selections, Output and its caps, the controls `interact` picks, links, and the import finder (`widgets.py`).

The run was at `main` ad1ac4cf8 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 541 of 541 mutants ran (521 point + 20 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. pytest was given `--basetemp` in a folder of this run's own: a run on the same box earlier that night had pytest's shared temporary folder fail at the end of every session, which would count every mutant as killed. A first run started in that window was stopped and run again from the start; the figures below are from the second run, and a few of its killed mutants were checked by hand to fail a real test.

Three tests in `test_ipywidgets.py` were left out with `--deselect` to keep each mutant's run short: `test_a_handler_stuck_in_native_code_holds_cells_and_controls_until_it_returns`, `test_a_slow_handler_is_stopped` and `test_widget_events_wait_for_a_proof_and_a_replay`. They take about 10 s of the file's 20 s and test the kernel's handler threads, not this module. The other files that call the module through the notebook kernel (test_dbutils, test_notebook_inputs and the rest listed in batch 11) were not in the set, so the score counts only the files given.

Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. No `-k` filter was used. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `widgets.py` | test_widgets_mutation, test_widgets_more_edges, test_ipywidgets_parity, test_output_cap, test_boundary_mutation_2, test_outputs_hardening, test_ipywidgets (+ test_widgets_uncapped) | 541 of 541 (521 + 20) | 494 / 47 | **91.3%** | 475/521 → 509/521 | 19/20 → 19/20 | 528 / 13 | **97.6%** | 13 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **widgets.py**:
  - The registry keeps the last 1000 widgets, and ids count from `w1`.
  - A layout and a style given to a widget are kept.
  - A select-multiple, and a range slider, keep a description and a tooltip; a range slider casts a new step or max to a number.
  - An event with no value (a submit on its own, or an empty event) leaves the value alone.
  - Setting a value equal to the current one, but not the same object, tells no observer.
  - The defaults: a slider is 0 to 100 in steps of 1, with readout and continuous update on; a range slider is 0 to 100 with (25, 75) picked; an unbounded number box starts at 0 whatever its min.
  - `Output.capture()` keeps what the output holds unless asked to clear it.
  - `update_display` inside an Output replaces only the output with that id.
  - `interactive_output` shows a result, and nothing for `None`.
  - A control given to `interactive` wins over the argument's default.
  - A password box shows its placeholder and never its value.
  - The controls `interact` picks for 0, 0.5, -2 and a float range, with a hundredth of the span as the step.
  - A widget shown inside an Output is drawn one level below it, so the depth limit counts it.
  - A dropdown value is found by equality, not only by identity.
  - Rich output past the cap says "passed 16 MB".
  - `ipywidgets` is found as a package, and `ipywidgets.widgets` as a module.
  - → `test_widgets_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `widgets.py` | 153 | `_view(depth=0)` default | Equivalent: every caller passes the depth (as in batch 11). |
| `widgets.py` | 183 | `value is not None` to `is None` in `ValueWidget.__init__` | Equivalent: only a selection has a value in its state before this line, it is `None` there, and a selection's default is `None`. |
| `widgets.py` | 210 | `_state.get("min", 0)` default to 1 | Equivalent: `min` is always in the state, set just before. |
| `widgets.py` | 230 | the `is int` test in `_from_app`, negated and swapped (2 mutants) | Equivalent: setting `value` casts it again, so an int slider gets an int and a float one a float either way (as in batch 11). |
| `widgets.py` | 333 | `Checkbox._default` returning `None` | Equivalent: the value is cast with `bool`, and `bool(None)` is `False`. |
| `widgets.py` | 356 | `Text._default` returning `None` | Equivalent: the value is cast to text, and `None` becomes `""`. |
| `widgets.py` | 618, 621 | `__exit__`'s annotation `Literal[False]` and its `return False` to `None` | Equivalent: the annotation is never read, and `None` doesn't swallow an error either. |
| `widgets.py` | 623, 636 | `clear_output(wait=False)` and `capture(wait=False)` defaults | Equivalent: `clear_output` doesn't read `wait`. |
| `widgets.py` | 797 | `v > 0` to `v >= 0` for the slider's min | Equivalent: for 0 both give `-0` and `3 * 0`, which are both 0. |
| `widgets.py` | 855 | the `isinstance(c, fixed)` test in `kwargs` negated | Equivalent: both branches read `c.value`. |

