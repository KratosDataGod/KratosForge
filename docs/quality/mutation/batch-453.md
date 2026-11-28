# Mutation testing, batch 453

One module not scored before, run with `--kinds all`:
- The Academy learner process in a Windows AppContainer: a profile of its own for each run, the Python runtime granted read and execute once to a capability SID, the workspace copied in and back with links and sealed files refused, and the process started in a Job with its own token (`academy/appcontainer.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 326 mutants, so a sample of 150 was run. `main` hasn't changed this module since.

The test set was test_academy_appcontainer and the tests of test_academy_sandbox about the AppContainer, sealed files and grants (`-k "appcontainer or sealed or grants"`; the "after" run named the same six tests by node id). The run was on Linux, where test_academy_appcontainer is skipped whole, so the "before" score is low: most of the module only runs on Windows.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. They run on every platform: Windows itself is stood in for (icacls, the Windows API, the profile and the process), so nothing here starts an AppContainer. Two survivors were left by the first "after" run that weren't Windows-only or equivalent. They got tests and were run again; the table counts that second run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/appcontainer.py` | test_academy_appcontainer, test_academy_sandbox (6 tests) (+ test_academy_appcontainer_mutation_b) | 150 of 326 (148 + 2) | 16 / 134 | **10.7%** | 16/148 → 90/148 | 0/2 → 2/2 | 92 / 58 | **61.3%** | 51 Windows-only, 7 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/appcontainer.py**:
  - `status()` says why not, off Windows and on it.
  - icacls runs with a minute's limit and answers its result. Denying ALL APPLICATION PACKAGES denies both SIDs once and refuses a failure. A grant is made only when it's missing, and a failure names the path.
  - The installed distributions are keyed by lowercase, hyphenated name. The learner paths follow requirements, pass over one that isn't installed or was already seen, and leave out the engine's names.
  - The runtime paths name the interpreter, the standard library, the sites and a venv.
  - Granting the runtime grants each path and the lib roots and records them. Without a data folder no record is kept. A second grant is skipped while its record is there. A record of the same SID skips the paths it lists. A reinstalled interpreter is granted again.
  - A plain path is absolute text icacls can't read as an option. The granted count counts plain paths that are there.
  - Revoking off Windows does nothing. Revoking removes the ACE, then the record. A path icacls can't change is listed and the record kept. Revoking while an exercise runs removes nothing, and without the capability it says why.
  - App package ACEs pair each path with its SDDL, and an ACL Windows wouldn't save is refused.
  - A folder or a sealed file every AppContainer can read is refused. A deny is fine, and a folder checked once isn't read again.
  - The copy takes 2,000 files and refuses one more, and takes exactly 1 GB and refuses one byte more.
  - A Box counts itself, shares the Job once and uncounts on close. A Box that can't be set up counts nothing. Any other setup error is raised as it is. A copy that fails closes the profile and raises.
  - → `test_academy_appcontainer_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `academy/appcontainer.py` | 107, 136 | 2 in `_api` (a DLL loaded without `use_last_error`; the API object not returned) | Windows-only: it loads the Windows DLLs through ctypes. test_academy_appcontainer runs it on Windows and is skipped here. |
| `academy/appcontainer.py` | 145, 157, 159, 160, 161 | 5 in `_sid_string` and `capability_sid` (a refusal removed, a condition negated, the SID's index, the value not returned) | Windows-only, as above: they call ConvertSidToStringSid and DeriveCapabilitySidsFromName. |
| `academy/appcontainer.py` | 168, 171, 172, 180, 185 | 6 in `Profile` (the name's length, a flag, the result check, a refusal removed, `mkdir`'s two flags) | Windows-only, as above: the profile is made by CreateAppContainerProfile. |
| `academy/appcontainer.py` | 548 to 581 | 19 in `LowboxProcess.__init__` (handle flags, the attribute list, pipe ends, the capability array, the environment block, the process flags, a refusal removed) | Windows-only, as above: they build the arguments of CreateProcessW. |
| `academy/appcontainer.py` | 596, 597, 603, 607, 610, 612 | 6 in `LowboxProcess.wait`, `kill` and `close` | Windows-only, as above: WaitForSingleObject, TerminateProcess and CloseHandle on a real process handle. |
| `academy/appcontainer.py` | 619 | 3 in `query_handle` (DuplicateHandle's access, inherit flag and result check) | Windows-only, as above. |
| `academy/appcontainer.py` | 649 to 677 | 10 in `sweep` (the registry walk, the folder's age, the profile's deletion) | Windows-only, as above: it reads the AppContainer mappings from the registry with winreg. |
| `academy/appcontainer.py` | 405 | `_SCAN_CHUNK` 1024 × 1024 to 1025 × 1024 | Equivalent: the chunk size only changes how a file is read in pieces. The tail kept between pieces still finds a mark across their edge. |
| `academy/appcontainer.py` | 410 | `keep = max(len(m)) - 1` to `+ 1`, and to `- 0` | Equivalent: a longer tail than the least that finds a mark across a chunk's edge only rereads a few bytes. |
| `academy/appcontainer.py` | 505 | `copyfile(..., follow_symlinks=False)` to `True` | Equivalent: `copy_tree` takes each source through `ws.files_under` and `ws.inside`, which refuse links, so the source is never a link. |
| `academy/appcontainer.py` | 513 | the incoming folder's random part `hex[:8]` to `hex[:9]` | Equivalent: only the temporary name's length changes. |
| `academy/appcontainer.py` | 531, 533 | `rmtree(..., ignore_errors=True)` to `False` for the incoming and old folders | Equivalent here: the folders were just made by the engine and hold plain files, so removing them doesn't fail. |
