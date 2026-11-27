# Mutation testing, batch 419

One module not scored before, run with `--kinds all`:
- The Academy learner process: a learner's Python exercise run in a process of its own with an environment built from scratch, time, memory, CPU and file limits (a Job Object on Windows, rlimits and a process group elsewhere), capped output, an AppContainer on Windows when the exercise can use one, and `contained()` for a trusted program such as Git run on learner input (`academy/sandbox.py`).

The run was at `main` ea988e99 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 207 mutants, so a sample of 150 was run. `main` hasn't changed this module since.

The test set was test_academy_sandbox, test_academy_pylab, test_academy_lab_python, test_academy_limits and test_academy_appcontainer. test_academy_appcontainer skips itself off Windows, and so do the Windows-only tests in test_academy_sandbox. On this Linux machine, no test reached the Job Object code, so all of it survived before.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. Learner scripts in the new tests run only through `sandbox.run`, and `contained()` runs only Python itself. The new Windows tests stand in for kernel32, ntdll and advapi32 and for `subprocess.Popen`, so they run on every OS, and no real job is made. Nineteen survivors were left by the first "after" run. They got tests, and those nineteen were run again; the table counts that second run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/sandbox.py` | test_academy_sandbox, test_academy_pylab, test_academy_lab_python, test_academy_limits, test_academy_appcontainer (+ test_academy_sandbox_mutation_b) | 150 of 207 (147 + 3) | 58 / 92 | **38.7%** | 57/147 → 141/147 | 1/3 → 3/3 | 144 / 6 | **96.0%** | 6 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/sandbox.py**:
  - A run reports its default limits (60 s, 1 GiB, 120 CPU seconds) and its seconds to the millisecond. On Linux and macOS, the process runs under its data, CPU, file-size, open-file and core rlimits.
  - The AppContainer is on unless KRATOSFORGE_ACADEMY_APPCONTAINER is 0. A run asks for one unless the exercise can't use one. On Windows, with AppContainers available, a run gets one. When setting it up fails, the reason is kept and the run takes the v1 way. A run with an AppContainer uses it and closes it.
  - Only the AppContainer's command line has `-B`, and its configuration carries the shared job handle.
  - The bootstrap's own refusal (exit 3, with its words) is reported as refused, and anything else as error or ok.
  - Read roots take only site-packages folders from the path. The sign-in packages are denied by their folders, and the engine by its own.
  - A learner library must be a plain folder.
  - Output is kept up to 1 MiB, cut at exactly 1 MiB whatever the chunk size, and then says it was cut. The output readers are daemon threads.
  - contained runs a trusted program with the limits and ends what that program left running. The POSIX group reports no memory hit.
  - The Windows job sets its limit flags, process count (at least 1), CPU time and memory. A named job can only be queried, must be new, and its access rules are freed and inherited by no one. Each failure says what Windows refused, and a process is resumed only once it is in its job. Closing waits up to ten seconds for the job to empty, reads its peak, and counts a hit at 98% of the limit. A job Windows won't describe counts as empty and unpeaked. The structures have Windows' layout.
  - On Windows, the v1 process and a contained program start suspended in their job, and one that can't join it is ended.
  - → `test_academy_sandbox_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `academy/sandbox.py` | 199, 311 | `t.join(timeout=5)` to `6` for the output readers | Equivalent: by then the process and its job or group have ended, so the pipes are closed and the readers finish at once. |
| `academy/sandbox.py` | 202 | `getattr(limiter, "emptied", False)` to `True` | Equivalent: the default is read only with an AppContainer, and there the limiter is a `_WinJob`, which always sets `emptied`. |
| `academy/sandbox.py` | 228 | `stream.read(65536)` to `65537` | Equivalent: the chunk size changes how output is read, not what is kept. |
| `academy/sandbox.py` | 368 | `processes: int = 1` to `0` in `_WinJob` | Equivalent: the limit is `max(1, int(processes))`, so 0 gives 1 too. |
| `academy/sandbox.py` | 379 | `created = 0` to `1` | Equivalent: `created` is only compared with 183, and an unnamed job never reads it. |
