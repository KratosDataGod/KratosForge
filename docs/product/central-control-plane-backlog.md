# Central control plane: stories

Stories for the [central control plane plan](central-control-plane-plan.md), in build order.
Priority: P0 blocks the Fabric foundation, P1 completes it, P2 follows. "Evidence" cites code at
`main` 82f8e21. A story is done only when its tests pass in CI and, where it says so, a live run in
`kf_tenant_test` passed with Chris's "yes, run it". A design or a fake-backed test alone is not done.

## Phase 1: Fabric foundation

### CP-001 Environment manifest model and validator (P0) **Built** (engine only, #3110)
- **Problem:** nothing identifies a KratosForge Environment; the Platform and the central lakehouse are configured apart (gap G1, G2).
- **User value:** a second PC can tell for sure that what it found is the organization's environment.
- **Evidence:** no manifest exists (`grep environment.json` finds only Fabric Environment items); `optimize/registry.py`, `central/service.py._platform_config`.
- **Solution:** `controlplane/manifest.py`: dataclass-free dict model, `new_manifest`, `validate`, `to_json`/`from_json`, `kfenv_` ids, secret refusal via `redact.secrets`, unknown keys kept, newer version read-only.
- **Dependencies:** none.
- **Security:** refuses secret-shaped keys and values; owner by object id only.
- **Acceptance:** valid manifests round-trip byte-stable; every rule in plan section 6 has a refusing test.
- **Tests:** unit tests, no network.
- **Owner:** Central control plane thread.

### CP-002 Manifest store with conditional writes (P0) **Built** (engine only, #3110)
- **Problem:** two installations writing the manifest at once could overwrite each other.
- **User value:** safe concurrent setup and upgrade (scenario 12).
- **Evidence:** OneLake file access exists (`onelake.FileStore`), lease pattern in `leases.py` uses ETags.
- **Solution:** `controlplane/store.py`: read and write `Files/_kratosforge/environment.json` with If-Match / If-None-Match; local folder implementation for tests; offline cache at `<data>/controlplane/environments.json`.
- **Dependencies:** CP-001.
- **Security:** writes only that one path; dry run writes nothing.
- **Acceptance:** a stale ETag write is refused and the caller rereads; create refuses when a manifest exists.
- **Tests:** unit tests on the local store and a faked OneLake store.
- **Owner:** Central control plane thread.

### CP-003 Provider contracts and capability declarations (P0) **Built** (engine only, #3110)
- **Problem:** no provider-neutral contract for central storage (gap G4).
- **User value:** Databricks and Snowflake can be added without rewriting features (scenario 9).
- **Evidence:** `central/client.py` (`LocalPlatform`, `FabricPlatform`), `optimize/lakehouse.py` (`_Tables`), `engines/capabilities.py` (pattern to reuse).
- **Solution:** `controlplane/providers.py`: the five Protocols in plan section 7, `ProviderDescriptor` with per-capability level and evidence (same rules as `EngineDescriptor`), `local` provider, declarations for `databricks` and `snowflake` claiming nothing.
- **Dependencies:** CP-001.
- **Security:** provisioning `apply` refuses without `confirmed=True`.
- **Acceptance:** a descriptor that claims a capability without evidence is refused; contract suite passes on `local`; Databricks/Snowflake skip with a reason.
- **Tests:** contract suite `test_controlplane_contract.py`.
- **Owner:** Central control plane thread.

### CP-004 Fabric discovery: find environments read-only (P0) **Built** (engine only, #3110)
- **Problem:** joining finds items by name (gap G2).
- **User value:** "Connect to existing" lists real environments the person can open, from any PC.
- **Evidence:** `optimize_service.optimize_discover`, `fabric.list_lakehouses`, `central/setup.join`.
- **Solution:** `controlplane/fabric.py` `FabricDiscovery`: list workspaces, list lakehouses, read the manifest file per lakehouse (bounded, parallel-safe), and report legacy candidates (a lakehouse with `fdopt_schema_version` but no manifest) as "can be adopted".
- **Dependencies:** CP-002, CP-003.
- **Security:** read-only; no item created; no data read beyond the manifest and table names.
- **Acceptance:** with fakes, discovery never calls a create or write API; a renamed lakehouse is still found.
- **Tests:** fake Fabric client; assert zero write calls.
- **Owner:** Central control plane thread.

### CP-005 Fabric provisioning plan and apply (P0) **Built** (engine only, #3121)
- **Problem:** two separate setups (gap G3); nothing explains costs and permissions in one place.
- **User value:** one guided "Create environment", with what will be created, what it costs and which permission each step needs, before anything happens.
- **Evidence:** `central/setup.setup`, `optimize_service.optimize_create`, `optimize/setup.initialize`.
- **Solution:** `FabricProvisioning.plan()` returns steps (workspace find-or-create, lakehouse find-or-create with schemas, families to initialize, optional operational store), each with permission and cost notes; `apply(plan, confirmed)` runs them by calling the existing functions, writes the manifest last.
- **Dependencies:** CP-004.
- **Security:** no paid resource without confirm; capacity choice explicit; refuses names that collide with an existing non-KratosForge item.
- **Acceptance:** plan makes no writes; apply with `confirmed=False` refuses; a rerun after a failed step finishes without duplicates.
- **Lakehouse optimize co-sign conditions (2026-11-19):** pass this PC's `Installation` to `setup.initialize(lake, installation, approve, family)` and keep `setup.inspect`'s state dict; `plan()` maps `newer` and `conflict` to a blocked step and never calls initialize on them, and `apply` shows that message without retrying; `apply(confirmed=False)` relies on `approve=False` refusing before any write, and with `confirmed=True` the core family is initialized first under the same approval; rows go through `optimize/shared.py` and the outbox (provenance and `record_id` MERGE), never a new writer, and reads keep the newer-schema read-only rule; the environment config row uses a family-prefixed key in the existing config table with `record_id` `env:<key>`. Changes to `schema.py`, `shared.py` or `setup.py` go to that thread for co-sign.
- **Tests:** fakes. Live: part of CP-012.
- **Owner:** Central control plane thread; Lakehouse optimize co-sign for `optimize/setup` calls.

### CP-006 Adopt an existing install as an environment (P0) **Built** (engine only, #3121)
- **Problem:** existing users have a central lakehouse and/or Platform but no environment.
- **User value:** keep every row; one confirm makes it a KratosForge Environment (scenario 11).
- **Evidence:** `optimize/registry.py` connections, `central/service._platform_config`.
- **Solution:** build the manifest from current connections (ids only), preview it, write it on confirm. No table touched.
- **Dependencies:** CP-002, CP-004.
- **Security:** preview shows every id that will be written; nothing else written.
- **Acceptance:** adopt writes exactly one file; deleting it returns the install to today's behavior.
- **Tests:** fakes; assert the only write is the manifest path.
- **Owner:** Central control plane thread.

### CP-007 Environment compatibility check (P1) **Built** (engine only, #3121)
- **Problem:** no one place says "this environment needs an upgrade" (gap G5).
- **User value:** clear Upgrade required / Read-only status instead of failures.
- **Evidence:** `core.SCHEMA_VERSION`, `optimize/setup.inspect` states.
- **Solution:** combine each family's inspect state and the operational version into one result; migration lock per CP-ADR-009.
- **Dependencies:** CP-003.
- **Security:** read-only check; upgrade needs confirm.
- **Acceptance:** newer anywhere means read-only for that store; older means Upgrade required with the list of steps.
- **Tests:** unit tests over inspect fixtures.
- **Owner:** Central control plane thread.

### CP-008 Environment status and offline behavior (P1) **Built** (engine only, #3121)
- **Problem:** no single status; offline looks like failure.
- **User value:** Connected, Working offline, Synchronizing, Upgrade required, Limited permissions, Connection failed, each with a next action.
- **Evidence:** `optimize/outbox.sync_state`, `central/client` errors.
- **Solution:** `controlplane/status.py` pure function over the cached manifest, outbox state, compatibility and the last reachability result.
- **Dependencies:** CP-002, CP-007.
- **Security:** none new.
- **Acceptance:** every status has a test and a next-action text; no network call when offline.
- **Tests:** unit tests.
- **Owner:** Central control plane thread.

### CP-009 RPC methods for the environment (P1) **Built** (engine only, #3131)
- **Problem:** the UI needs calls.
- **User value:** the Settings screen can be built on stable calls.
- **Solution:** `controlplane_service.py` mixin: `cp_status`, `cp_discover`, `cp_plan`, `cp_create`, `cp_adopt`, `cp_connect`, `cp_disconnect`, `cp_check`. Policy key `allow_central_environment` (default on) joins the policy-key list and the loosening rules where relevant.
- **Dependencies:** CP-004 to CP-008.
- **Security:** Security QC review (inbound surface, file writes, approval path).
- **Acceptance:** each method refuses when policy turns it off; create and adopt require `confirmed`.
- **Tests:** service tests with fakes.
- **Owner:** Central control plane thread.

### CP-010 Settings → KratosForge Environment UI (P1, mockup approved 2026-11-20, built as Preview)
- **Problem:** no screen.
- **User value:** create, connect, switch, validate, upgrade, repair, export configuration, disconnect.
- **Solution:** UX thread mockup first; Chris approves; then build in `features.js` conventions, Preview label.
- **Dependencies:** CP-009, mockup approval.
- **Security:** confirms use `confirmDialog`; Prod-named targets require typing the name.
- **Acceptance:** per approved mockup; browser test.
- **Tests:** browser test, accessibility check.
- **Owner:** UX thread (mockup), this thread (build).
- **Built:** `app/ui/kf-environment.js` (Settings > Environment), with `cp_upgrade_plan` and `cp_upgrade` for Upgrade under the same token, confirm and typed-name rules as Create; browser test `app/tests/browser/settings-environment.test.cjs`. Export configuration and repair are not in the approved mockup and wait on their own.

### CP-011 Environment cost beside savings (P1) **Built** (engine only, #3137)
- **Problem:** environment operating cost isn't shown or separated from savings (gap G6).
- **User value:** honest net figures (scenario 10).
- **Evidence:** `fdopt_sync`, Measured overhead rows from `optimize_service._record_overhead`.
- **Solution:** read-only summary: measured sync overhead, Fabric CU for the environment's items when the FinOps import provides it, otherwise Unknown; net shown only when both are known.
- **Dependencies:** FinOps capacity import; ESI reporting schema (ESI-501).
- **ESI notes (2026-11-19):** read `fdopt_` the way `setup.write` does and refuse a newer schema; keep measured, estimated and scenario figures apart and unknown as unknown (`measurement.py`); the Fabric CU import waits on ESI decisions D3 and D4, so until then there is no measured Fabric CU to read; currency comes from `savings/pricing.py` and is always a Scenario figure.
- **Security:** none new.
- **Acceptance:** with no cost data, net savings is never shown.
- **Tests:** unit tests.
- **Owner:** this thread with ESI and FinOps.

### CP-012 Live validation in `kf_tenant_test` (P0 for "done", gated)
- **Problem:** nothing here is verified live (gap G8).
- **User value:** scenarios 1, 2, 3, 4, 8 proven.
- **Solution:** a scripted run: create an environment, connect a second installation, write optimization rows from both, update a business key from one user and read it from another, try a write as a non-member. Record results in `docs/platforms.md` live runs.
- **Dependencies:** CP-005, CP-006, CP-009. Chris's typed "yes, run it". Trial or F2 capacity.
- **Security:** test workspace only; never Prod.
- **Acceptance:** each scenario's outcome recorded.
- **Owner:** this thread, run on Chris's PC through Remote Control.

## Phase 2 and later

### CP-013 Databricks provider (P2)
Problem: no Databricks central storage. Value: one environment on Databricks. Evidence: `engines/databricks_*` (execution only). Solution: UC catalog/schema namespace, Delta analytical store, operational store decision after verifying what the tenant offers. Dependencies: CP-003. Security: UC grants server-side. Acceptance: contract suite plus live run. Tests: contract, live. Owner: Dbx parity thread.

### CP-014 Snowflake provider (P2)
Problem: Snowflake is level 0. Value: one environment on Snowflake. Evidence: `snowflake_coverage.py`, connection profiles only. Solution: sign-in first (level 1), then database/schema namespace and tables. Dependencies: CP-003. Security: roles and grants. Acceptance: contract suite plus live run. Owner: unassigned (needs a thread).

### CP-015 Service registry and inventory view (P2)
Problem: can't see which feature uses which tables (gap G7). Value: find orphaned or duplicate data. Evidence: `optimize/schema.FAMILIES`, `core.py` table list. Solution: registry built from families and a core table list; dashboard panel. Dependencies: CP-010. Owner: this thread.

### CP-016 Optional central projection of developer and Academy history (P2, needs Chris's go)
Problem: profile and Academy progress are local by design (gap G10). Value: team views. Solution: an opt-in `fdkfx_` family, off by default, no code or query text. Dependencies: Chris's typed go; Ranks, Achievements, Academy threads. Security: private by default; names off.

### CP-017 FEA snapshots and ESI reporting registered in the environment (P2)
Owned by FEA (FEA-501) and ESI. This thread only lists them in the service registry and status.
