# Mutation testing, batch 500

One module not scored before, run with `--kinds all`:
- The first code in an Academy learner process: the Windows job and AppContainer checks, the import guard, the audit hook that keeps reads, writes, listings and native calls to the exercise's own folder, the wrappers that make Polars, deltalake, pyarrow, pandas and DuckDB refuse URLs and paths outside it, `kf_end_now`, and then the learner's script (`academy/_bootstrap.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 273 mutants, so a sample of 150 was run. The "before" run was stopped by a time limit after 119 of them; the other 31 were run with `--only` and the same seed and test set, and the table counts both parts.

The test set was test_academy_sandbox, test_academy_sandbox_mutation_b and test_academy_appcontainer. These run real learner processes. On Linux, the Windows-only parts (the job and AppContainer checks, the AppContainer path resolver, TerminateProcess in `kf_end_now`) never run there, which is why many of them survived.

The new tests load the file's functions on their own, in the test process, without the closing `_main()` call. Nothing they would change for real is changed: the audit hook is called directly and never added, the import guard goes on a stand-in `sys`, `os._exit` is stood in for, and the data libraries, DuckDB, deltalake's QueryBuilder and Windows (`ctypes.WinDLL`) are fakes. No learner process or sandbox is started. Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. One survivor was left by the first "after" run. It got a test and was run again; the table counts that second run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/_bootstrap.py` | test_academy_sandbox, test_academy_sandbox_mutation_b, test_academy_appcontainer (+ test_academy__bootstrap_mutation_b) | 150 of 273 (148 + 2) | 99 / 51 | **66.0%** | 99/148 → 145/148 | 0/2 → 1/2 | 146 / 4 | **97.3%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/_bootstrap.py**:
  - Outside an AppContainer a path is made absolute, then its links are followed. Inside one, a path in the workspace is checked folder by folder: a link is refused, a folder that can't be checked is refused without the first error attached, and a part that doesn't exist yet ends the check. A path beside the workspace isn't checked, and a workspace at a root joins its parts with one separator.
  - On Windows, `_main` asks about its job by name with query access only, or by a handle it was given (1 is a handle; 0, a negative number, True or text is not). An AppContainer run without a handle, a failed question or a process outside its job runs nothing and exits with 3. The job handle is closed. In an AppContainer the token is checked to be one (class 29, 4 bytes), and it is closed. `require_job` false skips the check. Both DLLs are loaded with `use_last_error`.
  - `_main` runs its steps in order, puts the learner libraries first on the path, follows a linked workspace, and runs the script as `__main__` in its folder with `kf_end_now`.
  - `kf_end_now` flushes both streams, even when one fails. On Windows it calls TerminateProcess on this process (-1) with the code as an unsigned 32-bit number; elsewhere the address it reports is 0.
  - The import guard drops blocked modules and goes first in `sys.meta_path`.
  - The audit hook: blocked imports by their first part; its own folder read and written; every write mode and flag outside it refused; the app's files refused; learner libraries read but never written; reads only under the read roots; a sibling folder with the same start isn't inside; the network, process and native events refused by name or prefix; `kf_end_now`'s one call allowed only with its exact address and plain ints; file changes only inside its folder; listings kept out of the app's folders but not its own.
  - The native wrappers: URLs, shares and paths outside the folder refused in each library, with the right words; `storage_options` and `filesystem` refused; named path arguments checked; a frame's write checks the path after it; functions reached through submodules wrapped too, but not in other packages; pyarrow's file classes checked, with the fallback for a class whose `__init__` can't be set; the local file system checking one or two paths per method; the other file systems and S3 functions refused; pyarrow's classes swapped only in pyarrow's own modules; `pyarrow.lib`'s openers wrapped where they are the same function.
  - DuckDB: every connection, and the default one, gets the four settings; a database file is checked; without a locked default connection the module's calls are refused.
  - deltalake's QueryBuilder: one SELECT or WITH only, execution settings only, the native builder refused where it is reachable, and nothing changed when the module or a builder is missing.
  - → `test_academy__bootstrap_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `academy/_bootstrap.py` | 18 | `TYPE_CHECKING = False` to `True` | Equivalent: the block then imports `collections.abc` and `typing`, which only name types in quoted annotations. |
| `academy/_bootstrap.py` | 19 | `if TYPE_CHECKING:` negated | Equivalent for the same reason. |
| `academy/_bootstrap.py` | 108 | `size = wintypes.DWORD(0)` to `DWORD(1)` | Equivalent: GetTokenInformation only writes the returned length into it, and nothing reads it. |
| `academy/_bootstrap.py` | 91 | `shared > 0` to `shared >= 0` | Equivalent: a handle of 0 is then taken as the job, and `not job` refuses it the same way. |
