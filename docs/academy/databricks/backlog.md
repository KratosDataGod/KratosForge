# Databricks path backlog: DBA-001 to DBA-016

Priorities: P0 for the first vertical slice, P1 phase work, P2 later. Owners are project threads
([threads](delivery-plan.md#threads)). Every story's definition of done also includes the
[quality gates](verification.md#quality-gates): unit and integration tests, validated reference
solutions, Security read where code runs or credentials are touched, accessibility checks for UI,
docs, evidence on the PR, Second quality's review and Release management's merge. Nothing extends a
system DP-700 already has by copying it; each story names the module it extends.

### DBA-001
**Databricks certification curriculum pack (P0, Phase 1).**
- **Scope:** `packs/dbx-de-associate/` (`pack.json`, `certification.json` with all 33 objectives word
  for word, weights, the May 4 wording where it differs, competencies, exercises, validators, docs
  links, gap records G-01 to G-10). Make the shared registry platform-aware instead of Fabric-only:
  a pack declares `platform` (`fabric` or `databricks`); the platform flag becomes
  `platform_required`, the platform check `platform_check`, the platform class
  `fabric_verified`/`databricks_verified` by platform, doc hosts by platform; readiness's
  `fabric_verified` measure becomes `platform_verified`. DP-700 data is migrated in the same PR.
  Exercise format gains `difficulty`, `troubleshooting` and `cleanup` for both packs.
- **Depends on:** #1776, #1778, #1779 (stacked on #1782).
- **Acceptance:** `registry.problems()` and `exercises.problems()` empty for both packs; 33
  objectives, weights sum to 100; every objective that needs Databricks has a gap record and
  `platform_check`; no Databricks exercise can be FABRIC VERIFIED or the reverse; DP-700 tests pass
  unchanged in meaning.
- **Tests:** registry tests for both packs; platform cross-claims refused; snapshot hash; crosswalk.
- **Owner:** this thread. **Evidence:** CI run, `problems()` output, coverage counts.

### DBA-002
**Databricks execution adapter and connection manager (P1, Phase 3).**
- **Scope:** `academy/databricks.py` ([architecture](adapter.md)): modes local, simulated, remote;
  capability detection via `engines/databricks_discovery.py`; sign-in via existing connection
  profiles and `engines/databricks_auth.py` (no new credential store); remote runs through the
  existing SQL warehouse and Databricks Connect engines and `engines/authorization.py`; result
  comparison; error mapping. No connection manager of its own: Settings > Connections is reused.
- **Depends on:** DBA-001, DBA-013 (ledger), #1738 (connections, draft).
- **Acceptance:** without a profile every remote step says so and the exercise stays local; a remote
  run never starts without a confirm that names the compute and the expected cost; tokens never
  reach the learner process or disk.
- **Tests:** fakes for every documented API; refusal paths; no network in local mode.
- **Owner:** this thread with Lead Databricks and Snowflake parity. **Security read:** required.
- **Evidence:** fake-backed tests; a dry run against Databricks Free Edition only after Chris's typed OK.

### DBA-003
**Local PySpark and Delta exercise validation (P0, Phase 1).**
- **Scope:** `pyspark_lab` adapter: the learner's PySpark runs in the Academy sandbox (PR-F) on
  DuckDB's PySpark API with a small Academy-owned shim for `saveAsTable`/`read.table` over Delta
  tables in the workspace (the sandbox refuses `fabric_desktop` imports, so `spark_local.py` can't
  be imported there). Grading reads the resulting Delta tables. Engine label "DuckDB's PySpark API
  on this PC (not Apache Spark)". Open decision: optional real Apache Spark on this PC for Spark
  semantics (G-05).
- **Depends on:** DBA-001, #1777 (validators), PR-F (sandbox).
- **Acceptance:** E19 reference passes, its planted wrong answers fail with explained messages, a
  rerun gives the same table (idempotent), and a pass writes an evidence row that moves 3.1.
- **Tests:** self test in CI; sandbox escape tests stay green; shim behaviour tests.
- **Owner:** this thread, sandbox thread reviews the runner. **Security read:** required (runs learner code).

### DBA-004
**Databricks SQL exercise support (P1, Phase 2).**
- **Scope:** `dbsql_lab` = `duckdb_lab` with the Databricks dialect (sqlglot, as `engines/local_sql.py`
  does). An exercise may not depend on a function the parity table marks `differs`, `varies`,
  `error` or `untranslated`; `problems()` checks the reference solution's functions against it.
- **Depends on:** DBA-001, #1782.
- **Acceptance:** E20 and E25 live; a solution that uses a non-`same` function fails `problems()`.
- **Tests:** self test; parity guard test. **Owner:** content thread with this thread.

### DBA-005
**Auto Loader training and file-arrival simulation (P1, Phase 2).**
- **Scope:** `landing` dataset generator (batches, schema change, malformed file); a file-arrival
  clock; models of `cloudFiles` (schema location, inference, hints, the five documented
  `schemaEvolutionMode` values, rescued data column) and `COPY INTO` (loaded-file tracking, `force`,
  `mergeSchema`), built on `spark_stream.py`'s checkpoint logic. Results are SIMULATED.
- **Depends on:** DBA-003. **Acceptance:** E08 to E12 pass and fail as designed; each mode's
  behaviour matches the documented sentence it cites. **Tests:** one per mode and option.
- **Owner:** content thread; Spark functions locally thread reviews the streaming reuse.

### DBA-006
**Lakeflow Spark Declarative Pipelines training (P1, Phase 2).**
- **Scope:** a local model that reads a pipeline's Python (`dp`/`dlt` decorators) or SQL dataset
  definitions as data (never by importing Databricks packages), builds the dataset graph, runs each
  dataset's query on the `pyspark_lab` path, applies expectations (warn, drop, fail) and records
  metrics in an event-log-shaped record. Materialized views and streaming tables are modelled.
- **Depends on:** DBA-003, #1777 `pipeline_outcome`. **Acceptance:** E28 to E30 live; an
  expectation's action changes the output exactly as documented. **Owner:** this thread.

### DBA-007
**Lakeflow Jobs orchestration and failure simulation (P1, Phase 3).**
- **Scope:** read a job definition (Jobs API JSON or bundle YAML); validate the task graph; a local
  run model with `depends_on`, the six documented run-if values, condition and for-each tasks,
  retries and timeouts, parameters and task values (reusing `dbutils_local.py`); injected failures;
  repair runs. Validator `job_graph`. Run records in the `runs` shape.
- **Depends on:** DBA-001, DBA-011. **Acceptance:** E34 to E43 live. **Owner:** Data Factory thread
  (orchestration) with this thread.

### DBA-008
**Unity Catalog governance and permissions training (P1, Phase 3).**
- **Scope:** `org` dataset and a permission model: securables, ownership, inheritance, USE CATALOG /
  USE SCHEMA, managed vs external storage and conversion, row filters and column masks with group
  membership, governed tags and ABAC policies. Validator `grants` evaluates an access matrix,
  including the paths that must be denied. Every rule cites the doc page it models.
- **Depends on:** DBA-001, DBA-011. **Acceptance:** E59 to E64 live. **Owner:** Enterprise governance
  thread with this thread. **Security read:** recommended (a wrong model teaches wrong security).

### DBA-009
**Databricks deployment and CI/CD exercises (P1, Phase 3).**
- **Scope:** an offline bundle resolver for exercises (variables, targets, overrides, modes) checked
  against `databricks bundle validate` output recorded from a real CLI run; reuse #1747 for real
  validate/deploy; local Git repo exercises on `git_service.py`; a CI workflow document checked by rules.
- **Depends on:** DBA-001, #1747. **Acceptance:** E44 to E50 live. **Owner:** Fabric workspace Git
  awareness thread with this thread.

### DBA-010
**Performance monitoring and optimization training (P2, Phase 3).**
- **Scope:** recorded datasets: `stages` (Spark stage metrics with skew, shuffle, spill), `runs`
  (run history with a regression), cluster events and driver logs; Delta maintenance on delta-rs
  (compaction, vacuum). Validator `plan_shows` reads a recorded plan. Recordings come from real
  Spark runs and say so.
- **Depends on:** DBA-001. **Acceptance:** E22, E27, E51 to E58 live. **Owner:** Optimize central
  lakehouse thread with this thread.

### DBA-011
**Databricks-specific automated grading (P0 for `idempotent`, Phase 1 onward).**
- **Scope:** extend `academy/validators.py`, never a second framework: `idempotent` (run twice,
  same table state, versions only where allowed), `job_graph`, `grants`, `bundle_resolves`,
  `plan_shows`, `platform_check`. Partial credit: each check carries a weight; the exercise result
  is the weighted share passed, and pass needs every `required` check. Messages explain the failed
  criterion without showing the answer.
- **Depends on:** #1777. **Acceptance:** each validator passes the reference and fails two planted
  wrong answers. **Owner:** grading sandbox thread. **Security read:** for anything reading learner output.

### DBA-012
**Certification assessment question bank (P1, Phase 4).**
- **Scope:** `packs/dbx-de-associate/questions/` in the exam engine's format (#1781): 206 original
  scenario questions, at least 5 per objective, cited docs fetched and supporting the answer;
  three 45-question, 90-minute forms weighted by section; confidence capture; remediation links to
  exercises. No exam dumps; never called official.
- **Depends on:** #1781, DBA-001. **Owner:** exam engine thread.

### DBA-013
**Databricks workspace verification and safe cleanup (P1, Phase 3).**
- **Scope:** pre-flight (sign-in, capabilities, compute access, permissions, resources needed,
  cost warning, confirm); every Academy-created object named `kf_academy_<run>` and tagged
  `kratosforge-academy=<pack>/<exercise>/<run>`; a local ledger of what each run created;
  cleanup removes only objects that are in the ledger **and** carry the tag; interrupted runs are
  found and offered for cleanup on next start; `platform_check` read-only checks.
- **Depends on:** DBA-002. **Acceptance:** cleanup refuses an untagged object or one missing from the
  ledger, with a test for each. **Owner:** this thread. **Security read:** required.

### DBA-014
**Cross-platform Fabric and Databricks competency mapping (P2, Phase 5).**
- **Scope:** `packs/shared/competencies.json`: portable competencies (Delta MERGE, dedupe, medallion
  cleaning, incremental watermark, data quality rules, windowed aggregates) each listing equivalent
  objectives in both packs. Evidence from one pack counts in the other only for portable,
  non-platform-required objectives, labelled "earned in the DP-700 path".
- **Depends on:** DBA-001, #1779. **Owner:** Academy lead thread.

### DBA-015
**End-to-end capstone projects (P2, Phase 4).**
- **Scope:** C1 to C3 ([curriculum](curriculum.md#capstones)) with hidden-seed validation and a
  portfolio artifact each. **Depends on:** DBA-003 to DBA-011. **Owner:** content thread.

### DBA-016
**Certification objective change monitoring (P2, Phase 4).**
- **Scope:** extend ACD-020's study-guide check with a Databricks source: the certification page's
  current guide link and the guide's objective text compared with the registry snapshot; a change
  opens a review task, never rewrites the registry. **Depends on:** ACD-020. **Owner:** Academy lead.
