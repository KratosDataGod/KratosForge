# Academy grading and the learner process

Stories: ACD-004 (validators), ACD-005 (learner process), ACD-011 (Python, PySpark and Delta
exercises), ACD-012 (pipeline exercises). Security read 2026-11-03: v1 ships without an OS sandbox,
as Preview and with first-party packs only, under the conditions in [Security conditions](#security-conditions).

## What we are protecting, and from what

The learner owns the PC and runs Academy as themselves. Academy is not trying to hide the PC from
them. The risks are:

1. **Accidental damage.** An exercise answer (a loop that writes forever, a `shutil.rmtree` with a
   wrong path, a fork bomb) must not harm the person's files, the app's data folder, or their
   Fabric tenant.
2. **Credential reach.** Learner code must never hold or reach the person's Fabric/Entra tokens,
   the token cache, the keys DB, the app's settings, or an attached lakehouse.
3. **Gaming the grade.** The learner's process must not be able to read the reference solution or
   hidden checks, or write the validator's inputs anywhere but its own workspace.
4. **Hostile content (later).** Starter code and broken environments are authored by pack authors.
   Today every pack ships in this repo and is reviewed. If packs can ever be imported (ACD-017),
   starter code becomes untrusted third-party code that runs on open.
5. **Hostile outputs.** Validators run in the engine process and read files the learner wrote, so
   those files are untrusted input to the engine (symlinks, junctions, huge files, Delta logs that
   point outside the workspace).

## Layout

```
<data>/academy/<pack>/<exercise>/      the workspace (learner owns it; Reset deletes and recreates)
  start/ ... copied from the pack's starter assets only
  .tmp/                                TEMP/TMP/HOME for the learner process
<install>/fabric_desktop/academy/packs/<pack>/grading/<exercise>/   reference + hidden checks (read only, never copied)
```

The workspace is created under `<data>/academy` with the same containment checks as Learn labs
(`labs.lab_dir`): ids only, resolved parent must equal the base, and the workspace folder itself
must not be a reparse point. Reset refuses to follow a link and only removes a folder whose
resolved parent is `<data>/academy/<pack>`.

## Layers for `python_lab` (learner Python, Polars, DuckDB, delta-rs)

| Layer | What it does | Boundary? |
| --- | --- | --- |
| L1 separate process | `python -I -X utf8 bootstrap.py`, cwd = workspace, stdin closed, CREATE_NO_WINDOW. Environment built from scratch: only PATH, PATHEXT, SYSTEMROOT, WINDIR and locale vars; TEMP/TMP/HOME/USERPROFILE/APPDATA/LOCALAPPDATA point into `.tmp`; no proxy vars, no `FABRIC_*`, no `AZURE_*`. Not the notebook kernel: no `kernel_host`, no token callback, no lakehouse sync, no RPC back to the engine at all; results go out as one JSON line on stdout. | Yes, for credentials held in memory by the engine |
| L2 limits | Windows: a Job Object with KILL_ON_JOB_CLOSE, ACTIVE_PROCESS_LIMIT = 1 (no child processes at all, so no fork bomb and no `cmd /c`), JOB_MEMORY limit (default 1 GB), per-job user CPU time limit; the engine also enforces a wall-clock limit (default 60 s) and output cap (1 MB). POSIX (CI only): new session, RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE, RLIMIT_NOFILE, wall clock, killpg. | Yes, for resource exhaustion |
| L3 audit hook | Installed by the bootstrap before learner code: refuses `socket.*`, `subprocess.Popen`, `os.system`, `os.exec*`, `os.spawn*`, `os.posix_spawn`, `os.fork`, `os.startfile`, `ctypes.dlopen`/`ctypes.dlsym`/`ctypes.call_function`, `winreg.*`, `open` for write and `os.remove/rename/mkdir/rmdir/chmod/symlink/link` outside the workspace, and `open` for read outside the workspace and the Python install (`sys.base_prefix`, `sys.prefix`, site-packages), always refusing the `fabric_desktop` package folder and the app's data folder. `duckdb.connect` is wrapped so every connection gets `allowed_directories=[workspace]`, `enable_external_access=false`, a memory limit and `lock_configuration=true` (checked on DuckDB 1.5.6: reading `/etc/passwd` raises PermissionException). Polars `read_*`/`scan_*`/`write_*`/`sink_*` and `deltalake.write_deltalake`/`DeltaTable` are wrapped to refuse URLs and paths outside the workspace. Imports of `fabric_desktop`, `msal` and the sign-in packages are refused. | **No.** Defense in depth: native code (a compiled extension, delta-rs object store, a pre-loaded `_ctypes` handle) is not seen by Python's audit hooks |
| L4 OS sandbox | Windows AppContainer: the learner process gets no network capability and file access only to folders ACL-granted to its AppContainer SID (the workspace read/write; the bundled Python runtime and site-packages read only). | Yes, for files and network, enforced by the kernel |

## Security conditions

The Security read (2026-11-03) agreed v1 doesn't need L4, as Preview with first-party packs only,
on these conditions:

1. The Job Object is assigned to the process while it is suspended, before any learner code runs,
   with no breakaway; a child process fails even as the first statement.
2. The learner process can't import `fabric_desktop`, `msal` or the engine's other sign-in
   packages (they aren't on its `sys.path`, and the audit hook refuses the import), and reads under
   the `fabric_desktop` package (which holds `grading/`) and the app's data folder outside the
   workspace are refused.
3. No claim about network. Polars, delta-rs and native extensions do I/O the audit hook never
   sees, so their read, scan, write and sink entry points are wrapped to refuse URLs and paths
   outside the workspace. That guards against accidents, not against someone determined.
4. Honest wording: "the process holds no tokens and gets none from the app". It runs as the same
   Windows user, so a deliberate learner can still read their own on-disk sign-in cache; only L4
   changes that. The UI says "runs as you on this PC, without your sign-in, with time and memory
   limits", never "sandboxed".
5. Validators run only after the learner process has exited and its Job is closed.

ACD-005b (Windows AppContainer, no `internetClient` capability, no access to
`%LOCALAPPDATA%\FabricDesktop`, read only on the runtime folder) is required before imported or
third-party packs, or any "sandboxed" claim.

## Grading material

- Validators run in the engine process, never in the learner process, and read reference data from
  `grading/` in the install folder. Nothing from `grading/` is copied to the workspace, passed in
  the learner's environment, or sent to the UI before the learner asks for the explanation after an
  attempt.
- No grading secrets exist: hidden checks are data in the pack, and the app is open source, so a
  determined learner can read them in the repo. The goal is that a run can't read them by accident
  or by its process, not secrecy.
- Fabric exercises (ACD-014, later) use the learner's own sign-in; no shared credentials.

## Validators reading learner outputs (ACD-004)

Learner outputs are untrusted input to the engine (`academy/workspace.py`):

- Every path a validator reads is resolved and must stay inside the workspace; any component that
  is a symlink, junction or other name-surrogate reparse point is refused (same rule as
  `notebook_files_service`). A file with more than one hard link is refused too, since a hard
  link isn't a reparse point and can name a file outside the workspace.
- Size caps before reading (file count, bytes, rows); JSON parsed with a size cap, and too-deep
  nesting refused. Tables are read by DuckDB under its memory limit with a row cap, never whole
  into Python first.
- Messages name the column, row count or reason, never cell values or file contents beyond a short
  value that doesn't look like a secret.
- Delta tables: the `_delta_log` commits are read as data, and any `add`, `remove` or `cdc` path that
  is absolute, a URI with a scheme, or escapes the table folder after decoding and normalisation
  fails the check, as does a deletion vector stored at an absolute path. A table with a checkpoint
  (or `_last_checkpoint`) is refused: exercise tables have a handful of commits and are checked from
  them only. `delta_table` reads its facts from the checked log and opens no data file.
- DuckDB connections used by validators: `enable_external_access=false`, `allowed_directories`
  limited to the workspace, memory limit, locked configuration, time limit (as Learn labs).
- Validators never import, `exec` or `eval` anything from the workspace. Pipeline JSON and
  settings documents are parsed, never run.

## The learner process as built (ACD-005)

`academy/sandbox.py` runs one script: `sandbox.run(work, script, data_root, time_limit=60,
memory_limit=1 GB, cpu_limit=120, lib_roots=None)`. It returns `status` (`ok`, `error`,
`time_limit`, `memory_limit` or `refused`), the exit code, stdout and stderr (each capped at
1 MB), the seconds taken and the limits, and `runs_as`, the sentence the UI shows: "you on this
PC, without your sign-in, with time and memory limits".

How each Security condition is met:

1. **Job before code.** On Windows the process starts with `CREATE_SUSPENDED`, goes into a Job
   Object (one active process, job memory, job CPU time, kill on close, die on unhandled
   exception, no breakaway flags), and is resumed only then (`NtResumeProcess`). The bootstrap
   asks Windows whether the process is in that job by its handle, and the bootstrap opens the
   job by its random name and checks the same, exiting with code 3 before reading the script if
   it isn't (a job the engine itself runs in, like a CI runner's, doesn't count). Others can only
   query the job by name: its access rules grant nothing else. Tests: a child process fails as the first statement with plain Python and no hook; the
   bootstrap won't run outside a job. On Linux (CI only), the process gets its own session and
   `RLIMIT_DATA`, `RLIMIT_CPU`, `RLIMIT_FSIZE`, `RLIMIT_NOFILE` and no core file.
2. **No engine imports, no grading reads.** The interpreter runs as `python -I -c`, with a short
   line that reads `_bootstrap.py` from the install folder (a whole bootstrap on the command line
   would near Windows' 32,767-character limit), so the script's folder is never on `sys.path`. A meta path finder and the `import` audit event
   refuse `fabric_desktop`, `msal`, `msal_extensions`, `pymsalruntime`, `azure`, `keyring`,
   `win32cred`, `mssql_python` and `mcp`. Reads and listings under the `fabric_desktop` package
   (which holds `grading/`), under those packages, and under the data folder outside the workspace
   are refused.
3. **No network claim.** Python sockets, `urllib`, `http`, `ssl` and similar are refused, but
   that is not a network boundary. Polars `read_*`, `scan_*`, `write_*` and `sink_*`;
   `deltalake.write_deltalake` and `DeltaTable`; pyarrow parquet, dataset, csv, feather, orc and
   json readers and writers; remote pyarrow file systems; pandas parquet, feather, orc, csv, json
   and Excel I/O; and every DuckDB connection (`allowed_directories` = the workspace, external
   access off, locked) all refuse URLs, `storage_options` and paths outside the workspace. The same
   functions reached through submodules (`polars.io.csv.functions.read_csv`) are replaced too.
   `pyarrow.OSFile`, `memory_map`, `create_memory_map`, `input_stream` and `output_stream` check
   their path. `pyarrow.fs.LocalFileSystem` is a subclass whose path methods check every path
   (`SubTreeFileSystem` can't confine a file system, since it lets `..` through), and every other
   pyarrow file system is refused. `FileSystem.from_uri` takes only a checked path and hands back
   the checked local file system. The same replacements are made in `pyarrow._fs`, `pyarrow.lib`
   and every pyarrow module that imported these names. If DuckDB's default connection can't be locked, its
   module-level functions are refused (fail closed). These
   libraries are imported before the audit hook starts, because some (Polars, pyarrow) load
   native code through ctypes, which the hook then refuses. Learner code that deliberately
   re-imports a module or reaches its internals can get past the wrappers; that is the accepted
   limit until ACD-005b.
4. **Wording.** The process holds no tokens and gets none from the app. It runs as the same
   Windows user, so the person's own sign-in cache on disk is theirs to read; only ACD-005b
   changes that.
5. **Validators after the Job.** `run` returns only after the job is terminated and reports no
   active process (Windows), or the process group is killed (Linux). A test checks that nothing
   writes to the workspace after `run` returns. Because of this ordering, the check-then-read gap
   in `workspace.py` is acceptable. That includes Windows `scandir` entries, which carry no inode,
   so `links()` can't detect a swap there.

Also:

- The environment is built from scratch: `PATH`, `PATHEXT`, `SYSTEMROOT`, `WINDIR`, `COMSPEC`,
  processor and locale variables, with `TEMP`, `TMP`, `HOME`, `USERPROFILE`, `APPDATA` and
  `LOCALAPPDATA` set to `<workspace>/.tmp`. No proxy, `FABRIC_*`, `AZURE_*` or `PYTHON*` variable from the engine
  reaches it.
- `lib_roots`: read-only folders put first on `sys.path`, for learner libraries the engine builds
  (the Databricks path's generated PySpark package). A folder is refused if it is a link, holds or
  sits in the engine package, or holds the workspace or the data folder.
- What learner code can't do, by design, and what to do instead: change a DuckDB setting after
  connecting (the configuration is locked; pass settings to `duckdb.connect(config=...)`); load
  ICU or another extension (extensions aren't installed or autoloaded, and `HOME` is the workspace);
  read a Delta table with `DeltaTable.to_pyarrow_table()` or `to_pyarrow_dataset()` (they go
  through `PyFileSystem`, which is refused; use `deltalake.QueryBuilder`).
- On Windows, `workspace.links()` opens each file the validators read to get its link count, so
  opening a OneDrive cloud-only placeholder in an exercise folder downloads it first.
- The script, the workspace and its `.tmp` folder are resolved through `workspace.inside()`, so a
  link, a path outside the workspace or a device name is refused before anything runs.

## Python and Delta exercises (ACD-011)

`academy/pylab.py` is the `python_lab` and `delta_lab` adapter. An exercise's folder holds the
starter script (`starter: answer.py`) and the data files it reads (`files`); its grading folder holds
`solution.py` and at least two `wrong-*.py`. Grading copies only those listed files and the learner's
script into the workspace, runs the script with `sandbox.run`, and, once the process and its job have
ended, runs the checks in the engine on what is left:

| Check | Reads |
| --- | --- |
| `notebook_ran` | the run itself: it finished without an error (optional `prints`) |
| `artifact_present` | files and folders, through `workspace` |
| `config_matches` | a JSON file (`path`, `rules`) |
| `schema_matches`, `data_quality` | a parquet, CSV or JSON file loaded by the validators' locked DuckDB (a file it can't read fails, with the reader's message cut to 200 characters) |
| `delta_table` | the checked Delta log; no data file is opened |

A run that stops early (an error, the time or memory limit) is reported on its own, in words, and the
other checks are skipped. `exercises.problems()` refuses a Python exercise without a `.py` starter, a
`solution.py`, two wrong answers, plain file names in `files`, or with `uses` (a Learn SQL lab).
`exercises.self_test()` runs the reference and each wrong answer in a fresh workspace of its own.

## `pipeline_lab` (ACD-012)

Runs in the engine process on `orchestration/runner.py` with only local activity adapters and the
exercise's mocks: no Copy, no Fabric, no Web activity, no notebook activity unless it runs through
`python_lab`. Any activity type not on the allow list fails the run with a message. Pipeline JSON
comes from the workspace, so it is parsed with the same caps and its expressions are evaluated by
the existing expression engine only (no Python eval).

## Delivery

1. **PR-E (ACD-004)** `academy/validators.py`: `schema_matches`, `data_quality`, `config_matches`,
   `artifact_present`, `delta_table`, `pipeline_outcome`, plus `academy/workspace.py` (safe paths
   and caps). No learner code runs. Tests: each validator passes a reference and fails two planted
   wrong answers with a named reason; link, junction, absolute Delta path and size-cap refusals.
2. **PR-F (ACD-005)** `academy/sandbox.py` + `academy/_bootstrap.py`: L1 to L3. Tests are escape
   attempts: import `fabric_desktop` or `msal`, Polars write outside or https read, path traversal, symlink, reading `grading/`, socket, env token, subprocess, fork bomb,
   memory and time limit, writes outside, Reset. Windows CI runs the Job Object tests.
3. **PR-I (ACD-011)** `python_lab`/`delta_lab` adapters and `notebook_ran`.
4. **PR-H (ACD-012)** `pipeline_lab` and runMultiple exercises.
5. **ACD-005b** AppContainer, if Security asks for it before v1, it goes before PR-I.

These are engine-only; the exercise format (ACD-002, lead thread) wires them in. Each PR gets a
Security CLEAR and a Second quality PASS before Release management merges it.
