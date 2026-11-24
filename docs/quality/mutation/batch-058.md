# Mutation testing, batch 58

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- `%pip` in a notebook cell on this PC: the commands it answers itself, an install followed through the engine, and how a failure is shown (`pip_magic.py`).
- The headless agent's JSON log lines and its native crash report (`logs.py`).
- OneLake shortcuts: how one is described, where it may go, its target, its name, and which managed tables use it (`shortcuts.py`).

The run was at `main` 346098b16 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/` or `examples/`, and no `-k` filter was used.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed + timeout / survived | Before | Point before → after | Boundary before → after | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `pip_magic.py` | test_pip_magic_run, test_notebook_magics_windows, test_environments, test_builtin_links (+ test_pip_magic_mutation) | 70 of 70 (69 + 1) | 61 / 9 | **87.1%** | 61/69 → 68/69 | 0/1 → 1/1 | 69 / 1 | **98.6%** | 1 equivalent |
| `logs.py` | test_logs_json, test_agent_crash_report, test_agent_clock_and_folder (+ test_logs_mutation) | 34 of 34 (33 + 1) | 29 / 5 | **85.3%** | 29/33 → 33/33 | 0/1 → 1/1 | 34 / 0 | **100%** | none |
| `shortcuts.py` | test_shortcut_center, test_shortcut_request_retries, test_shortcut_service_paths (+ test_shortcuts_mutation) | 85 of 85 (85 + 0) | 72 / 13 | **84.7%** | 72/85 → 85/85 | none | 85 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **pip_magic.py**:
  - An install is followed for 16 minutes, a minute past the engine's own limit.
  - A failure shows exactly the last 4000 characters of pip's log.
  - Reaching the deadline exactly still polls once more; only past it gives up.
  - `%pip show` prints an empty summary when a package has none.
  - → `test_pip_magic_mutation.py`.
- **logs.py**:
  - An object written as text is cleaned too.
  - The crash log is set aside past one mebibyte, and a log of exactly the limit is kept.
  - The crash log's folder is made with its parents, and every thread is traced.
  - → `test_logs_mutation.py`.
- **shortcuts.py**:
  - A folder shortcut is used by tables that read files under it, not by one whose folder only shares the start of its name, nor by the folder itself.
  - A table shortcut clashes only with a table of the same schema and name.
  - A shortcut's kind comes from its path, and its target type from the target, with "Other" when there is none.
  - Names with other characters (`\ / : * ? " < > |`), an empty name and one over 256 characters are refused; 256 is allowed and the name is trimmed.
  - → `test_shortcuts_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `pip_magic.py` | 60 | `re.split(..., maxsplit=1)` to `maxsplit=0` | Equivalent: only the first piece (`[0]`) is kept, and splitting at every comment marker leaves the text before the first one the same. |

