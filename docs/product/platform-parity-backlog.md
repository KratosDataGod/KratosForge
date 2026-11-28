# Three-platform parity backlog

Prioritized backlog for the three-platform parity program (Fabric, Azure Databricks, Snowflake).
Audit baseline: main `81e70e22`, 2026-11-19. The assessment, ownership map and roadmap are in
[platform-parity-assessment.md](platform-parity-assessment.md). Each area's next items and owner are
in the generated table at the end of [docs/parity.md](../parity.md); a test checks that every item that
table names has a heading here.

These ids are new for this program. They are not the earlier `SF-00`..`SF-06` ids of the Snowflake
foundation PRs (#993, #995) or the warehouse thread.

## Scoring

Each item is scored 1 to 5 on seven criteria (Chris's master prompt, section 12):

| Criterion | Weight |
| --- | ---: |
| D Developer productivity | 25% |
| C Cross-platform value | 20% |
| E Enterprise adoption | 15% |
| L Local-first or cost value | 15% |
| F Feasibility | 10% |
| R Architectural reuse | 10% |
| X Differentiation | 5% |

Priority follows the score unless an override is recorded. Overrides: verification and connectivity
items are P0 whatever their score (prompt section 12: correctness, connectivity and verification are
foundational), and any item that needs a live platform stays gated on Chris typing "yes, run it".

| Item | D | C | E | L | F | R | X | Score | Priority | Effort | Owner |
| --- | -: | -: | -: | -: | -: | -: | -: | -: | --- | --- | --- |
| PAR-002 Verification harness | 4 | 5 | 5 | 4 | 4 | 5 | 5 | 4.50 | P0 | L | Platform parity |
| SF-003 Snowflake cloud SQL | 5 | 4 | 4 | 4 | 4 | 5 | 4 | 4.35 | P0 | M | Platform parity |
| PAR-001 Registry plan metadata | 4 | 5 | 4 | 3 | 5 | 5 | 3 | 4.20 | P0 | S | Platform parity |
| SF-001 Snowflake sign-in | 5 | 4 | 5 | 3 | 4 | 4 | 3 | 4.20 | P0 | M | Platform parity |
| SF-006 Snowflake credits | 3 | 4 | 5 | 5 | 3 | 4 | 4 | 3.95 | P1 | M | Platform parity |
| DBX-006 DBU cost intelligence | 3 | 4 | 5 | 5 | 3 | 4 | 4 | 3.95 | P1 | M | Optimization engine |
| PAR-003 Connection contract | 4 | 5 | 4 | 2 | 4 | 4 | 3 | 3.85 | P0 | S | Platform parity |
| SF-002 Snowflake connection | 5 | 3 | 4 | 3 | 4 | 4 | 3 | 3.85 | P0 | S | Platform parity |
| DBX-001 Databricks live check | 3 | 4 | 5 | 3 | 4 | 5 | 3 | 3.80 | P0 (override) | M | Databricks and Snowflake parity |
| FAB-006 Capacity optimization | 3 | 3 | 5 | 5 | 3 | 4 | 4 | 3.75 | P1 | M | Optimization engine |
| DBX-002 Unity Catalog browser | 4 | 3 | 4 | 3 | 4 | 5 | 2 | 3.65 | P1 | M | Platform parity |
| FAB-001 Fabric live tests | 3 | 3 | 5 | 3 | 4 | 5 | 3 | 3.60 | P0 (override) | M | Platform parity |
| SF-004 Snowflake objects | 4 | 3 | 4 | 3 | 4 | 4 | 2 | 3.55 | P1 | M | Platform parity |
| SF-005 Snowflake query history | 3 | 3 | 4 | 4 | 4 | 4 | 3 | 3.50 | P1 | S | Platform parity |
| FAB-002 Data Factory development | 4 | 2 | 4 | 4 | 3 | 4 | 4 | 3.50 | P1 | L | unassigned |
| FAB-003 Warehouse development | 4 | 2 | 4 | 4 | 3 | 4 | 3 | 3.45 | P1 | L | Warehouse |
| PAR-004 Parity view (UI) | 3 | 4 | 3 | 2 | 4 | 5 | 4 | 3.40 | P1 | M | Platform parity + UX |
| PAR-005 Availability from the registry | 3 | 4 | 4 | 2 | 3 | 4 | 3 | 3.30 | P1 | M | Platform parity |
| FAB-004 Semantic engineering | 3 | 3 | 4 | 3 | 3 | 4 | 4 | 3.30 | P2 | (OSSIE) | Semantic engineering |
| FAB-007 Deployment readiness | 4 | 2 | 5 | 2 | 3 | 4 | 3 | 3.30 | P1 | L | unassigned |
| DBX-003 Job authoring | 4 | 2 | 4 | 3 | 3 | 4 | 3 | 3.30 | P1 | L | Platform parity |
| SF-007 Snowpark assessment | 3 | 3 | 3 | 4 | 2 | 3 | 4 | 3.10 | P2 | M | unassigned |
| DBX-005 Compute | 3 | 2 | 3 | 4 | 4 | 4 | 2 | 3.10 | P2 | S | Platform parity |
| FAB-005 Governance assessments | 3 | 2 | 5 | 2 | 3 | 4 | 3 | 3.05 | P2 | M | Governance |
| SF-008 Tasks, streams, dynamic tables | 3 | 2 | 4 | 3 | 2 | 3 | 3 | 2.85 | P2 | L | unassigned |
| DBX-004 Lakeflow development | 3 | 2 | 4 | 3 | 2 | 3 | 3 | 2.85 | P2 | L | unassigned |
| SF-009 Stages and loading | 3 | 2 | 3 | 3 | 3 | 3 | 2 | 2.75 | P2 | M | unassigned |

Effort: S under a week of agent work, M one to two weeks, L more.

## Cross-platform

### PAR-001 Registry plan metadata (P0, this program's first PR) **Built** (#3112)

- **Functional:** each parity area says its feature family, priority, where its work runs today,
  the backlog items that move it, its owner and when it was reviewed. docs/parity.md shows it.
- **Design:** `Plan` and `PLANS` in `sidecar/fabric_desktop/parity.py`, next to `AREAS`. No new
  registry; `matrix()` carries the plan. A Fabric "Sign-in and connections" area is added (its sign-in
  was recorded in the KUDP runs).
- **Acceptance:**
  - Given an area, when `PLANS` lacks it, then a test fails.
  - Given a plan whose mode says cloud or local, when the area's cells don't back that, then a test fails.
  - Given a backlog item named in a plan, when this file has no heading for it, then a test fails.
  - Given `PLANS`, when docs/parity.md's plan table differs, then a test fails.
- **Tests:** `sidecar/tests/test_parity_plan.py`.

### PAR-002 Cross-platform verification harness (P0)

- **Functional:** a developer or CI runs a named suite of deterministic cases on this PC, and, only
  with an explicit opt-in per platform, on Fabric, Databricks or Snowflake, and gets a recorded,
  comparable result per case.
- **Design:** reuse `engines/compare.py` (engine-to-engine comparison with confirm), `verify.py`
  (explicit rules and levels), the KUDP comparison and `dbx_livetest.py`. New: a case file format
  (dataset, statement per dialect, expected schema and values, allowed normalizations by name) and a
  run record (platform, runtime and connector versions, case id, date, location, expected, actual,
  outcome, evidence path). Records never land in shared analytical storage with credentials.
- **Acceptance:**
  - Given a case with no allowed normalization, when decimal scale differs, then the outcome is
    "differs" with both values, never "match".
  - Given a run against a fake API, when the record is written, then its evidence level is "fake" and
    the parity matrix can't read it as verified.
  - Given no opt-in, when a suite runs, then no network call leaves the PC.
- **Tests:** local runs of every case on DuckDB 1.5.6 and DuckDB 2; contract tests per remote engine
  against fakes. Live runs only after Chris types "yes, run it" (Fabric: `kf_tenant_test`).
- **First increment:** case format plus local runner plus record, no cloud.

### PAR-003 Unified connection contract (P0)

- **Functional:** every platform answers "test this connection" the same way: who I am, which role or
  workspace, what I may do, and each problem with the fix.
- **Design:** a small result type shared by the Fabric, Databricks and Snowflake connection checks;
  `connprofiles.py` stays the profile store. Platform-specific fields stay native (Snowflake role and
  warehouse, Databricks host, Fabric tenant).
- **Acceptance:** Given an expired or refused credential on any platform, when the check runs, then the
  result names the platform's own reason and a next step, and no secret appears in it.

### PAR-004 Platform parity view (P1, needs a mockup Chris approves)

- **Functional:** a page that shows each platform's coverage from `parity.matrix()`, the evidence behind
  each cell, and next actions (configure connection, run compatibility test, open native platform).
- **Design:** reads the matrix only; no claims of its own. Skins never change it.
- **Gate:** UX mockup first; no code before Chris approves it.

### PAR-005 Availability from the registry (P1)

- **Functional:** actions that need a cloud engine say so for the current connection and runtime, and
  are never silently replaced by a local simulation.
- **Design:** reuse `capability_registry.py` and the engine descriptors; add only the connection input.

## Snowflake

The local translator (`fabric_desktop/snowflake_sql.py`) stays with the Databricks and Snowflake parity
workstream. The cloud engine is a new module, `engines/snowflake_remote.py`, Preview, tested against
fakes until Chris types "yes, run it" for a live account.

### SF-001 Snowflake sign-in (P0)

- **Functional:** sign in with the methods `connprofiles.py` already accepts: key pair, programmatic
  access token and a Snowflake CLI connection first; browser sign-in through Snowflake's local OAuth
  application after.
- **Design:** a token provider like `engines/databricks_auth.py`. Key pair: a short-lived JWT signed
  locally, the key file named by full path, passphrase from a secret reference. PAT: from `credman:` or
  `env:` references only. Nothing in plaintext, nothing in logs (`redact.py`).
- **Acceptance:**
  - Given a key-pair profile, when a token is made, then it expires within an hour and carries the
    account and user in Snowflake's documented form.
  - Given a refused token, when the API answers 401, then the error says which method and what to check,
    and never repeats the secret.
- **Security review:** yes (tokens, file reads).

### SF-002 Snowflake connection management (P0)

- **Functional:** test a profile: account, user, role, warehouse, database and schema, each checked, and
  a permission-aware error for each that fails.
- **Acceptance:** Given a role the user doesn't hold, when the check runs, then the result names the role
  and says the user lacks it, without trying another role.

### SF-003 Snowflake cloud SQL, read-only first (P0)

- **Functional:** run a SQL cell on a Snowflake warehouse through the SQL API, with cancel, timeout,
  result paging and actionable errors. Read-only statements first; anything that changes state needs an
  explicit confirm and stays off until its own increment.
- **Design:** mirror `engines/databricks_sql.py`: `RemoteAuthorization` before anything opens (a running
  warehouse bills credits), never a fallback to or from the local translator, the `result` capability
  for PAR-002 comparisons.
- **Acceptance:**
  - Given no authorization for the engine and warehouse, when a cell runs, then nothing is sent.
  - Given an INSERT, when read-only is on, then it is refused before any request.
  - Given a cancel, when the statement runs, then the SQL API's cancel is called and the cell ends as
    cancelled.

### SF-004 Snowflake object discovery (P1)

Browse databases, schemas, tables and views the current role may see, through the SQL API's `SHOW` and
`INFORMATION_SCHEMA`. Read-only. UI placement needs a mockup.

### SF-005 Snowflake query monitoring (P1)

Status, history and run time of the user's own queries (`INFORMATION_SCHEMA.QUERY_HISTORY*`), with the
query profile link. Read-only.

### SF-006 Snowflake credit visibility (P1)

Warehouse metering from `ACCOUNT_USAGE` when the role may read it, with its documented latency shown.
Credits only, never converted to currency without a measured rate, never called savings (ADR-006).

### SF-007 Snowpark assessment (P2)

Research first: which Snowpark Python calls can be checked on this PC, labeled as compatibility, never
as Snowflake runtime equivalence. Needs Chris's go before any runtime.

The research is in [snowpark-assessment.md](snowpark-assessment.md). Chris approved option C first, then
option A. Option C, the Snowpark API compatibility check, is built (Preview,
[engines.md](../engines.md#snowpark-api-compatibility-check-preview)), and so is option A, the
`snowpark-local` engine on Snowflake's local testing framework ([engines.md](../engines.md#snowpark-on-this-pc-preview)).
Still open: whether `session.sql` goes through the Snowflake SQL translator, and Security's review of
the runtime's packages.

### SF-008 Tasks, streams and dynamic tables (P2)

After SF-001..005. Author and validate locally; deploy only through Snowflake's own statements.

### SF-009 Stages and data loading (P2)

Stages, file formats and `COPY INTO`, after SF-004.

## Databricks

The Databricks and Snowflake parity workstream keeps the local runtime (SQL translation, dbutils,
magics, Unity Catalog names on this PC) and DBX-001. New functions next to `databricks_discovery.py` and
`databricks_sql.py` are fine; changes to their existing functions are agreed with that workstream first.

### DBX-001 Live integration verification (P0 override, gated)

Run the existing `dbx_livetest.py` against an authorized workspace once Chris types "yes, run it", and
record it in docs/platforms.md. Owner: Databricks and Snowflake parity.

### DBX-002 Unity Catalog browser (P1, needs a mockup)

Catalogs, schemas, tables, views and volumes the user may see, from the Unity Catalog API the SQL engine
already calls. The local view stays consistent with the session catalog `main` and schema `default`.

### DBX-003 Job authoring and runs (P1, needs a mockup)

Create, validate and start jobs through the Jobs API, each start behind a confirm, beyond today's
read-only Monitor.

### DBX-004 Lakeflow development (P2)

Develop, validate, deploy and monitor Lakeflow declarative pipelines, building on bundles.

### DBX-005 Compute (P2)

Show clusters and warehouses with state, runtime version and its compatibility with Databricks Connect.
Read-only first; never start compute without a confirm.

### DBX-006 DBU cost intelligence (P1)

Trends and workload attribution on top of today's billing-table read. DBUs are not the whole cost; the
page says so. Owner: Optimization engine, with Enterprise savings intelligence.

## Fabric

### FAB-001 Live compatibility tests (P0 override, gated)

Extend the recorded runs (KUDP, `live-test`, tenant smoke test) to SQL endpoint, notebook runs, Delta
writes, pipelines and publish, in `kf_tenant_test` only, after "yes, run it". Feeds PAR-002.

### FAB-002 Data Factory development (P1)

Authoring, validation, local simulation and debugging of pipelines; deploy through supported APIs.
Needs an owner.

### FAB-003 Warehouse development (P1)

T-SQL compatibility and warehouse tools. Owner: Warehouse workstream.

### FAB-004 Semantic engineering (P2 here)

Owned by the semantic engineering program (OSSIE-001..015). This program only reflects its results in
the matrix.

### FAB-005 Governance and assessments (P2)

Security, best-practice, performance and configuration assessments. Owners: Governance, BPA and the
Fabric excellence assessment.

### FAB-006 Capacity optimization (P1)

Measured consumption beside local runs, from existing Optimize and capacity code. Owners: Optimization
engine and Enterprise savings intelligence.

### FAB-007 Deployment readiness (P1)

Git and deployment workflows with validation, approvals and rollback, building on `fabric_git.py` and
the Prod name-typing rule. Needs an owner.
