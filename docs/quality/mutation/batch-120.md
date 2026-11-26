# Mutation testing, batch 120

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- What the headless agent runs: an environment's settings, the settings file, where pipelines leave run requests, a pipeline run looked up in Fabric, and what a run hands the engine (`runtime.py`).
- Shell commands from notebook cells: the shell a script cell uses, the cmd script file, running a command and reading its output, and ending a command on Windows (`shell.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. Neither module has a boundary mutant. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`.

A mutant whose run times out counts as killed. Four of `shell.py`'s timed out: they flip the checks that the reader thread has the output pipe and the input thread has the input pipe, or that input was given, so a thread fails or never starts and the command or the read waits forever.

Before the "after" runs, the new tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways. The Windows parts of `shell.py` are tested through its module-local `os` (`SimpleNamespace(name="nt")`) and a stand-in for `taskkill`, so they run on every OS.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `runtime.py` | test_runtime_edges, test_agent, test_cli_local_commands, test_json_files (+ test_runtime_mutation) | 44 of 44 (44 + 0) | 33 / 11 | **75.0%** | 33/44 → 44/44 | none | 44 / 0 | **100%** | none |
| `shell.py` | test_shell_and_decimals_edges, test_notebook_magics, test_app_control, test_runtimes_edges, test_subprocess_timeouts (+ test_shell_mutation) | 85 of 85 (85 + 0) | 75 / 10 (4 timeouts) | **88.2%** | 75/85 → 84/85 | none | 84 / 1 | **98.8%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **runtime.py**:
  - An environment's settings are a copy of the project's, and an environment it doesn't have is `{}`.
  - The settings file is indented by two spaces and leaves out the project and the alert address, which come from `agent.toml`.
  - Run requests go to the environment's LH_Meta in OneLake, and to its folder (no LH_Meta id) on this PC, and the backend is returned.
  - A pipeline run can't be looked up for an environment that runs on this PC.
  - The entity names are the project's.
  - One entity runs on its own unless a cascade is asked; the attempt is 1 when the job doesn't say; names and a backfill reach the engine as given.
  - The keys store is opened only when the run uses keys, and is handed to the engine.
  - → `test_runtime_mutation.py`.
- **shell.py**:
  - A `%%sh` cell uses sh, then bash, then nothing else; a `%%bash` cell uses bash.
  - A cmd script file that is already gone is no error.
  - A command given as a list runs with its arguments, not through the shell.
  - The threads that read a command's output and feed its input are daemons.
  - On Windows, `taskkill /F /T` ends the command's tree, with its output captured, no check and a 30 s limit, and the command is then awaited for 5 s.
  - → `test_shell_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `shell.py` | 105 | `text=True` to `False` | Equivalent: `encoding="utf-8"` is also given, and `subprocess.Popen` opens text pipes when an encoding is given. |

