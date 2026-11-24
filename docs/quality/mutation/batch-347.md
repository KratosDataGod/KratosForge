# Mutation testing, batch 347

One module, run uncapped: every mutant, not a sample. This run replaces the first batch 347 run of this module (at `main` 293fb38da, 174 mutants), because #3233 and #3243 changed the module afterwards and its kill tests no longer ran on today's code.
- The notebook debugger: breakpoints, stepping, pausing a cell and evaluating expressions while it is paused (`debugger.py`).

The run was at `main` a73ebb71a6 (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 224 of 224 mutants ran (212 point + 12 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended, and a check refused any connection to anything but this machine; it wrote down none (two small plugins, not committed).

The set is test_debugger, test_debugger_hardening and test_debugger_robust, as in the first run, about 50 s a session. test_debugger_eval (81 s), test_debugger_engine (63 s) and test_debugger_soak (56 s) also drive the module, and were left out to keep each mutant's run inside the 120 second limit; so were the other debugger files, to keep the set the first run's. The score counts only the files given.

The first run's `test_debugger_mutation.py` failed 5 tests on today's code: its stand-in frames were `SimpleNamespace` objects, which can't be dict keys and have no `co_flags`, and the module now keeps each cell's namespace by frame and reads `co_flags`. The stand-ins are now a small class hashed by identity, like a real frame, and the test of the cell's own variables gives them in the frame's globals, where a cell's names now come from. The file then got the new tests below.

Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used. No mutant's run timed out, before or after (the longest took 61 s), so no timeout needed checking on its own. After the runs no temporary folder was nested deeper than 40 levels. The run ends on boundary mutants that survived and one that was killed, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests: the "at most 100 breakpoints" refusal removed failed 2, a cell's namespace read for a function's frame failed 6, and a function's variables dropped failed 12.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `debugger.py` | test_debugger, test_debugger_hardening, test_debugger_robust (+ test_debugger_mutation) | 224 of 224 (212 + 12) | 150 / 74 | **67.0%** | 143/212 → 203/212 | 7/12 → 10/12 | 213 / 11 | **95.1%** | 11 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **debugger.py**:
  - Most were what the first run's tests already kill, on the limits (100 breakpoints, 200 variables, 5 stepped-over names, 20 evaluations, 2000 characters and a 10 second wait), Stop and commands when the cell isn't paused, dunder and kernel names, the module name of a stepped-over call, and when a generator's frame suspends.
  - Past the 3 second describe deadline, a variable is listed by name and type only, and it is described until the deadline is exactly reached.
  - A last expression on the same line as the statement before it (`y = 2; y`) is not a second stop, and code added later isn't taken for that expression.
  - A pause in a class body shows the class's own names and evaluates in them.
  - `breakpoint()` in the cell pauses on its line and says it paused, and is refused outside the cell's code or while the cell is paused.
  - A cell that turns tracing off is told the debugger lost track, and a cell that doesn't isn't.
  - A breakpoint on a blank line pauses at the next line with code, and one past the last code gets its exact note.
  - A step or next from a comprehension line goes on to the next line, not round the loop.
  - A generator stepped out of and resumed on the same cell line is a new place to stop.
  - → `test_debugger_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `debugger.py` | 150 | `_line` returns None, not itself, on the line it skips | Equivalent: CPython keeps a frame's local trace function when it returns None (the first run's line 125). |
| `debugger.py` | 161 | `_line` returns None, not itself | Equivalent: the same. |
| `debugger.py` | 123 | `event == "call" and …` to `or` | Equivalent: the trace function `sys.settrace` sets gets only "call" events, so the first part is always true. A function's frame is then kept too, but never read (a function's names come from its `f_locals`), and it is dropped when it returns. |
| `debugger.py` | 171 | `_stops_on_return` returns None, not False | Equivalent: its only caller tests it in an `if`. |
| `debugger.py` | 185 | `_stops` returns None, not False, on the line a step started on | Equivalent: its only caller tests it in an `if`. |
| `debugger.py` | 192 | `_stops` returns None, not False | Equivalent: its only caller tests it in an `if`. |
| `debugger.py` | 78 | the first `self.depth = 0` to 1 | Equivalent: every pause sets the depth before Step over and Step out read it, and Step and Continue never read it. |
| `debugger.py` | 175 | `_depth` starts counting at 1, not 0 | Equivalent: every depth moves by the same one, and the pause's depth comes from `_depth` too. |
| `debugger.py` | 337 | `0 <= frame.f_lasti` to `1 <=` | Equivalent: only the first instruction differs, and a code object never starts with a return instruction. |
| `debugger.py` | 337 | `0 <= frame.f_lasti` to `0 <` | Equivalent: the same, at offset 0. |
| `debugger.py` | 337 | `frame.f_lasti < len(code)` to `<=` | Equivalent: a frame's last instruction is always inside its code. |
