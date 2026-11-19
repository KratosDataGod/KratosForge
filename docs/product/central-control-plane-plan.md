# Central control plane: Phase 0 audit, gap analysis and target architecture

Status: Phase 0 (discovery and architecture). Owner: Central control plane thread (claude-opus).
Companion files: [ADRs](central-control-plane-decisions.md) and [stories](central-control-plane-backlog.md).

This plan answers one question: what does KratosForge need so that a person can create or join one
**KratosForge Environment** in Fabric, from any PC, and every feature that needs shared persistence
uses it, without inventing a second store. The short answer, from the audit below, is that most of
the storage already exists in two separate halves. The work is to give those halves one identity
(a manifest that lives in the environment), one setup and join flow, and one provider contract,
not to build a new storage system.

Nothing in this plan has run against a live tenant. Every Fabric statement below is either cited to
code in this repository or marked as unverified.

## 1. Existing-state audit

Verified by reading the code at `main` 82f8e21 (2026-11-19). Paths are under
`sidecar/fabric_desktop/` unless they start with `docs/`.

### 1.1 Two central stores already exist

| Half | What it is | Code | Docs | Live-verified |
| --- | --- | --- | --- | --- |
| **Platform** (operational) | Per deployment environment (Dev, Test, Prod), one *Platform workspace* with `SQL_Platform` (Fabric SQL database), `UDF_Platform` (User Data Functions: the only API to the database), `LH_Meta` (run history, the pipeline mailbox), `LH_Landing` (landing zone) and `VL_Platform` (variable library of item ids). Optional shared *Keys workspace* (`SQL_Keys`, `UDF_Keys`) for one key system across environments. | `central/setup.py`, `central/core.py` (stdlib-only, runs in the UDF and against SQLite), `central/udf.py`, `central/client.py`, `central/service.py`, `central/keys_home.py`, `central/migrate.py` | `docs/platform.md`, `docs/platform-audit.md`, `docs/data-mesh.md` | No (Preview; `docs/platform.md` "What isn't proven yet") |
| **Central lakehouse** (analytical) | One lakehouse per organization, default `FabricDesktop_Central`, holding table *families*: `fdopt_` Optimize and savings, `fdacc_` access cache, `fdten_` tenant settings history, `fdcap_` capacity settings history, `fdinc_` incident history. Insert-only, `record_id` MERGE, provenance on every row, additive migrations per family, newer schema opens read-only. | `optimize/schema.py`, `optimize/setup.py`, `optimize/lakehouse.py`, `optimize/shared.py`, `optimize/outbox.py`, `optimize/installation.py`, `optimize/registry.py`, `optimize/roles.py`, `optimize_service.py`, `access_cache.py`, `tenant_history.py`, `capacity_history.py`, `workloads/incident_history.py` | `docs/optimize/` | No |

Properties the audit confirmed in code, which the control plane must keep:

- **Server-side authorization for operational metadata.** `UDF_Platform` takes the caller from
  Fabric's `executing_user` (`central/udf.py:54`, `:69`), not from the desktop. Platform admins,
  domain owners, product owners and project members are checked in `core.py`. This is the basis for
  acceptance scenario 8.
- **Idempotent operational writes.** `core.call_once` (`central/core.py:3362`) with `catalog.call`
  rows kept 7 days, so a retried change is applied once.
- **Operational schema versioning.** `core.SCHEMA_VERSION = 5` with `ensure_*` additive steps;
  `config.setting` holds the version.
- **Idempotent analytical writes.** Every central lakehouse row has `record_id` and is written by an
  insert-only MERGE (`optimize/schema.py` docstring); a retry adds nothing.
- **Per-family analytical versioning.** `optimize/schema.py` `Family`, `Migration`, `register`;
  `fdopt_` is at version 2 (ESI-101). `setup.inspect` reports `ready`, `empty`, `unrelated`,
  `incomplete`, `upgrade`, `newer`, `conflict`; `newer` is read-only; `initialize(approve=False)`
  refuses.
- **Durable local queue.** `optimize/outbox.py`: SQLite, bounded (200 items, 30 days), backoff,
  sync states `synced`, `pending`, `synchronizing`, `offline`, `failed`, `conflict` (ESI-105).
- **Installation identity.** `optimize/installation.py`: random UUID, not derived from person, PC
  or tenant, separate from the opt-in telemetry id.
- **Local fallback.** Without Fabric, the Platform runs on SQLite at
  `%LOCALAPPDATA%\FabricDesktop\platform\platform-<env>.sqlite`; the central lakehouse has a
  folder-on-this-PC store used by tests (`optimize/lakehouse.py`).
- **Honest platform support levels.** `platforms.py` and `engines/capabilities.py` refuse claims
  without evidence; `docs/platforms.md` is generated from code.

### 1.2 How each half is found today

| Half | Connect from a second PC | Identity of the resource |
| --- | --- | --- |
| Platform | **Join existing** per environment, choose the workspace; items are found **by display name** (`docs/platform.md` "Join it from other PCs"). Or IT pushes ids in `policy.json` `platform`. | Item ids after the join. |
| Central lakehouse | `optimize_discover(workspace_id)` lists a workspace's lakehouses and marks one holding `fdopt_schema_version`; `optimize_connect` registers it in `<data>/optimize/connections.json`. | Workspace and lakehouse ids. |

Neither half knows the other exists. A PC can be joined to a Platform and to a central lakehouse
that belong to two different organizations, and nothing records that they belong together.

### 1.3 Provider abstraction today

- **Execution** has a real abstraction: `engines/contract.py` `EngineAdapter`, `engines/registry.py`,
  `engines/capabilities.py` with levels and evidence. Databricks has SQL warehouse, Connect, jobs,
  bundles and billing adapters; Snowflake has functions coverage and connection profiles only.
- **Central storage has no provider abstraction.** `central/client.py` has `LocalPlatform` and
  `FabricPlatform` with one shape (`client.call(op, **kw)`), and `optimize/lakehouse.py` has a local
  folder and a OneLake store behind `_Tables`. Both are Fabric-or-local, not provider-neutral.
- Per `docs/platforms.md`: Fabric built level 4, verified level 2; Databricks built level 2,
  verified 0; Snowflake level 0. None of them has central storage.

## 2. Persistence and storage inventory

Every store found that holds data, metadata, configuration, telemetry or operational state.
"Stays" means where it belongs in the target architecture (section 4).

| # | Store | Location | Owner (thread) | Model | Stays | Authoritative copy |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Operational catalog, keys, policies, requests, admins, audit | `SQL_Platform` via `UDF_Platform`; SQLite fallback `platform/platform-<env>.sqlite`, `platform/keys.sqlite` | Platform / mesh, Governance (policies), TMDL/BPA read | Relational, `core.py` schema v5 | Central, operational | `SQL_Platform` (or SQLite before Fabric) |
| 2 | Run history, pipeline mailbox, leases | `LH_Meta` per Platform workspace (`mailbox.py`, `leases.py`, `meta/store.py`) | Metadata framework, Workload mgmt | Delta tables and JSON files | Central, analytical (runs) and coordination (mailbox) | `LH_Meta` |
| 3 | Landing zone files | `LH_Landing` (and landing workspace) | Platform | Files | Central data, not metadata | `LH_Landing` |
| 4 | Optimize, savings ledger, measurements, CU usage, local execution, baselines, attribution, sync overhead | `FabricDesktop_Central` `fdopt_*` | Lakehouse optimize, ESI, Optimization engine | Delta, insert-only, v2 | Central, analytical | Central lakehouse |
| 5 | Access cache | `fdacc_*` | Governance / OneLake access | Delta | Central, analytical (cache) | Fabric itself; table is a cache |
| 6 | Tenant settings history | `fdten_*` | Enterprise readiness | Delta | Central, analytical | Central lakehouse |
| 7 | Capacity settings history | `fdcap_*` | FinOps | Delta | Central, analytical | Central lakehouse |
| 8 | Incident history | `fdinc_*` (live list in `LH_Meta`) | Workload mgmt | Delta | Central, analytical | Central lakehouse |
| 9 | Optimize outbox, connections, installation id | `<data>/optimize/outbox.sqlite`, `connections.json`, `installation.json` | Lakehouse optimize | SQLite, JSON | Local | This PC |
| 10 | Platform and app settings, IT policy | `app_settings.py`; `policy.json`; per-user settings | Settings, Governance | JSON | Local (policy pushed by IT) | `policy.json` wins over the user's |
| 11 | Workload management records | `<data>/workloads.sqlite` (`workloads/store.py`) | Workload mgmt | SQLite | Local; history projects to `fdinc_` | This PC |
| 12 | Integrations, approvals | `<data>/integrations/integrations.sqlite` | MCP server / AI integration | SQLite | Local (security boundary) | This PC |
| 13 | Agent job queue, alerts | `queue.sqlite3`, `alerts.sqlite3` (`jobqueue.py`) | Headless agent | SQLite | Local to the agent | Agent's disk |
| 14 | Local schedules and their runs | `schedules.json`, `schedule_runs.jsonl` | Local engine | JSON | Local | This PC |
| 15 | Run receipts | `<data>/receipts/*.json` | Prove | JSON | Local; reference centrally later | This PC |
| 16 | Notebook and app history, favorites, workspaces list | `history.jsonl`, `favorites.json`, `workspaces.json` (`service.py`) | App shell | JSON | Local | This PC |
| 17 | Local data projects | `projects.json`, `<project>/project.json`, transform runs | Local engine | JSON, Delta on disk | Local | This PC |
| 18 | Developer profile, ranks, achievements | `profile/<account>/profile.json` (`progress.py`) | Ranks, Achievements, Mastery | JSON | Local; optional central projection later | This PC |
| 19 | Academy and Learn progress | `academy/`, `learn/` (local files; team packs in policy package) | Academy | JSON, SQLite sandboxes | Local; optional projection later | This PC |
| 20 | BPA results | `<data>/bpa/*.json`, results in `LH_Meta` | BPA | JSON | Local plus `LH_Meta` | Mixed (see gap G7) |
| 21 | Governance checks state | `governance_checks.json`, policy package files | Governance | JSON | Local; policy itself in item 1 | Item 1 |
| 22 | Usage data (opt-in, off) | `usage.json` (`telemetry.py`) | Telemetry | JSON | Local unless opted in | This PC |
| 23 | Audit trail (app) | Local trail, end anchored in Windows Event Log (#3044) | Codebase audit / Security | Hash chain | Local | This PC |
| 24 | Editor and Git settings | `editor/settings.json`, `git_repos.json` | Editor parity, VS Code | JSON | Local | This PC |
| 25 | Fabric Excellence Assessment snapshots | Not persisted centrally yet; FEA-501 plans `optimize/shared.py` | FEA | Pure engine today | Central, analytical (planned) | Central lakehouse (planned) |
| 26 | Backups | zip with `manifest.json` (`backup.py`) | App shell | Zip | Local | Covers items 9 to 24 |

Not found: any per-feature central store outside rows 1 to 8. That is good news: the "do not let
features invent their own central storage" rule is mostly already true, enforced by
`optimize/schema.register` for analytical data and by `core.py` for operational data.

## 3. Gap analysis

| Id | Gap | Evidence | Consequence |
| --- | --- | --- | --- |
| G1 | No environment identity. Platform and central lakehouse are configured separately (`settings["platform"][env]`, `optimize/connections.json`). | 1.2 | A second PC can join mismatched halves; nothing says which installations share an environment. |
| G2 | No manifest discoverable in the environment. Platform join finds items by display name. | `docs/platform.md` | Renamed items break the join; a fresh install can't be sure what it found is KratosForge's. |
| G3 | No single create or connect flow. Platform: Platform > Settings per deployment environment. Lakehouse: Optimize settings. | `central/service.py`, `optimize_service.py` | Two setups to learn; a "connect" from a second PC can still create a second lakehouse if the person picks Create. |
| G4 | No provider-neutral storage contract. | 1.3 | Databricks or Snowflake central storage would mean rewriting `central/client.py` and `optimize/lakehouse.py` callers. |
| G5 | No environment-wide compatibility check. Each half checks its own version (core v5, each family). | `core.SCHEMA_VERSION`, `setup.inspect` | The app can't say "this environment needs an upgrade" in one place. |
| G6 | No cost view of the environment itself. `fdopt_sync` records sync seconds, rows and commits (PR-OPT-009, ESI-202), but SQL database, UDF and storage cost are not shown, and are not separated from savings in one view. | `optimize_service._record_overhead` | Net savings could be overstated (ESI already refuses to fabricate; this closes the remaining hole). |
| G7 | No service inventory: which feature uses which central tables or services. Families know their prefixes; `core.py` lists its tables in a docstring only. | `optimize/schema.FAMILIES`, `core.py` docstring | Can't show orphaned data, unused services or duplicates (prompt section 14). |
| G8 | Both halves are unverified on a live tenant. | `docs/platform.md`, `docs/platforms.md` | Nothing here may be called production-ready until live runs pass. |
| G9 | Overloaded word "environment": Dev/Test/Prod (`meta/project.ENVIRONMENTS`), Fabric Environment items (`fabric_environment.py`), notebook environments (`environments.py`). | grep | UI and code confusion. See ADR CP-ADR-002. |
| G10 | Developer history, Academy, ranks are local only, by design (private profile, `progress.py`). | row 18, 19 | Prompt asks for central Academy progress "where appropriate": needs an opt-in decision, not a default. |

## 4. Target architecture

**One logical environment, two stores, existing code behind provider contracts.**

```
KratosForge Environment  (manifest: Files/_kratosforge/environment.json in the analytical store)
 ├─ Analytical store      Fabric: the central lakehouse   (optimize/schema families; fdopt_, fdacc_, ...)
 ├─ Operational store     Fabric: SQL database + UDF API  (central/core.py schema; optional)
 ├─ Deployment bindings   dev / test / prod -> Platform workspace, LH_Meta, LH_Landing, Keys workspace
 └─ Service registry      which feature uses which family or core schema (code, not data)
```

- **Manifest** (section 6) is the environment's identity. It lives inside the analytical store, so
  a fresh install that can read the lakehouse can reconnect without any file from another PC. It
  holds ids, versions and enabled services, never secrets.
- **Analytical store** is the existing central lakehouse and its families. No new tables are needed
  for Phase 1 except the manifest file and an environment `config` row (CP-ADR-004).
- **Operational store** is the existing Platform database (`central/core.py`) reached through
  `UDF_Platform`. It is optional: an environment can start lakehouse-only (Optimize, history,
  assessments) and add the operational store when a feature that needs transactions is turned on
  (keys, catalog, requests, policies). This follows the prompt's step "determine whether an
  operational database is required" and avoids a paid SQL database nobody uses.
- **Deployment bindings** keep today's Dev/Test/Prod Platform workspaces exactly as they are. The
  environment references them; it doesn't replace them.
- **Provider contracts** (section 7) wrap `optimize/lakehouse.py`, `optimize/setup.py`,
  `central/client.py` and `central/setup.py`. Features keep calling the same services; only the
  environment manager and new code go through the contracts.

Default names for a **new** environment follow the brief: workspace `KratosForge`, lakehouse
`KratosForgeLakehouse`, operational database `KratosForgeMetadata`. An existing environment keeps
its names (`FabricDesktop_Central`, `SQL_Platform`); the manifest identifies resources by id, so
names are labels only (CP-ADR-003).

### 4.1 Fabric physical mapping

| Logical | Fabric item | New environment default | Adopted from an existing install |
| --- | --- | --- | --- |
| Environment namespace | Workspace | `KratosForge` | The workspace holding the central lakehouse |
| Analytical store | Lakehouse (schemas on) | `KratosForgeLakehouse` | `FabricDesktop_Central` or whatever Optimize connected |
| Manifest | File `Files/_kratosforge/environment.json` | written at create | written at adopt (after preview and confirm) |
| Operational store | SQL database + User Data Functions | `KratosForgeMetadata` + `UDF_KratosForge` (only when enabled) | Prod `SQL_Platform` + `UDF_Platform` |
| Deployment bindings | Platform workspaces, Keys workspace | unchanged | unchanged |

Whether the Prod Platform database should be *the* operational store of the environment, or a new
`KratosForgeMetadata` database in the environment workspace, is CP-ADR-005. Recommendation: reuse
the Prod `SQL_Platform` when one exists (guardrail 9, "don't create a database when one can be
reused"), create `KratosForgeMetadata` only for an organization with no Platform yet.

### 4.2 Local-first

Unchanged in principle and already built for Optimize: work runs on this PC, rows wait in the
outbox, sync is idempotent. The control plane adds:

- a cached copy of the manifest at `<data>/controlplane/environments.json`, so the app knows its
  environment offline and shows **Working offline** rather than an error;
- one environment status built from the parts that exist (outbox sync state, operational store
  reachability, version compatibility): Connected, Working offline, Synchronizing, Upgrade
  required, Limited permissions, Connection failed;
- nothing new uploaded. What syncs is decided by each family's existing opt-in rules (D4 in the
  ESI plan: auto-sync off by default, one opt-in per installation).

### 4.3 Security

- No secrets in the manifest; a validator refuses token-, password- and connection-string-shaped
  values (reuses `redact.secrets`).
- Authorization stays with the provider: workspace roles for the lakehouse, `executing_user` checks
  in `UDF_Platform` for operational data. The installation id is never treated as proof of anything
  (prompt section 9).
- Personal data: the manifest names an owner by Entra object id only, no email or UPN (same as ESI
  D3).
- Creating anything in Fabric needs an explicit confirm in the UI and, for any run against a real
  tenant during development, Chris's typed "yes, run it" in `kf_tenant_test`.

### 4.4 Cost transparency

The environment's own operating cost is a separate figure from savings, always:

- Measured: sync seconds, rows and commits already written to `fdopt_sync` and as Measured
  `fdopt_measurements` (`optimize_service._record_overhead`).
- From Fabric, when the capacity metrics import exists (FinOps thread): CU used by the environment's
  items (lakehouse, SQL database, UDF).
- Unknown is shown as unknown. Net savings are shown only when the environment cost for the same
  period is known; otherwise gross savings with "environment cost not measured".

## 5. Provider capability matrix

Starting points only. **Verified** means a recorded live run; nothing in this table is verified for
central storage yet.

| Capability | Fabric | Azure Databricks | Snowflake |
| --- | --- | --- | --- |
| Environment namespace | Workspace (built, Preview) | Unity Catalog catalog + schema in a workspace (planned) | Database + schemas with roles (planned) |
| Analytical store | Lakehouse Delta tables via OneLake (built, Preview) | Delta tables in Unity Catalog (planned) | Snowflake tables; managed Iceberg where it fits (planned) |
| Manifest location | Lakehouse file (CP-002, planned) | UC volume file or table property (to verify) | Table or stage file (to verify) |
| Operational store | Fabric SQL database through UDF (built, Preview) | To decide: Lakebase/Postgres if available to the tenant, else SQL warehouse tables with optimistic concurrency (to verify) | Hybrid or standard tables with transactions (to verify) |
| Server-side authorization | UDF `executing_user` + workspace roles (built, Preview) | UC grants (to verify) | Roles and grants (to verify) |
| Identity | Entra ID, Fabric CLI public client + WAM (built) | Entra or Databricks OAuth (Databricks auth built for execution) | Not built |
| Governance | Fabric domains, workspace roles, OneLake security roles (built, Preview) | Unity Catalog | Snowflake access controls |
| Support level today (`docs/platforms.md`) | Built 4, verified 2 | Built 2, verified 0 | 0 |

Databricks and Snowflake stay **Not available** in the setup screen until a provider passes the
contract tests and a live end-to-end run (CP-ADR-007).

## 6. Environment manifest specification (v1)

Path: `Files/_kratosforge/environment.json` in the analytical store (Fabric). The leading
underscore keeps it out of the way of data folders; `_kratosforge` is reserved.

```json
{
  "manifest_version": 1,
  "environment_id": "kfenv_2f0c0c1e9c8a4c6f9b1d7a3e5f4b2a10",
  "name": "Contoso data platform",
  "provider": "fabric",
  "namespace": {"workspace_id": "<guid>", "display_name": "KratosForge"},
  "analytical_store": {"kind": "fabric_lakehouse", "workspace_id": "<guid>", "lakehouse_id": "<guid>",
                       "display_name": "KratosForgeLakehouse", "schemas": true},
  "operational_store": {"kind": "fabric_sql_udf", "workspace_id": "<guid>", "database_id": "<guid>",
                        "functions_id": "<guid>", "display_name": "KratosForgeMetadata"},
  "deployments": {"prod": {"platform_workspace_id": "<guid>"}, "keys": {"workspace_id": "<guid>"}},
  "schema": {"operational": 5, "families": {"fdopt_": 2, "fdacc_": 1}},
  "compatibility": {"min_app_version": "0.20.0", "written_by_app_version": "0.20.0"},
  "owner": {"object_id": "<entra object id>"},
  "created_at": "2026-11-19T20:00:00Z",
  "updated_at": "2026-11-19T20:00:00Z",
  "services": ["optimize", "savings", "access_cache", "incidents"],
  "capabilities": ["analytical_store", "operational_store"],
  "migration": {"state": "idle"},
  "config_refs": {"policy": "Files/_kratosforge/policy/"}
}
```

Rules (enforced by `controlplane/manifest.py`, CP-001):

- `environment_id` is `kfenv_` plus 32 lowercase hex, random at create, never derived from names.
- Every Fabric reference is a GUID. `display_name` fields are labels and never used to find a
  resource.
- `operational_store` may be `null` (lakehouse-only environment).
- `schema` records versions as last written; the authoritative versions stay in each store
  (`config.setting` and each family's `<prefix>schema_version`). A mismatch means the manifest is
  refreshed, not that the store is changed.
- No key may hold a secret. The validator refuses values `redact.secrets` would change, and any key
  named like `token`, `secret`, `password`, `connection_string`, `key`.
- Unknown keys are kept on rewrite (forward compatibility); a `manifest_version` newer than the app
  knows opens the environment read-only.
- `migration.state` is `idle`, `running` (with `by` installation and `started_at`, used as a lock
  with expiry), or `failed` (with `step`).
- The manifest is written with an ETag-conditional write so two installations can't overwrite each
  other; the loser rereads.

## 7. Provider abstraction interfaces

Python `Protocol`s in `controlplane/providers.py` (CP-003). Small on purpose (guardrail 13): one
interface per store plus discovery and provisioning, each mapped onto code that exists.

```python
class EnvironmentDiscovery(Protocol):
    provider: str
    def list_namespaces(self) -> list[Namespace]: ...                        # workspaces the user can open
    def find_environments(self, namespace_id: str) -> list[Found]: ...       # stores holding a manifest, read-only
    def read_manifest(self, ref: StoreRef) -> Manifest | None: ...

class PlatformProvisioning(Protocol):
    def plan(self, request: CreateRequest) -> ProvisionPlan: ...             # what would be created, costs, permissions; no writes
    def apply(self, plan: ProvisionPlan, confirmed: bool) -> ProvisionResult: ...  # refuses unless confirmed
    def write_manifest(self, ref: StoreRef, manifest: Manifest, etag: str | None) -> str: ...

class AnalyticalStorageProvider(Protocol):
    def inspect(self, family: Family) -> Inspection: ...                     # optimize/setup.inspect
    def initialize(self, family: Family, approve: bool) -> Inspection: ...    # optimize/setup.initialize
    def append(self, table: str, rows: DataFrame) -> int: ...               # insert-only MERGE on record_id
    def read(self, table: str, since: str | None = None) -> DataFrame | None: ...

class OperationalMetadataProvider(Protocol):
    def call(self, operation: str, **kwargs) -> Any: ...                     # central/client call shape; server checks caller
    def schema_version(self) -> int: ...

class HealthCheck(Protocol):
    def check(self, manifest: Manifest) -> list[CheckResult]: ...            # reachability, permissions, versions
```

Implementations in Phase 1: `fabric` (wraps existing code) and `local` (the folder store and the
SQLite platform; used by contract tests and offline single-user mode). Databricks and Snowflake
get capability declarations that say *not available* and contract-test skeletons that are skipped
with a reason, so they can't be shown as working (CP-ADR-007).

A shared contract test suite (`tests/test_controlplane_contract.py`) runs against every provider
that declares a capability. Scenario 9 is satisfied when `fabric` (against fakes) and `local` pass.

## 8. Migration of existing data

No data moves in Phase 1. Existing installs **adopt**:

1. An installation connected to a central lakehouse and/or a Platform runs **Adopt as KratosForge
   Environment** (CP-006): it previews the manifest it would write (ids only), the person confirms,
   and it writes the manifest into the existing lakehouse. No table is created, changed or copied.
2. A second installation then finds the environment by its manifest (CP-004) and connects without
   creating anything.
3. Identifiers are preserved: `record_id`s, `installation_id`s, key numbers, catalog ids.
4. Rollback: delete the manifest file. Both halves keep working through today's settings, which
   remain until the environment flow is proven live (do not delete existing storage until the
   replacement is verified).

Phase 2 integrations (FEA-501 snapshots, ESI reporting, Academy projection) register as families or
core schemas through their owner threads; the control plane only lists them in the service registry.

## 9. Acceptance scenarios, mapped

| # | Scenario | Stories | Proof |
| --- | --- | --- | --- |
| 1 | New Fabric environment | CP-005, CP-010 | Fakes in CI; live run needs "yes, run it" in `kf_tenant_test` (CP-012) |
| 2 | Second installation, no duplicates | CP-004, CP-006 | Contract test: connect never calls provisioning |
| 3 | Shared optimization results, no double count | Existing `record_id` MERGE; CP-012 live | Existing tests plus a two-installation contract test |
| 4 | Shared business key update | Existing `core.py`; CP-012 live | Existing tests |
| 5 | Local-first | CP-008 | Offline status test |
| 6 | Sync recovery | Existing outbox (ESI-105) | Existing tests |
| 7 | Schema upgrade | CP-007 | Compatibility tests |
| 8 | Authorization | Existing UDF checks; CP-012 live | Live only |
| 9 | Provider independence | CP-003 | Contract suite |
| 10 | Cost transparency | CP-011 with FinOps | Unknown shown as unknown |
| 11 | Existing data preserved | CP-006 | Adopt writes one file only |
| 12 | Concurrent access | Existing insert-only design; manifest ETag (CP-002) | Contract test with two writers |

## 10. Ownership and coordination

| Area | Owner | This thread's part |
| --- | --- | --- |
| `optimize/schema.py`, `shared.py`, `setup.py` | Lakehouse optimize thread | Reads them through the provider; any change goes to that thread for co-sign |
| `central/*` (Platform, keys, catalog) | Platform/mesh owners, Governance (policies) | Wraps `client.py`; no schema changes in Phase 1 |
| Savings, measurements, cost | ESI, FinOps, Optimization engine | Cost view reads their tables (CP-011) |
| Assessment history | FEA (FEA-501) | Listed in the service registry |
| Settings → KratosForge Environment UI and dashboard | UX thread mockup, Chris approves | Builds after approval (CP-009, CP-010) |
| Databricks, Snowflake | Dbx parity thread; Snowflake none | Contract skeletons only (CP-013, CP-014) |
