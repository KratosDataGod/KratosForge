# Mutation testing, batch 62

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The Fabric step: the Run on Fabric mark, table names for V-Order, reading what a cell OPTIMIZEs, and the one-cell Spark notebook (`fabric_step.py`).
- Script activities in local pipeline runs: lakehouse or local data project, the checks before the run, running each block and reading its rows (`pipeline_script.py`).
- Schedules that also run while you're signed out of Windows: the schedule id check, the PowerShell call, Task Scheduler's results and the log line (`signed_out_tasks.py`).

The run was at `main` ae262ce70 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. No test file that reads `docs/`, `examples/` or `app/` was run. Some test files were left out:
- `test_pipeline_runs.py` reaches `pipeline_script.py` but takes about 20 seconds a run. A first run that included it was stopped and the module was run again without it.
- `test_side_by_side.py`, `test_side_by_side_edges.py`, `test_notebook_cells.py`, `test_notebook_magics.py`, `test_risk.py`, `test_risk_service_edges.py`, `test_validate.py` and `test_integrations_executions.py` reach `fabric_step.py` or `signed_out_tasks.py` only through other modules. They were left out for time, which can only lower the "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `fabric_step.py` | test_fabric_step (+ test_fabric_step_mutation) | 89 of 89 (85 + 4) | 81 / 8 | **91.0%** | 77/85 → 85/85 | 4/4 → 4/4 | 89 / 0 | **100%** | none (scored before #2759; see below) |
| `pipeline_script.py` | test_pipeline_script, test_pipeline_script_edges (+ test_pipeline_script_mutation) | 147 of 147 (144 + 3) | 110 / 37 | **74.8%** | 108/144 → 142/144 | 2/3 → 3/3 | 145 / 2 | **98.6%** | 2 equivalent |
| `signed_out_tasks.py` | test_signed_out_schedules, test_local_schedules_edges (+ test_signed_out_tasks_mutation) | 87 of 87 (86 + 1) | 68 / 19 | **78.2%** | 68/86 → 86/86 | 0/1 → 1/1 | 87 / 0 | **100%** | none |

One bug was found, in `fabric_step.py`, and filed as #2749: a `%sql` line magic below the cell's first line (`df = %sql SELECT 1`) was refused with "isn't Python that Fabric Spark can run", though the docstring says `%sql` lines become `spark.sql` as when publishing. Nothing ran, so the refusal was safe. #2759 fixed it, and `test_a_sql_line_magic_below_the_first_line_becomes_spark_sql` now passes as a plain test.

After #2759, the module has 90 mutants (86 + 4), and the same test files kill 89 of them (**98.9%**). The one left turns `language == "sql"` into `!= "sql"` at line 166. It is equivalent: a cell whose language is `sql` always starts with `%` once leading blank lines are stripped, so the next test in that `if` catches it anyway, and a cell with no magics comes through the `%sql` rewrite unchanged. Cells with string literals holding `%%sql`, comments, `!` lines, other magics, indented `%sql` lines and leading blank lines all give the same result either way.

### What the survivors were, and the tests that now kill them

- **fabric_step.py**:
  - An empty marked cell is named as empty, whether it is last or has code below it.
  - Two code cells below the mark are counted as "2 code cells are below".
  - A table name has one or two parts, each quoted with backticks (a backtick inside is doubled). Empty parts are skipped, and no parts or three parts are refused.
  - A table OPTIMIZEd twice is listed once.
  - A bare `%sql` line on the first line and an indented `%%sql` cell both become `display(spark.sql(...))`, and a Python cell is left as written.
  - A syntax error names its line.
  - The step notebook's header names the notebook exactly, and the result goes to the request's file in the attached lakehouse.
  - → `test_fabric_step_mutation.py`.
- **pipeline_script.py**:
  - An expression is a string starting with `@` (not `@@`) or a dict of type `Expression`. Only an expression's text is unwrapped.
  - Projects keep only text names that map to text projects.
  - A Script activity reads a lakehouse when its connection is a `Lakehouse` and no project is named for the activity or its connection.
  - A connection with no type is called "a connection", and a named type is named.
  - Every message names the script it's about by its number (parameters, unreadable T-SQL, a file read, a confirm, a refusal, a time limit, a stop or a failure), and a file read names its statement by number too.
  - A refusal without a label or reason still reads ("is refused (not allowed here)").
  - A failed script says the refusal's message, else the error, else "failed", with the matching error code.
  - A person cancelling a running script fails it as cancelled.
  - Lakehouse translation notes are logged under their script's number.
  - Later pages ask only for the rows still wanted, a longer page is cut at 5,000 rows, and a row that doesn't match the columns fails.
  - At exactly the deadline a script keeps running; only a later clock reading ends it with the policy timeout (added in review).
  - → `test_pipeline_script_mutation.py`.
- **signed_out_tasks.py**:
  - A schedule id is 1 to 32 lowercase hex characters and is returned as given. `../x`, `a/b`, `a\b`, an empty or missing id, uppercase, 33 characters, a leading or trailing space, a trailing newline, `;`, `'`, a space inside and `0x1f` are refused.
  - PowerShell is called by its full path under `SystemRoot` (or `C:\Windows`), with exact arguments: `-NonInteractive` and a 2-minute limit by default, no `-NonInteractive` and a 5-minute limit for the password prompt. Output is captured as text, and a non-zero exit is returned, not raised.
  - The exit codes the scripts use are the ones the Python side reads.
  - The task definition is written as UTF-16, handed over by environment, and removed afterwards, also when the prompt times out.
  - Pausing or resuming passes the choice, succeeds on exit 0 and raises with Task Scheduler's words on any other exit. A bad id is refused before anything runs.
  - The log line replaces the account name in any case, but not a one-letter name. It is one line, cut at 500 characters, and a refusal is logged without the account name.
  - A task's last result reads "password" or "not allowed" for both forms of each code, and anything else reads as nothing.
  - → `test_signed_out_tasks_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `pipeline_script.py` | 159 | `"resultSetCount": 0` to `1` | Equivalent: every path that returns `out` sets `resultSetCount` to the number of result sets first (line 175). |
| `pipeline_script.py` | 204 | `return None, False` to `None, True` | Equivalent: `run` reads the cut flag only when there are rows (`rows is not None`), and here there are none. |

None of the three involves text, so leading or trailing whitespace and regex edge cases can't change them.

