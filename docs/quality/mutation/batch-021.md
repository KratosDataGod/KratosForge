# Mutation testing, batch 21

Three modules that no open pull request is changing:
- Warehouse projects in source control: the size and count limits and the messages that name them, how deep projects and files may sit, which folders are searched, names read from the text, and which environment a deploy counts as (`warehouse_project_service.py`).
- What an outside tool is given: page limits, the thin adapters to the engine, executions, what notebook runs and proofs start with, and which fields reach the tool (`integrations/services.py`).
- The notebook's own Python process: requests and their answers, the snapshot a starting cell shows, polls, stop, restart and close, the result viewer's requests, and how exit codes and limits are written (`kernel_proc.py`).

`integrations/services.py`, left out of batch 20, is in this one.

The run was at `main` adb130d08 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 20 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Four tests in `test_integrations_prompts.py` read pages in `docs/` and failed there, so they were deselected from the `integrations/services.py` run. Every other test that was kept passed both ways, so no run below can score a false 100%.

`test_integrations_executions.py` and `test_integrations_conformance.py` also use `integrations/services.py`, but each takes about 40 seconds, so the sampled run left them out. `test_integrations_executions.py` was run on the 11 survivors in the notebook run and proof start checks and killed 9; the other two only drop `retryable=True`. For `kernel_proc.py`, the existing tests of the outside tool's environment (`test_integrations_executions.py`) and of exit codes (`test_notebook_out_of_room.py`) were missed by the first run; they were run on its 49 survivors and killed 12. The new tests were written for the rest.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `warehouse_project_service.py` | test_warehouse_project, test_warehouse_project_edges, test_warehouse_project_more_edges (+ test_warehouse_project_mutation) | 150 of 340 | 129 / 21 | **86.0%** | 149 / 1 | **99.3%** | 1 equivalent |
| `integrations/services.py` | test_integrations_services_edges, test_integrations_core, test_integrations_workspace_scope, test_integrations_registry, test_integrations_prompts, test_integrations_mcp (+ test_integrations_services_mutation) | 150 of 203 | 59 / 91 | **39.3%** | 150 / 0 | **100%** | none |
| `kernel_proc.py` | test_kernel_process, test_kernel_process_edges (+ the four existing tests above, test_kernel_proc_mutation) | 150 of 505 | 101 / 49 | **67.3%** | 143 / 7 | **95.3%** | 2 Windows only, 5 equivalent or timing |

The one timeout was in `kernel_proc.py`: stop's wait for the reader, negated, waits its full 5 seconds. The three new test files pass on DuckDB 1.5.6 and 2.0.0. The `kernel_proc.py` tests that start a real process end it in a `finally` block.

### What the survivors were, and the tests that now kill them

- **warehouse_project_service.py**:
  - 5000 objects being taken and 5001 refused, for writing and for comparing.
  - A file of exactly 1 MB being read and one byte more refused, and the read, save and diff messages naming 1024 KB.
  - A diff taking 2 MB in all.
  - A small project having no note about being cut.
  - Projects being found four folders down and no deeper, and a folder deeper than that being refused.
  - Hidden folders, build folders (in any case) and files named like projects holding no projects.
  - At most 50 projects being listed from one repository.
  - A project being named from its `.platform` file, and the repository by its name or else its folder.
  - A file seven folders deep being allowed and eight refused, and a link to nowhere being refused.
  - Quotes inside a bracketed name being part of the name.
  - A table without a schema being skipped for that reason, when writing and when comparing.
  - A project made twice keeping its first `.platform` file.
  - `prod` sent without a workspace counting as no environment, so the deploy is gated.
  - → `test_warehouse_project_mutation.py`.
- **integrations/services.py**:
  - A page being at most 500 and at least 1, and the default page sizes of each listing, from the first row.
  - Listings asking the engine for everything before paging.
  - About 20 adapters handing back what the engine gave and passing what the caller sent.
  - Pipeline run settings, and a zero timeout meaning none.
  - A step an outside tool can only mock saying so.
  - Notebook dependencies and environment coming from the saved notebook.
  - Proofs, side-by-side runs and receipts coming back without data, and metadata and notebook fields that reach a tool, with the metadata project named or "Metadata project".
  - Cell line counts, and source hidden when asked.
  - The execution manager being made once, SQL and Python run titles, targets, waits, timeouts and row limits, and a Python run taking a whole lakehouse or none.
  - Execution status waiting only when asked and leaving out the logs, and logs, lists and cancels going to the manager.
  - What a notebook run and a proof are started with, and the message for a notebook with no code.
  - A receipt export hiding what the caller asked and what policy requires.
  - → `test_integrations_services_mutation.py`.
- **kernel_proc.py**:
  - A request without a process being refused and starting none, and a request returning its result or raising the error it is answered with, by message or else by type.
  - The variable libraries call answering nothing when the engine gives nothing.
  - A starting cell showing running, with the next count and no outputs, and a cell that ends its process before starting showing those outputs.
  - A cell not starting while another notebook's cell runs in a shared session, naming that notebook or "Another notebook".
  - Another notebook's poll asking the running process without starting one, with its timeout.
  - A poll without outputs keeping the outputs already shown, and a poll that gets no answer showing the cell as it last was.
  - A process that ends only ending a cell that is running, and a restart clearing the count and the cell.
  - The debugger's timeout, and the result viewer's defaults and answers.
  - Memory being read in bytes from the kilobytes Linux reports.
  - The reader thread being a daemon, stop waiting for a slow reader, and close ending a process that runs.
  - A cell inside its time limit not being interrupted.
  - Exit codes shown in hex only past four hex digits, signed or unsigned, and limits written in their largest whole unit, in GB from 1 GB up.
  - → `test_kernel_proc_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `warehouse_project_service.py` | 95 | `and` ↔ `or` in `_unquote` | Equivalent: `_PART` only matches a balanced bracket or quote form or a plain identifier, so both forms give the same answer. |
| `kernel_proc.py` | 837 | `JOB_PIDS = 1024` → `1025` | Windows only: the job object's process list. |
| `kernel_proc.py` | 838 | `PROCESS_QUERY_LIMITED_INFORMATION` `0x1000` → `0x1001` | Windows only: `OpenProcess` access rights. |
| `kernel_proc.py` | 313 | `proc is self._proc and self.pid` → `or` in the memory watch | Equivalent once the process has said its id; before that, under a second, the watch measures nothing instead of the launcher. |
| `kernel_proc.py` | 468 | the 5 second wait after a cell ends its process → 6 | Timing: the reader marks the process gone well inside either. |
| `kernel_proc.py` | 481 | `or` → `and` when another notebook polls | Equivalent: an ended process makes the request fail, and that gives the same idle answer. |
| `kernel_proc.py` | 533 | stop's 5 second wait for the reader → 6 | Timing, as at line 468. |
| `kernel_proc.py` | 630 | `spawn=False` → `True` for `frame_wrangle` | Equivalent: `alive()` is checked just before, so a process is always running. |

### Real bugs found

None.

### Not in this batch

`meta/source_systems.py`, kept as a substitute, wasn't needed.

