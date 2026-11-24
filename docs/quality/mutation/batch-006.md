# Mutation testing, batch 6

The policy and approval gates that a pull request isn't already changing:
- The settings kept sealed with DPAPI: webhooks and route addresses (`sealed_settings.py`).
- The SQL statement policy for the warehouse (`sql_policy.py`).
- The integrations policy: which integrations may run and who may edit them (`integrations/policy.py`).
- The App Studio model policy: allowed providers and hosts (`appstudio/policy.py`).
- The Git sync gates for Fabric workspaces (`fabric_git_service.py`).
- The warehouse SQL path, including its refusal of change statements and dynamic SQL where they aren't allowed (`warehouse.py`).
- The central policy and its promotion gate (`central/policy.py`).
- The local data service (`localdata_service.py`).

The run was at `main` 7c3dbd0ec (2026-11-12), with `sidecar/tools/mutate.py` and the same method as batches 1 to 5 (sample cap 150, seed 7). `warehouse.py` and `central/policy.py` have more points than the cap, so 150 of each were sampled. Every mutant of the other modules was run.

Owners: the Governance thread owns the policy modules, the Warehouse thread owns Prod writes, and the Data retention thread owns the audit and receipt time limits (not in this batch, see below).

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Equivalent left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `sealed_settings.py` | test_sealed_settings (+ test_sealed_settings_mutation) | 56 | 52 / 4 | **92.9%** | 56 / 0 | **100%** | 0 |
| `sql_policy.py` | test_sql_exec, test_warehouse_queries, test_tsql_contract | 130 | 130 / 0 | **100%** | 130 / 0 | **100%** | 0 |
| `integrations/policy.py` | test_policy_fail_closed, test_integrations_permissions, test_integrations_executions (+ test_integrations_policy_mutation) | 49 | 45 / 4 | **91.8%** | 49 / 0 | **100%** | 0 |
| `appstudio/policy.py` | test_appstudio_connections, test_appstudio_edit, test_appstudio_generate (+ test_appstudio_policy_mutation) | 60 | 51 / 9 | **85.0%** | 60 / 0 | **100%** | 0 |
| `fabric_git_service.py` | test_fabric_git, test_audit (+ test_fabric_git_service_mutation) | 78 | 63 / 15 | **80.8%** | 78 / 0 | **100%** | 0 |
| `warehouse.py` | test_warehouse_connect, test_warehouse_edges, test_warehouse_precondition, test_warehouse_queries, test_parse_errors_plain, test_safe_parse_readers, test_sql_worker_inprocess (+ test_warehouse_mutation) | 150 of 309 | 124 / 26 | **82.7%** | 150 / 0 | **100%** | 0 |
| `central/policy.py` | test_best_practice_checks, test_central_roles, test_central_upgrades, test_domain_delete_uses, test_file_text_one_line, test_governance, test_platform_retries, test_policy_package, test_policy_promote_gate, test_requests_core_four | 150 of 653 | 150 / 0 | **100%** | 150 / 0 | **100%** | 0 |
| `localdata_service.py` | test_local_data, test_local_data_calls, test_local_delta, test_local_delta_dml, test_local_explain, test_local_transform, test_spill_leftovers, test_sql_exec | 63 | 63 / 0 | **100%** | 63 / 0 | **100%** | 0 |

The "after" numbers re-run each survivor with `--only` and the new tests in.

### What the survivors were, and the tests that now kill them

- **sealed_settings.py**: nothing checked that a value isn't unsealed when DPAPI isn't there, that a webhook set again replaces one that can't be read while an empty one keeps it, that the same holds for route addresses, or that a route without a url passes through unchanged. → `test_sealed_settings_mutation.py`, with the same stand-in DPAPI as `test_sealed_settings.py`.
- **integrations/policy.py**: the answer for a policy file that can't be read was only checked in part, and the editors setting was only checked as true. → `test_integrations_policy_mutation.py` pins the whole fail-closed answer, the defaults, both editors values and a wrong-type value.
- **appstudio/policy.py**:
  - The fail-closed answer was only checked in part.
  - A providers or hosts value that isn't a list.
  - The 40- and 60-character cuts and the limit of 5 names in the problem text.
  - The refusal text with and without an allowed list, and a host matched without regard to case.
  - → `test_appstudio_policy_mutation.py`.
- **fabric_git_service.py**:
  - The item-support answer, for every type and for one.
  - A link file that can't be read, for status and for update.
  - Other Fabric errors passing through status.
  - The text for an update without a Test link.
  - Every gate answer saying it didn't run.
  - Nothing to update.
  - An overview where Fabric refuses the read.
  - → `test_fabric_git_service_mutation.py`, with the fixtures of `test_fabric_git.py`.
- **warehouse.py**:
  - Change statements numbered across GO batches.
  - Names checked inside brackets, and a comment between words keeping them apart (`EXEC/* … */sp_executesql`).
  - OPENROWSET, OPENQUERY and OPENDATASOURCE.
  - The frozen endpoint and the missing-server text.
  - The access-token attribute number and each transient login error number.
  - The retry log text.
  - The name and column limits of a precondition at their edges.
  - A change's row count, commit and close.
  - An empty result.
  - When progress is said.
  - The Arrow types for a one-digit decimal and an all-null datetime column.
  - → `test_warehouse_mutation.py`, with stand-in connections. Progress replaces only `warehouse`'s own `time` name, not the process-wide one.

### Equivalent mutants (left on purpose)

- None.

### Real bugs found

- None. Every killed survivor was a missing test, not wrong behavior.

### Not in this batch

- `app_settings.py` (the policy ceilings and what a setting may loosen), which open pull requests still change.
- `policy_schema.py`, `policy_watch.py`, `policy_trust.py` and `registry_policy.py`, which an open pull request still changes.
- `audit.py` (writing the audit record, and failing closed when it can't), which an open pull request still changes.
- `audit_chain.py` and `retention.py`, which an open pull request still changes.
- `service.py`, which holds the typed-name check for Prod writes and other approval paths, and which several open pull requests still change.
- `lakehouse.py`'s write refusal, already at 100% in batch 2.
- The publish confirmation in `appstudio_service.py`, left for a later batch because the module is large.

