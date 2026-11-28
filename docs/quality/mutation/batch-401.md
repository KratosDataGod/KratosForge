# Mutation testing, batch 401

One module not scored before, run with `--kinds all`:
- The platform's central store: domains, projects, products, sources, keys, requests and their audit (`central/core.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged; no mutant's run timed out. Each pytest session got a fresh temporary folder. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. The module has far more mutants than the cap, so 150 were sampled.

33 test files were run. `test_authz_matrix` was left out because it reads a file outside `sidecar/`, and the first attempt failed its baseline in the copy of `sidecar/` for that reason. `test_policy_promote_gate` and `test_platform` were left out for time: together they add about a minute to every one of the 150 runs.

The "before" run was cut twice by restarts of the machine. The first cut was at 98 mutants and was run again. The second cut was at 133; the log lines of those 133 were kept, and the 17 sampled mutants it hadn't reached were run with `mutate.py --only` (12 killed, 5 survived). The two parts together cover the same 150 mutants as one uncut run.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. The "after" figures come from `mutate.py --only` runs with the new tests added: the 52 survivors in six parts (23 killed), then a second pass of tests for the 28 still left, run again with `--only` on the same 33 files (28 killed). Each of those 28 kills was checked by hand: the mutant was applied and a named new test failed.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/core.py` | 33 files: test_approvals_guard, test_best_practice_checks, test_central_client_cov3, test_central_roles, test_central_upgrades, test_change_window, test_domain_delete_uses, test_entra_graph, test_entra_groups, test_governance, test_landing_zones, test_platform_refusals, test_platform_retries, test_platform_service_paths, test_policy_package_gateway, test_product_groups, test_product_promises, test_product_status, test_shared_keys, test_shortcut_center, test_shortcut_fixes, test_shortcut_request_retries, test_shortcut_service_paths, test_sqlite_stores, test_trust_badges, test_trust_service_edges, test_versioned_contracts, test_workload_audit, test_zone_delete_waiting, test_meta_keys_edges, test_requests_core_four, test_requests_service_cov3, test_request_retries (+ test_central_core_mutation_b) | 150 (149 + 1) | 98 / 52 | **65.3%** | 97/149 → 148/149 | 1/1 → 1/1 | 149 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/core.py**, first pass (23 of the 52):
  - Making the key table says it was made, and the product status table is listed once it is made.
  - A subdomain of a missing domain, and a project in a missing domain, are refused.
  - A status report of more than 500 products is refused, and so is an unknown classification.
  - Saving a term gives back what was saved, and a group can't be a platform admin.
  - A source row checks its connections, keeps its active flag, takes hours with a decimal point and refuses a secret in its connections.
  - Each new source takes the next id, and deleting one lists the rest.
  - A data request says what it needs, and a change request names a published product and its owners.
  - Joining a subdomain of another domain is refused.
  - `whoami` lists only the domains the caller owns, and a registered key table is listed with its own business key.
  - → `test_central_core_mutation_b.py`.

- **central/core.py**, second pass (the other 28):
  - A setting read from a store without the settings table is empty text, not nothing. Closing keys is audited as "close keys" with its reason, and reopening as "reopen keys".
  - A Fabric domain added by the import keeps its description. Importing it again changes nothing, and a domain renamed in Fabric is updated.
  - A project keeps its team and its landing readers. A published environment keeps the product's deprecation date.
  - A first publish that names other people as owners is refused. A product's owner sees who follows it.
  - A public product, or a domain product the project was granted, links from any domain; a domain product without a grant is still refused.
  - A platform admin certifies a project that has no domain. A project known only by a product is in the catalog.
  - A project's stored members are listed as they are; an older project with none lists its earlier people.
  - Moved sources are audited against the project. Asking for a source the platform has, or for access a project already has, is refused.
  - A shortcut request for a product is decided by the product's owners.
  - Taking access away also drops its review. A product's owner, not an admin, reviews the access to their own product.
  - Owner coverage names "No owner" and an owner never seen. Seen is taken from the audit as well as the person table. Owners seen lately, or written as names, are covered, and without presence nobody is checked for being quiet.
  - Anyone may report a shortcut broken. Shortcut changes go back 30 days by default.
  - → `test_central_core_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `central/core.py` | 495 | `TSQL_ROWS_PER_INSERT` 500 to 501 | Equivalent: the batch size of the generated inserts changes, not what is inserted. 501 rows of three parameters is 1,503, still under SQL Server's limit of 2,100. |
