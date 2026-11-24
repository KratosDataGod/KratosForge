

# Mutation testing, batch 356

One module not scored before, run with `--kinds all`:
- The mastery class catalog: reading and checking the skills registry and the class files, the talent graphs' order and reach, and refusing names that are or resemble a god (`mastery/catalog.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 125 mutants, so all were run.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. The "after" figures come from `--only` runs of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mastery/catalog.py` | test_mastery_catalog_cov3, test_mastery (+ test_mastery_catalog_mutation) | 125 of 125 (118 + 7) | 116 / 9 | **92.8%** | 110/118 → 117/118 | 6/7 → 7/7 | 124 / 1 | **99.2%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **mastery/catalog.py**:
  - The error counts the problems past five from the sixth, and says nothing extra at exactly five.
  - A class version of 0 is refused, and a specialization id used twice is refused.
  - A class title or an achievement name that names a god is refused.
  - `known_ids()` gives all four kinds and counts draft Learn modules.
  - `load()` checks hooks by default, refuses a hook that names nothing, and returns a clean catalog.
  - → `test_mastery_catalog_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `mastery/catalog.py` | 254 | `lru_cache(maxsize=4)` to 5 | Equivalent: only how many catalogs stay cached changes, not what `load()` returns. |

# Mutation testing, batch 357

Two modules not scored before, run with `--kinds all`:
- The semantic model controls from a model's definition: relationships that filter both ways, row-level security, Direct Lake fallback and the definition-only model rules (`excellence/catalog_models.py`).
- The notebook and pipeline controls: the notebook rules and the pipelines' deactivated activities and fixed workspace ids (`excellence/catalog_artifacts.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. Each module has 79 mutants, so all were run.

Since ea988e99, #3162 added pipeline retry, timeout and failure-path controls to `catalog_artifacts.py`; this score is of the module as it was at ea988e99. The new tests also pass against it as it is on `main` today.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. The "after" figures come from `--only` runs of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog_models.py` | test_excellence_catalog, test_excellence_models, test_excellence_artifacts, test_catalog_artifacts_cov3, test_excellence_capacity, test_excellence_architecture (+ test_excellence_catalog_models_mutation) | 79 of 79 (77 + 2) | 54 / 25 | **68.4%** | 53/77 → 77/77 | 1/2 → 2/2 | 79 / 0 | **100%** | none |
| `excellence/catalog_artifacts.py` | the same (+ test_excellence_catalog_artifacts_mutation) | 79 of 79 (77 + 2) | 46 / 33 | **58.2%** | 46/77 → 77/77 | 0/2 → 2/2 | 79 / 0 | **100%** | none |

No bugs were found. The existing tests check these controls through whole assessments, by outcome; most survivors changed only a control's text, a count or a default.

### What the survivors were, and the tests that now kill them

- **excellence/catalog_models.py**:
  - Names list five and count the rest; one and many are worded apart.
  - A payload counts as read only with a model kind and a relationship count.
  - Every control says not evaluated, with its reason, when the definition wasn't read, and a rule without a rules object is not evaluated.
  - The exact texts of the both-directions, row security and fallback controls, and DirectQuery only named as such.
  - Fallback applies from one Direct Lake table. A rule hit without a count is 0 found.
  - → `test_excellence_catalog_models_mutation.py`.
- **excellence/catalog_artifacts.py**:
  - Places and names list five and count the rest.
  - A notebook rule's text for one place and many, a hit without a count, and a clean notebook's cell count (0 when not given).
  - The VACUUM control names 7 days.
  - An activity that couldn't be read is named once or many; the deactivated and fixed workspace texts and details for one and many.
  - → `test_excellence_catalog_artifacts_mutation.py`.

### Left

None.

# Mutation testing, batch 358

One module not scored before, run with `--kinds all`:
- The capacity and FinOps controls from imported Capacity Metrics exports: the usage record, failed work's share of CU, refreshes per day and a paid F capacity's use (`excellence/catalog_capacity.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 179 mutants, so 150 were sampled.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. The "after" figures come from `--only` runs of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog_capacity.py` | test_excellence_catalog, test_excellence_models, test_excellence_artifacts, test_catalog_artifacts_cov3, test_excellence_capacity, test_excellence_architecture (+ test_excellence_catalog_capacity_mutation) | 150 of 179 (139 + 11) | 105 / 45 | **70.0%** | 104/139 → 137/139 | 1/11 → 11/11 | 148 / 2 | **98.7%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/catalog_capacity.py**:
  - A CU value must be a number of 0 or more (0 counts; a bool or text doesn't).
  - Operations skip a row without CU and round to three places. The refresh days keep the top 20, name their workspace and item, tie by name, and count a day over only past 24 refreshes.
  - An items period must end after it starts and have some CU. The usage record lists each import it used, in id order.
  - CU-hours divide by 3,600. Failed work with a little CU is worked out, with none it is not evaluated, and exactly 10% fails. The share is rounded to four places.
  - Refreshes without operations are not evaluated; too many refreshes names five days and the hourly saving; exactly one day of operations is enough.
  - Compute-page periods that touch, in either order, both count. F0 isn't a paid size, F1 is. A paused capacity is named. Exactly seven days are enough, short periods are named in tenths of days, exactly 10% use passes, the benefit is the unused capacity, and the detail is rounded.
  - → `test_excellence_catalog_capacity_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `excellence/catalog_capacity.py` | 188 | `round(failed, 3)` to 4 places | Equivalent: `failed` is a sum of values already rounded to three places. |
| `excellence/catalog_capacity.py` | 282 | `round(used, 3)` to 4 places | Equivalent: `used` is a sum of items' CU already rounded to three places. |

# Mutation testing, batch 359

One module not scored before, run with `--kinds all`:
- The architecture controls: naming evidence (environment, layers, left-overs), lakehouse properties, notebook bindings, and the medallion, schemas, left-over and binding controls (`excellence/catalog_architecture.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 133 mutants, so all were run.

Since ea988e99, #3165 changed two lines of the module; this score is of it as it was at ea988e99. The new tests also pass against it as it is on `main` today.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. The "after" figures come from `--only` runs of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog_architecture.py` | test_excellence_catalog, test_excellence_models, test_excellence_artifacts, test_catalog_artifacts_cov3, test_excellence_capacity, test_excellence_architecture (+ test_excellence_catalog_architecture_mutation) | 133 of 133 (130 + 3) | 103 / 30 | **77.4%** | 101/130 → 130/130 | 2/3 → 3/3 | 133 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/catalog_architecture.py**:
  - The workspace subject uses its id and its display name or name. Naming skips an item without a type.
  - A lakehouse's record takes its own name and its default schema. Bindings are only for notebooks whose definition was read or not read. Properties are read only for lakehouses.
  - Names list five and count the rest, falling back to the id.
  - The exact medallion texts: one unnamed lakehouse, one or two mixed, and a split layer with its detail. Schemas name the default schema or dbo.
  - The left-over control's not evaluated texts (both words, or none), and one and many left-overs.
  - A missing binding, or one without its field, is unknown; the binding control needs both records.
  - → `test_excellence_catalog_architecture_mutation.py`.

### Left

None.

# Mutation testing, batch 360

One module not scored before, run with `--kinds all`:
- The proof runtime copied into both sides of a proof: pinned reads, sandboxed writes, the read-only guard, recorded file reads, fixed seed and time zone, and the saved log (`proof_runtime.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 75 mutants, so all were run; none was a boundary mutant. The Spark tests that also import this module (test_prove_spark, test_spark_writer, test_spark_merge and others) were left out to keep each mutant's run short; the three files run cover the module directly.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. The "after" figures come from `--only` runs of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `proof_runtime.py` | test_proof_runtime_edges, test_clock_changes_mid_run, test_prove (+ test_proof_runtime_mutation) | 75 of 75 (75 + 0) | 59 / 16 | **78.7%** | 59/75 → 74/75 | none sampled | 74 / 1 | **98.7%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **proof_runtime.py**:
  - A plain table is read with the pyarrow reader. Deletion vectors (reader or writer feature) or column mapping (feature or mode) are read through DataFusion. Stand-in tables only.
  - Storage options come from the side the runtime is bound to.
  - A flat lakehouse keeps tables without a schema folder; keys and shared paths default to dbo.
  - An overwrite isn't seeded from the pinned table unless it keeps rows.
  - The seed defaults to 0, seconds are rounded to milliseconds, and a file read records its SHA-256 and its size.
  - → `test_proof_runtime_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `proof_runtime.py` | 181 | the read size `1 << 20` to `1 << 21` | Equivalent: only how much is read at a time changes, not the hash or the size. |

# Mutation testing, batch 361

One module not scored before, run with `--kinds all`:
- Settings > Integrations on the engine: the local API's process and switch, policy refusals, connected tools and their permissions, status, pending approvals, the activity list and Test connection (`integrations/app_service.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 197 mutants, so 150 were sampled.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. The "after" figures come from `--only` runs of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/app_service.py` | test_local_retention_gaps, test_integrations_peer, test_integrations_app_service_cov3, test_integrations_app_edges (+ test_integrations_app_service_mutation) | 150 of 197 (147 + 3) | 90 / 60 | **60.0%** | 90/147 → 142/147 | 0/3 → 3/3 | 145 / 5 | **96.7%** | 5 equivalent |

No bugs were found. The local API's process, the audit trail, the executions list and the MCP client are stand-ins; nothing listens on a port.

### What the survivors were, and the tests that now kill them

- **integrations/app_service.py**:
  - The last line of a short log keeps its first character. The log rolls only past 5 MiB.
  - A tool's config has the API's URL only for HTTP. The API isn't allowed when the organization turns integrations off.
  - Starting the API when its folder is there, and stopping one that never started.
  - The API switch is refused when policy forbids it. Ports outside 1024 to 65535, or not a number, are refused; both ends are taken.
  - A new token comes back. Status, when the organization turns integrations off or the person turns them on, reports both and writes the launcher only when on. Turning integrations on is refused when the organization turned them off.
  - Pending follows the switch and the policy. Approvals (50 by default) and decisions pass through.
  - Activity reads 100 lines by default, shows a finished call once, looks for unrecorded approvals from its oldest line when the page is full, shows an approval with no line as unfinished, and shows an execution's state where it has one.
  - The audit check names its folder. Testing a stdio tool runs the stdio test, which lists the tools and the protocol with a stand-in client.
  - → `test_integrations_app_service_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `integrations/app_service.py` | 33, 132, 301, 309 | `HTTP_START_SECONDS` 15, the stop wait 10 s, the health check 5 s and the stdio test 60 s, each one second longer | Equivalent: only how long a wait may last changes. |
| `integrations/app_service.py` | 58 | `f.seek(0, os.SEEK_END)` to `f.seek(1, ...)` | Equivalent: the read window starts one byte later at most, and only the last line is used. |

# Mutation testing, batch 368

One module not scored before, run with `--kinds all`:
- The platform repository's files: records from the platform, TOML files for them, reading the repository back, comparing Git with what is deployed, exporting, and the deploy plan (`git/platform_files.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 425 mutants, and 150 were sampled. `main` hasn't changed this module since this run.

The run used test_platform_repo, test_platform_files_edges, test_platform_files_cov3, test_git_service_platform_mutation and test_regex_anchors, and scored 137 / 150 (91.3%).

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `git/platform_files.py` | test_platform_repo, test_platform_files_edges, test_platform_files_cov3, test_git_service_platform_mutation, test_regex_anchors (+ test_git_platform_files_mutation) | 150 of 425 (148 + 2) | 137 / 13 | **91.3%** | 136/148 → 148/148 | 1/2 → 2/2 | 150 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **git/platform_files.py**:
  - Only the shared landing zone lets dev and test read by default.
  - A blank line in a file's header comment is a bare `#`.
  - A long file name in a problem is cut to 200 characters.
  - A table compared with a list is one change, not field by field, and shortcut rules not deployed yet are compared field by field.
  - Export refuses to replace uncommitted files unless confirmed. It names up to 5 of them and adds "and more" from 6, and it counts the files it left unchanged.
  - A plan without a problems list, or with no admins anywhere and none ticked to remove, has no stop.
  - A new assignment step names its scope and target.
  - A deployed policy that Git also holds is never offered for removal.
  - → `test_git_platform_files_mutation.py`.

### Left

None.

# Mutation testing, batch 369

One module not scored before, run with `--kinds all`:
- The offline policy package: taking it from the platform's resolution, checking its hashes and parts, reading it from a file with a pinned hash and a maximum age, resolving from it alone, and saying how old it is (`central/policy_package.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 143 mutants, so all were run. `main` has changed this module since this run (`verify` checks the shape of each part, `read` also refuses a package nested too deep to read, and `resolve_offline` turns a broken package's errors into a refusal); the new tests pass against today's version too.

The first run used test_policy_package, test_policy_promote_gate and test_policy_package_gateway, and scored 101 / 143 (70.6%). Each survivor was run again with test_best_practice_checkers, test_checks_service_cov2 and test_policy_promote_gate_gateway added. That run killed none of the 42 survivors, so the "before" figures are the first run's.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/policy_package.py` | test_policy_package, test_policy_promote_gate, test_policy_package_gateway (+ test_central_policy_package_mutation) | 143 of 143 (137 + 6) | 101 / 42 | **70.6%** | 100/137 → 137/137 | 1/6 → 6/6 | 143 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/policy_package.py**:
  - A package is format version 1, and its hash is over sorted keys in ASCII JSON.
  - The platform is named by its function and workspace ids, with question marks when they are missing, or as the platform on this PC by file name.
  - No inheritance chain takes no package. Only exceptions for the package's own policies that are active and not ended go in.
  - `verify` refuses a newer or missing format version, and its messages give the first 12 characters of both hashes, or "missing".
  - A resolved policy whose version isn't in the package, or a package missing a part, is refused with the missing part named.
  - `read` says a missing file "isn't there". A maximum age that is negative, infinite or not a number is refused, and an age equal to the maximum is allowed.
  - An unreadable `taken_at` is "of unknown age", and one without a time zone is read as UTC.
  - `age` changes from minutes to hours at 60 minutes, and from hours to days at 48 hours.
  - `resolve_offline` passes the context on, and no context as an empty one.
  - → `test_central_policy_package_mutation.py`.

### Left

None.

# Mutation testing, batch 370

One module not scored before, run with `--kinds all`:
- A team asks for a source with a Copy job, and approving makes it: where the job lands, finding or making it once, its daily schedule, which waiting request lands where, a source kept while its Copy job is still in Fabric, and the landing zone naming each source's job (`central/landing_copy_service.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 139 mutants, so all were run; none is a boundary mutant. `main` hasn't changed this module since.

The first run used test_landing_copy_service_edges and test_landing_zones only, and scored 63 / 139 (45.3%). Each survivor was run again with test_landing_copy_requests, test_request_left_in_fabric, test_zone_delete_waiting and test_request_audit_trail added, which reach this module through the service, and the "before" figures are from that run.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/landing_copy_service.py` | test_landing_copy_service_edges, test_landing_zones, test_landing_copy_requests, test_request_left_in_fabric, test_zone_delete_waiting, test_request_audit_trail (+ test_central_landing_copy_service_mutation) | 139 of 139 (139 + 0) | 112 / 27 | **80.6%** | 112/139 → 137/139 | none | 137 / 2 | **98.6%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/landing_copy_service.py**:
  - A workspace is gone on a 404 or a not-found code, and not on a 403 or a 405. The admin API's answer decides, and a state of Deleted counts as gone.
  - Without one landing workspace, a job lands in the environment's LH_Landing, with no workspace name, zone or secure flag. An environment needs both its workspace and its LH_Landing ids.
  - Only waiting source requests that ask for a Copy job are told where they land, and none is without one landing workspace.
  - A new Copy job is made, recorded and scheduled, and its description keeps the reason (none too). The job this request made is found by its id, or by its marker in a name that differs only in case, and isn't recorded again. Its existing schedule is kept. A job of another type or name isn't it.
  - A request with no times gets no schedule, and one already scheduled keeps its schedule.
  - Only source requests for this source, with a job id, hold the source.
  - The landing zone shows the Copy jobs of approved source requests only, with the job's name or the default one, and no tables or times when the copy details are missing.
  - → `test_central_landing_copy_service_mutation.py`, with a stand-in catalog and Fabric.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `central/landing_copy_service.py` | 204 | `return False` to a bare `return` when the admin API fails with an error that isn't Fabric's | Equivalent: the only caller tests the result for truth, and `None` and `False` are both false. |
| `central/landing_copy_service.py` | 141 | a new job's `created` flag `True` to `False` | Equivalent: a new job's id is never the recorded one, so `made.get("copy_job_id") != item["id"]` still records it, and nothing else reads the flag. |

# Mutation testing, batch 371

One module not scored before, run with `--kinds all`:
- Enterprise governance on the desktop: the calls that pass policy changes on to the platform, a project's `governance.toml`, resolving the policies in force, and the exceptions about to end with their one Teams notice per organization (`central/governance_service.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 87 mutants, so all were run. `main` hasn't changed this module since.

The first run used test_governance only, and scored 26 / 87 (29.9%). Each survivor was run again with test_best_practice_checks, test_requests_core_four, test_central_roles, test_approvals_guard and test_policy_package added, which reach this module through the service, and the "before" figures are from that run.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/governance_service.py` | test_governance, test_best_practice_checks, test_requests_core_four, test_central_roles, test_approvals_guard, test_policy_package (+ test_central_governance_service_mutation) | 87 of 87 (86 + 1) | 61 / 26 | **70.1%** | 61/86 → 84/86 | 0/1 → 1/1 | 85 / 2 | **97.7%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/governance_service.py**:
  - Each policy call (a version, create, save or discard a draft, publish, set a status, applicability) sends its arguments and returns the platform's answer.
  - Assigning sends the settings, or none as `{}`, and unassigning; both return the policy again.
  - A policy shows `problems` only when its exceptions can't be read (an older UDF_Platform).
  - An exception with an `ended_at` isn't active even when its row says so. An end date with a time still counts its days. One that ends in exactly 14 days is ending; 15 isn't.
  - Resolving sends the environment only when one is named. A named domain and subdomain win over the open project's, and a missing one takes the project's. An entity the open project doesn't have adds no layer.
  - "For you" marks the person who asked, the open project and its domain or subdomain, and nothing else: not a project named like a domain, or a domain named like the project.
  - A claimed notice is sent, and what sending says is a problem only when there is something to say. An unclaimed one is left to the PC that claimed it. Quiet hours saved with seconds still let a notice be claimed.
  - → `test_central_governance_service_mutation.py`, with a stand-in platform that records each call.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `central/governance_service.py` | 217, 230 | `return []` to a bare `return` in `_claim_warnings` | Equivalent: its only caller tests the result for truth (`if events:`), and `None` and `[]` are both false. |

# Mutation testing, batch 372

One module not scored before, run with `--kinds all`:
- The kernel process's side of the protocol: the channel to the engine, the host that runs each request against the kernel, and taking or refusing each line the engine sends (`kernel_host.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 115 mutants, so all were run. `main` has changed this module since this run (the Prod guard passes on whether a write came from a cell); the new tests pass against today's version too.

The first run used test_kernel_host_coverage and test_secret_leaks, and scored 93 / 115 (80.9%). Each survivor was run again with test_debugger_robust added, which drives `dispatch` with malformed lines, and the "before" figures are from that run.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `kernel_host.py` | test_kernel_host_coverage, test_secret_leaks, test_debugger_robust (+ test_kernel_host_mutation) | 115 of 115 (114 + 1) | 102 / 13 | **88.7%** | 102/114 → 112/114 | 0/1 → 1/1 | 113 / 2 | **98.3%** | 2 can't be seen from a test |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **kernel_host.py**:
  - A protocol message is at most 64 MB, and a line of exactly the limit is read.
  - The refusal of a longer line names the limit in megabytes.
  - Each request runs on a daemon thread named for its op, cut to 40 characters, and the notice of a skipped line names at most 40 characters of the op.
  - → `test_kernel_host_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `kernel_host.py` | 293 | `if __name__ == "__main__"` negated, and `==` to `!=` | Can't be seen from a test: importing the module then runs `main()`, which reads the test process's stdin to its end and calls `os._exit(0)`, so the pytest process ends with exit code 0 and the run counts as passed. The kernel process itself is started with `-m`, where the guard is true. |

# Mutation testing, batch 373

One module not scored before, run with `--kinds all`:
- Talking to the platform: the SQLite platform on this PC with its "refused" rows, UDF_Platform in Fabric with how each answer is read, and issuing, checking and exporting surrogate keys in batches (`central/client.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 108 mutants, so all were run. `main` hasn't changed this module since.

The first run used test_central_client_cov3, test_request_retries, test_platform_retries, test_typed_errors and test_reply_text_one_line, and scored 71 / 108 (65.7%). Each survivor was run again with test_shared_keys, test_platform, test_platformtest, test_versioned_contracts and test_central_service_cov3 added, and the "before" figures are from that run.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/client.py` | test_central_client_cov3, test_request_retries, test_platform_retries, test_typed_errors, test_reply_text_one_line, test_shared_keys, test_platform, test_platformtest, test_versioned_contracts, test_central_service_cov3 (+ test_central_client_mutation) | 108 of 108 (106 + 2) | 100 / 8 | **92.6%** | 99/106 → 106/106 | 1/2 → 2/2 | 108 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/client.py**:
  - Keys are issued 20,000 to a call by default: 20,001 keys take two calls.
  - A missing key table that the person can't make is refused with the way to request it.
  - `exists` is what the platform's key statistics say.
  - A batch without a count of new keys adds none.
  - A refused change gets its "refused" row, and a refused read doesn't.
  - Each call is posted with a five-minute timeout.
  - An answer of exactly 400 with a JSON object and no errors is still the platform's refusal, with the answer's text.
  - → `test_central_client_mutation.py`, with stand-in answers and a stand-in key service.

# Mutation testing, batch 374

One module not scored before, run with `--kinds all`:
- Entra ID groups for a domain: the suggested group names, the plan of groups each role needs, and the PowerShell, Azure CLI, CSV and Fabric-role scripts made from it (`central/entra.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. Of `test_csv_cells.py` only `test_the_listing_exports_protect_and_count` was run, the one test there that reaches this module: the whole file takes over a minute a run. The module has 137 mutants, so all were run; none is a boundary mutant. `main` hasn't changed this module since.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/entra.py` | test_entra_service_mutation, test_entra_groups, test_entra_graph, test_csv_cells (one test) (+ test_central_entra_mutation) | 137 of 137 (137 + 0) | 114 / 23 | **83.2%** | 114/137 → 137/137 | none | 137 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/entra.py**:
  - A role can't be changed once made.
  - The domain part of a group name is the domain's id unless the naming says `name`. A name is cut to 256 characters and a mail nickname to 64.
  - A domain that isn't there is refused. A naming without `roles_off` plans every role.
  - A domain without a name is called by its id. The environment's words go into a description only for a role given per environment. A domain's Fabric domain is kept, and one without is empty.
  - Only new groups with names a script can't carry are left for by hand; a linked group never is.
  - The header names the first row's domain (none for no rows), who made it only when known, and "1 new group" or "2 new groups".
  - The CSV leaves a linked group's mail nickname empty.
  - The roles script names each Fabric role: a domain role waiting for the domain to be in Fabric, a domain role given, a workspace role with no workspace known yet, and the readers' by-hand step. A role with no Fabric role (stewards) adds nothing.
  - → `test_central_entra_mutation.py`.

# Mutation testing, batch 406

One module not scored before, run with `--kinds all`:
- The Academy's validators: deterministic, explained checks of what a learner made (a table's schema, data-quality rules, row counts, settings documents, files, Delta tables read from their log, tables left unchanged), each saying what is wrong in words (`academy/validators.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 499 mutants, so 150 were sampled. `main` hasn't changed this module since.

The test set was test_academy_validators and test_academy_configlab, which reaches the settings checks through the config lab.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/validators.py` | test_academy_validators, test_academy_configlab (+ test_academy_validators_mutation_b) | 150 of 499 (142 + 8) | 114 / 36 | **76.0%** | 113/142 → 142/142 | 1/8 → 8/8 | 150 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/validators.py**:
  - The ordered-columns message names the columns, and a pass says "and key" only when a key is set.
  - A range rule with no bounds, and a rule nobody knows, are refused. A range message names both bounds.
  - row_count fails below its minimum, and a message with only a maximum reads "between 0 and".
  - Values are said by their shape: "an object", a text of 40 characters shown and 41 not, "café" not escaped, a list of 8 shown and 9 counted.
  - count_max allows exactly the maximum. contains fails on a missing item and on a number.
  - A delta item that is a file fails, a file under min_bytes fails, and a json item that isn't JSON fails.
  - Operations out of order pass unless the order is asked for, and asking for order with no operations checks nothing.
  - A long history is cut to eight names ("and 1 more"), and an operation name of 41 characters is cut to 40 with "…".
  - versions_min and versions_max allow exactly their bound. Asking for no partitions says so. files_min and files_max hold at the edge, and an average file size equal to the minimum passes.
  - One version and one data file are said in the singular.
  - unchanged with nine changed tables says "and 1 more"; with eight it doesn't, and the pass message lists eight tables, then says others hold.
  - → `test_academy_validators_mutation_b.py`, with Delta logs written by hand as commit JSON; no data file is read.

# Mutation testing, batch 407

One module not scored before, run with `--kinds all`:
- Academy exercises and their verification classes: reading an exercise and its reference answer, whether a pass completes an objective, grading a duckdb_lab answer in memory, sending each adapter to its own self-test, and the checker that finds broken exercises without running anything (`academy/exercises.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`. The module has 317 mutants, so 150 were sampled. `main` hasn't changed this module since.

The test set was test_academy_exercises, test_academy_readiness, test_academy_databricks_pack, test_academy_databricks_content and test_academy_limits, with `-k "not reference_passes and not starter_does_not_pass and not runaway and not never_ends and not wrong_answer"`. That leaves out 91 tests that run every exercise's reference solution, starter and planted wrong answers, which take about ten minutes per mutant; 98 tests ran. Leaving them out can only lower the "before" figure, and the new tests reach the same lines without running a learner's answer.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/exercises.py` | test_academy_exercises, test_academy_readiness, test_academy_databricks_pack, test_academy_databricks_content, test_academy_limits, 91 deselected (+ test_academy_exercises_mutation_b) | 150 of 317 (146 + 4) | 104 / 46 | **69.3%** | 104/146 → 146/146 | 0/4 → 4/4 | 150 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/exercises.py**:
  - self_test sends python_lab and delta_lab, pipeline_lab, config_model, git_lab, sqlproj_lab, quiz, dbsql_lab and pyspark_lab exercises each to its own self-test. A duckdb_lab exercise is graded here, with its reference passing and each planted answer failing. Any other adapter is refused.
  - Knowledge is weaker than a simulation, and local evidence is weaker than a platform's. An unknown best_local counts as LOCAL VERIFIED.
  - get refuses an exercise that isn't there. The list of exercises holds drafts unless asked not to.
  - An exercise built on a Learn lab takes the lab's starter. The planted answers come from the grading folder, and there are none when it holds none.
  - SQL that fails to run fails its check, with the reason.
  - The checker: the version starts at 1, minutes run from 1 to 240 at both edges, and a Fabric pack can't hold Databricks work. Blank hints, a prerequisite exercise that doesn't exist and a fault without all its parts are caught.
  - Each adapter's own check problems are reported: pipeline_outcome, config_matches, quiz_score and repo_matches.
  - A pyspark result check needs a table. A duckdb_lab check takes no table and no hidden data, and a hidden-data check needs hidden.sql.
  - A missing child pipeline and a missing notebook are caught. files_folder is only for pyspark_lab, and a pipeline's mocks must be a map.
  - Two planted answers are enough and one is too few, for config_model, git_lab and sqlproj_lab.
  - A grading folder with no exercise is caught.
  - → `test_academy_exercises_mutation_b.py`, on copies of the bundled packs. The adapters' self-tests are stood in for, so nothing runs a learner's answer; the one real run is a duckdb_lab reference in the in-memory `grade`.

# Mutation testing, batch 408

One module not scored before, run with `--kinds all`:
- Certification change detection: reading the skills list from a vendor's published study guide as untrusted HTML, lining it up with a pack's registry, and the report and crosswalk of what was added, removed or reworded (`academy/guide_check.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. test_academy_guide_check reads `sidecar/tools/academy_guide_check.py` and nothing in `docs/`, `examples/` or `app/`, so nothing was left out. The module has 145 mutants, so all were run. `main` hasn't changed this module since.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/guide_check.py` | test_academy_guide_check (+ test_academy_guide_check_mutation_b) | 145 of 145 (143 + 2) | 129 / 16 | **89.0%** | 129/143 → 143/143 | 0/2 → 2/2 | 145 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/guide_check.py**:
  - A page of exactly 4 MiB is read, and one byte more is refused as over 4 MB.
  - Entities such as `&amp;` are decoded.
  - Text inside a list item in a heading belongs to the item, not to the heading.
  - A certification whose guide lists objectives straight under each section is refused for this reader.
  - Objectives exactly as alike as the threshold count as reworded, and the most alike pair is matched first.
  - A removed skill area has no new id, and the crosswalk says so.
  - A report cell is cut at 400 characters. The report names the page it compared and its date, or says the date isn't shown, and a report with no source says "Compared None".
  - → `test_academy_guide_check_mutation_b.py`, on small hand-written pages; nothing is fetched.

# Mutation testing, batch 409

One module not scored before, run with `--kinds all`:
- The Academy's engine service: certification packs, exercises, the learner's workspaces, hints and explanations, and recording a pass in Profile without ever changing the result (`academy_service.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. test_academy_appcontainer skips itself off Windows, so it ran no tests here. The module has 33 mutants, so all were run. `main` hasn't changed this module since.

The test set was test_academy_lab, test_academy_configlab, test_academy_quizlab, test_academy_sqlprojlab, test_academy_lab_python, test_academy_gitlab and test_academy_appcontainer, which reach this module through the service.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy_service.py` | test_academy_lab, test_academy_configlab, test_academy_quizlab, test_academy_sqlprojlab, test_academy_lab_python, test_academy_gitlab, test_academy_appcontainer (+ test_academy_service_mutation_b) | 33 of 33 (33 + 0) | 24 / 9 | **72.7%** | 24/33 → 31/33 | none | 31 / 2 | **93.9%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy_service.py**:
  - An exercise built on a Learn lab shows the lab's lesson, with its prerequisites, what is expected, the remediation and its three hints.
  - Saving an answer says it was saved.
  - A pass with the reference solution is recorded, and the Profile row doesn't say the answers changed.
  - → `test_academy_service_mutation_b.py`, on E37 in a workspace under the test's own folder.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `academy_service.py` | 89 | `answers_changed` default `False` to `True` | Equivalent: `lab.validate` always returns `answers_changed`, so the default is never used. |
| `academy_service.py` | 96 | hint `level` default 1 to 0 | Equivalent: `lab.hint` raises the level to at least 1. |

# Mutation testing, batch 410

One module not scored before, run with `--kinds all`:
- Academy assessments: practice exams, domain diagnostics and objective drills built from a pack's questions, with an honest clock driven by heartbeats and a result scored once at Finish (`academy_exam_service.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 99 mutants, so all were run. `main` hasn't changed this module since.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy_exam_service.py` | test_academy_exam_service (+ test_academy_exam_service_mutation_b) | 99 of 99 (98 + 1) | 79 / 20 | **79.8%** | 79/98 → 98/98 | 0/1 → 1/1 | 99 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy_exam_service.py**:
  - A practice exam for a pack with no published exam takes 50 questions in 100 minutes, and one with a published exam takes no more questions than the bank holds.
  - A diagnostic asks two questions per objective.
  - A domain or objectives that the certification doesn't have are refused in words, and so is an empty list.
  - A drill is titled by its objectives up to three, and by a count from four.
  - Answering returns the session's view, and flagging flags unless told not to.
  - A heartbeat after Finish says the exam is finished and not running.
  - A result breaks the score down by domain and by objective.
  - The home screen counts the questions per objective, and an objective with none shows 0.
  - → `test_academy_exam_service_mutation_b.py`.

# Mutation testing, batch 411

One module not scored before, run with `--kinds all`:
- Granting the exercise sandbox read and execute on the Python install, one allow entry per path through the Windows security API, after checking whether the path already holds exactly that grant (`academy/acl.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 78 mutants, so all were run. `main` hasn't changed this module since.

test_academy_acl skips itself off Windows, so on this Linux machine no test reached the module and every mutant survived. The new tests stand in for advapi32 and kernel32 with the real ctypes structures, so they run on every OS. The "after" figure is a full rerun.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/acl.py` | test_academy_acl (+ test_academy_acl_mutation_b) | 78 of 78 (78 + 0) | 0 / 78 | **0%** | 0/78 → 75/78 | none | 75 / 3 | **96.2%** | 3 Windows only |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/acl.py**:
  - The access, inheritance and mode constants have the values Windows defines.
  - The API is built once and then reused.
  - Paths get the long form, for drive paths and for UNC shares, and a failure names the call and its error.
  - A path counts as granted only with an explicit allow entry for the capability's SID, with read and execute and exactly the inheritance flags this module sets. A deny entry, an inherited one, other flags, missing bits or another SID don't count. A wider entry does. A NULL DACL doesn't.
  - When reading the permissions fails at any step, the error says so and the security descriptor is freed. A SID that won't convert is an error.
  - The grant sends one allow entry with read and execute to the SID and sets the DACL, protected only when asked. When a change fails, what was allocated is freed, and a failed read stops the grant.
  - → `test_academy_acl_mutation_b.py`, with a stand-in for advapi32 and kernel32; no real permission is read or changed.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `academy/acl.py` | 67, 68 | `use_last_error=True` to `False` when loading advapi32 and kernel32 | Not equivalent, but Windows only: the line loads `ctypes.WinDLL`, which doesn't exist on Linux. test_academy_acl, which runs only on Windows, is where it can be reached. |
| `academy/acl.py` | 89 | `return api` dropped at the end of `_api` | Not equivalent, but Windows only, as above. |
