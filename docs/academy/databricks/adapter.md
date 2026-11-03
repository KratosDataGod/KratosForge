# Databricks execution adapter (DBA-002, DBA-013)

One adapter, three modes, built from the Databricks pieces KratosForge already has. It holds no
credentials of its own and adds no second connection manager.

```
Academy exercise (pack dbx-de-associate)
        │  adapter: pyspark_lab | dbsql_lab | delta_lab | model (copyinto, autoloader, ldp, jobs, uc, bundle) | databricks
        ▼
academy/databricks.py  ── mode: local ──► sandbox.py (learner process, no tokens) ─► Delta in the workspace ─► validators
                       ── mode: simulated ► the story's model (DBA-005..010), engine process, data only ─► validators
                       ── mode: remote ──► preflight ─► confirm ─► run ─► platform_check ─► cleanup
                                              │            │          │
                     engines/databricks_discovery   engines/authorization   engines/databricks_sql | engines/dbconnect
                     (GET only, no DBU)             (names compute + cost)  (existing remote engines)
                                              │
                     connprofiles + engines/databricks_auth (CLI profile, OAuth U2M/M2M; tokens in memory only)
```

## Modes

| Mode | Runs | Class it can give |
| --- | --- | --- |
| local | The learner's code in the Academy sandbox on this PC | LOCAL VERIFIED (or SIMULATED where the engine isn't the real one for what is taught) |
| simulated | KratosForge's model of a Databricks service, fed the learner's document or code as data | SIMULATED |
| remote | The learner's own workspace, their identity, after their confirm | DATABRICKS VERIFIED |

## Remote run sequence

1. **Sign-in.** The learner picks a connection profile (Settings > Connections). No profile: the
   exercise stays local and says what a remote run would add.
2. **Capabilities.** Discovery reads who the learner is, warehouses, clusters, Unity Catalog
   (read-only GETs, no DBU). The exercise's `requires` (for example `unity_catalog`,
   `serverless_jobs`, `sql_warehouse`) is compared; anything missing is named.
3. **Permissions.** The privileges the exercise needs are listed (for example `CREATE SCHEMA` on a
   catalog the learner chooses). No admin rights are asked for; an exercise that needs them is
   marked so in the catalog and has a local version.
4. **Resources and cost.** The plan lists what will be created (schema `kf_academy_<run>`, a job,
   a pipeline) and what compute runs, with the documented billing basis. Nothing is provisioned yet.
5. **Confirm.** `engines/authorization.py`'s confirm, naming compute and expected spend. In this
   project, any such run also needs Chris's typed OK.
6. **Run.** Through the existing SQL warehouse or Databricks Connect engine. Created objects are
   tagged `kratosforge-academy=<pack>/<exercise>/<run>` where the object supports tags, named with
   the `kf_academy_<run>` prefix, and written to the run ledger before the call returns.
7. **Verify.** `platform_check` read-only reads (table history, grants, job run state). Result
   comparison: the remote table's rows are compared with the local run's, and differences are
   shown, not hidden.
8. **Cleanup.** Only objects in the ledger **and** carrying the tag or prefix; anything else is
   refused even if the learner asks. The ledger survives a crash; the next start lists unfinished
   runs and offers cleanup or recovery.

## Error reporting

Every remote failure maps to a sentence and a next step: signed out (sign in again), missing
privilege (which one, on what), capability missing (which feature), compute unavailable, cost
confirm declined, cleanup partial (what is left and why). The raw response is kept for the support
bundle after redaction.

## Boundaries

- Tokens never enter the learner process, the workspace folder, evidence rows or logs.
- The learner process never calls Databricks; only the engine does, after the confirm.
- No Databricks package is installed into the engine (ADR-004); Databricks Connect keeps its own
  runtime process.
- Security QC reads the adapter, the ledger and the cleanup before merge.
