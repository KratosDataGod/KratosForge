# Three-platform parity assessment (Phase 0)

Audit of KratosForge against Microsoft Fabric, Azure Databricks and Snowflake for the three-platform
parity program (Chris's master prompt, 2026-11-19).

- **Audited commit:** main `81e70e22`, 2026-11-19.
- **Method:** read the code registries that already describe the platforms (`parity.py`,
  `platforms.py`, `capability_registry.py`, `engines/registry.py`, `connprofiles.py`), the engine and
  service modules per platform, the tests they name, docs/parity.md, docs/platforms.md and the open pull
  requests. Nothing was run against a cloud platform. A status here is never stronger than the
  generated tables in [docs/parity.md](../parity.md) and [docs/platforms.md](../platforms.md), which
  tests keep equal to the code.
- **Backlog:** [platform-parity-backlog.md](platform-parity-backlog.md).

## A. Executive summary

**What works.** Fabric is the mature platform: sign-in, workspaces and lakehouses, and Spark SQL are
verified by two recorded live runs, and the platform manifest puts Fabric at level 4 built and level 2
verified. Local execution (DuckDB, Polars, delta-rs) is the strongest part of the product: Spark SQL,
PySpark, T-SQL, Databricks SQL and Snowflake SQL all run or are validated on this PC, each with a
generated list of what runs, differs, is refused or fails.

**What partially works.** Databricks is built to level 2 against fakes: sign-in and discovery, the SQL
Statement API, Databricks Connect, notebook sync, read-only job and Lakeflow monitoring, bundles
(validate, plan, deploy) and DBU reads. None of it has a recorded live run. Fabric pipelines run on this
PC with expected outcomes taken from documentation, not a tenant.

**What is missing.** Snowflake has no cloud integration at all: profiles and sign-in methods are
defined in `connprofiles.py`, but nothing signs in, runs a query, lists objects or reads credits.
Snowpark, stages, tasks, streams and dynamic tables are absent. Databricks has no Unity Catalog
browser, job authoring or compute view. Fabric Dataflows Gen2 and task flows are unassessed.

**What is unverified.** Every Databricks cloud cell, every Fabric cell except sign-in, workspace
listing and Spark SQL, and every runtime-equivalence cell except Fabric's 10-query Spark comparison
(9 match; decimal division differs and is reported).

**Major risks.**
1. A matrix that reads as more done than it is. Mitigated: `Cell` refuses claims without tests, and
   PAR-001 makes each area's mode match its cells.
2. Simulated execution mistaken for the platform. Every local Snowflake and Databricks note says it is
   not the platform's runtime; PAR-005 carries this into the app.
3. Cloud cost from development. Remote engines open only with a `RemoteAuthorization`, and live runs
   need Chris's typed go.
4. Two agents changing the same Databricks modules. The ownership split below addresses it.

**Recommended next investments.** In order: the verification harness (PAR-002), Snowflake sign-in,
connection and read-only SQL (SF-001..003) as a new remote engine, the shared connection-check result
(PAR-003), then the gated live runs (DBX-001, FAB-001) that turn Built into Verified.

## B. Parity matrix

The matrix is [docs/parity.md](../parity.md), generated from `sidecar/fabric_desktop/parity.py`. It
judges five levels separately (visual, workflow, cloud API, local execution, verified equivalence) with
the statuses Not assessed, Missing, Partial, Built and Verified. This program extends it rather than
adding a registry: each area now also carries a `Plan` (family A..I, priority, where its work runs,
backlog items, owner, review date), shown in the generated "What's next, and who owns it" table.

Summary at `81e70e22` (cloud API column):

| Platform | Verified | Built | Partial | Missing | Not assessed |
| --- | -: | -: | -: | -: | -: |
| Fabric (12 areas) | 3 | 4 | 0 | 0 | 5 |
| Databricks (8 areas) | 0 | 3 | 3 | 0 | 2 |
| Snowflake (7 areas) | 0 | 0 | 0 | 2 | 5 |

The program's "Not applicable" status is not added yet: no area so far lacks a platform equivalent.

## Implementation inventory by family

| Family | Fabric | Databricks | Snowflake |
| --- | --- | --- | --- |
| A Auth and connectivity | MSAL and WAM with the user's identity, no app registration; verified live | CLI and API discovery, Credential Manager; fakes | Profile schema and four sign-in methods defined; no client |
| B SQL development | SQL endpoint and warehouse queries; T-SQL on this PC with loud gaps | SQL Statement API with cancel; Databricks SQL on this PC | Snowflake SQL validated on this PC only |
| C Notebooks and compute | Livy Spark verified; PySpark simulated on DuckDB | Databricks Connect (16+), dbutils and magics on this PC, workspace sync | Snowpark missing |
| D Storage and catalog | OneLake and lakehouse read and write from this PC | Unity Catalog names resolved on this PC; no browser | Not assessed |
| E Orchestration | Pipelines run on this PC; dataflows not assessed | Read-only job and Lakeflow runs | Not assessed |
| F Source control and deploy | Fabric Git and publish; not recorded live | Bundles validate, plan, deploy; fakes | Not assessed |
| G Monitoring and cost | Capacity and Optimize | DBUs from billing tables after a confirm | Not assessed |
| H Semantic | TMDL offline, Direct Lake check; OSSIE program | Not assessed | Not assessed |
| I Shared services | Central platform (keys, products, exceptions); control plane in progress | Not assessed | Not assessed |

## C. Prioritized backlog

[platform-parity-backlog.md](platform-parity-backlog.md): 27 items, scored with the section 12 weights,
with acceptance criteria for the P0 items and recorded priority overrides.

## D. Architecture recommendations

1. **One registry, three views.** `parity.py` (experience parity per area), `platforms.py` (support
   level per platform) and `capability_registry.py` (where each capability runs per engine) already
   split the question cleanly and each refuses claims without evidence. Keep them; link them by area and
   capability ids instead of merging them.
2. **Remote engines follow the Databricks SQL pattern.** An `EngineDescriptor` with `location="remote"`,
   a token provider, `RemoteAuthorization` before anything opens, no fallback between local and remote,
   and the `result` capability for comparisons. Snowflake gets `engines/snowflake_remote.py` and
   `engines/snowflake_auth.py`; the local translator keeps its module.
3. **Connection checks share a result type, not an abstraction.** PAR-003 shares only identity, scope
   and problems; roles, warehouses, hosts and tenants stay native.
4. **Verification records are evidence, not metadata.** PAR-002 writes run records with their evidence
   level (local, fake, live). Only live records can raise a cell to Verified. The control plane can store
   them later through its providers; this program adds no store.
5. **Skins stay cosmetic.** Nothing in this program reads the skin to decide execution.

## E. Verification plan

| Layer | What runs | Counts as |
| --- | --- | --- |
| Unit and contract | Every engine against fakes of the platform's documented API | Built |
| Local equivalence | Each PAR-002 case on DuckDB 1.5.6 and DuckDB 2 | Local evidence only |
| Live, opt-in | The same cases on Fabric (`kf_tenant_test`), Databricks or Snowflake after Chris types "yes, run it" | Verified, once recorded in docs/platforms.md |

Comparisons use `verify.py` rules: explicit keys, tolerances and timestamp precision. A normalization is
allowed only when the case names it. A mocked API pass is never recorded as cloud equivalence.

## F. Roadmap

| Step | Increment | Depends on | Gate |
| --- | --- | --- | --- |
| 1 | PAR-001 plan metadata and this assessment | none | Quality review |
| 2 | SF-001 + SF-002 Snowflake sign-in and connection check (engine module, fakes) | 1 | Quality, Security |
| 3 | SF-003 read-only Snowflake SQL with cancel and paging (fakes, Preview) | 2 | Quality, Security |
| 4 | PAR-002 case format, local runner and run record | 1 | Quality |
| 5 | PAR-003 shared connection-check result across the three platforms | 2 | Quality, Security |
| 6 | SF-004, SF-005 objects and query history (engine side) | 3 | Quality |
| 7 | PAR-004 parity view and Snowflake/Unity Catalog browsers | 6 | UX mockup approved by Chris |
| 8 | Live: FAB-001, DBX-001, first Snowflake run | 3, 4 | Chris types "yes, run it" |

At most two of these are open as pull requests at a time.

## G. Agent coordination

| Workstream | Owns in this program |
| --- | --- |
| Platform parity (this thread) | PAR-001..005, SF-001..006, DBX-002, DBX-003, DBX-005 |
| Databricks and Snowflake parity | Local runtime parity for both platforms, existing Databricks cloud modules, DBX-001 |
| Central control plane | Shared environment, metadata stores; parity records go there only through its providers |
| Semantic engineering (OSSIE) | FAB-004 and all semantic work |
| Warehouse | FAB-003 |
| Optimization engine, Enterprise savings intelligence | FAB-006, DBX-006 (with SF-006 numbers as input) |
| Governance, BPA, Fabric excellence assessment | FAB-005 |
| Developer experience audit | Reads this matrix; no overlap |
| UX | Mockups for PAR-004, SF-004 and DBX-002/003 screens |
| Unassigned | FAB-002, FAB-007, SF-007, SF-008, SF-009, DBX-004 |

Claims follow ai-commons `standards/work-coordination.md`; this program's claim is in
`projects/kratosforge/status.md` on ai-commons main.
