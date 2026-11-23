# ESI-109: collector (mode C) feasibility note

Status: feasibility note, from public Microsoft documentation only. Nothing here is built, and
nothing was created or called in Fabric. Building mode C still needs Chris's typed go.
Story: [ESI-109](enterprise-savings-intelligence-plan.md#phase-1-shared-telemetry-foundation).
Plan sections: 4.2 (ingestion modes) and 4.3 (identity, tenancy and access).

Documentation was read on 2026-11-19. Microsoft changes these pages; check them again before
building.

## 1. Question

Can a Fabric User Data Function (UDF) be the collector for Optimize's shared tables? That is:
desktops send it a batch, it checks the schema and tenant, stamps the caller from Fabric's own
context, and writes Delta rows to the `fdopt_` tables in the shared Optimization Lakehouse. Writers
would then need only Execute on the function, not Contributor on the workspace.

## 2. What mode C needs

1. **Write Delta tables** in a lakehouse, insert-only and idempotent by `record_id`, as
   `shared.py` does today with delta-rs (batches of up to 5,000 rows, one MERGE commit each,
   retried after a lost commit race).
2. **Take a batch** big enough to be worth a call, inside the request size limit.
3. **Finish in time**, including retries after a lost commit race.
4. **Name the caller** from Fabric, not from the request: tenant id and object id (plan 4.3, D3).
5. **Write without the caller's write rights.** The function must write with an identity other
   than the caller's. Otherwise each caller still needs Contributor and nothing is gained.
6. **Execute-only callers.** A person can be allowed to run the function without any role that
   lets them read the lakehouse.
7. **Known cost per call**, so OPT-009 can record the overhead and net benefit subtracts it.
8. **Behaviour under load**: many desktops syncing at once.

## 3. Findings

Sources (all on learn.microsoft.com):

- [S1] Service limits: `/fabric/data-engineering/user-data-functions/user-data-functions-service-limits`
- [S2] Connect to data sources: `/fabric/data-engineering/user-data-functions/connect-to-data-sources`
- [S3] Overview: `/fabric/data-engineering/user-data-functions/user-data-functions-overview`
- [S4] Python programming model: `/fabric/data-engineering/user-data-functions/python-programming-model`
- [S5] SDK reference, `FabricLakehouseClient`: `/python/api/fabric-user-data-functions/fabric.functions.fabriclakehouseclient`
- [S6] SDK reference, package index (error classes): `/python/api/fabric-user-data-functions/fabric.functions`
- [S7] Manage libraries: `/fabric/data-engineering/user-data-functions/how-to-manage-libraries`
- [S8] Invoke from a Python application: `/fabric/data-engineering/user-data-functions/tutorial-invoke-from-python-app`
- [S9] Portal editor: `/fabric/data-engineering/user-data-functions/user-data-functions-portal-editor`
- [S10] Share items: `/fabric/fundamentals/share-items`
- [S11] Fabric operations (meters): `/fabric/enterprise/fabric-operations#fabric-user-data-functions`
- [S12] Capacity throttling and smoothing: `/fabric/enterprise/throttling`
- [S13] REST API scopes: `/rest/api/fabric/articles/scopes`

| Requirement | Documented? | Source | Implication |
| --- | --- | --- | --- |
| Write Delta **tables** in a lakehouse | **No.** Lakehouses are listed as "read/write operations for Lakehouse files and … read-only operations for the SQL analytics endpoint". Tables are not listed as a write target | S2, S3 | The core of mode C as written is not a documented path. The documented write path is lakehouse **Files** |
| Lakehouse client surface | Yes: `FabricLakehouseClient` has `connectToSql()` (returns a `Connection`), `connectToFiles()` and `connectToFilesAsync()` (return a `DataLakeDirectoryClient`). No table method | S5 | A function can write files under `Files/`. Writing a Delta table would mean writing its log and Parquet files through the Files client, which no page describes |
| `deltalake` (delta-rs) in a function | Not documented. Only `fabric_user_data_functions` is named as included by default. PyPI libraries can be added in Library management. Uploaded private `.whl` files must be platform-independent and under 30 MB (28.6 MB in S1) | S7, S1 | Whether a native wheel such as `deltalake` installs from PyPI and runs is not documented. Not something to assume |
| Request payload | Yes: 4 MB, "all request parameters combined" | S1 | A batch must stay under 4 MB of JSON. Today's 5,000-row batch may not fit for wide rows; batches would be sized by bytes, not rows. `client.py` already keeps UDF_Platform calls "well under" this limit |
| Response size | Yes: 30 MB | S1, S6 | Not a constraint for a write that returns a count |
| Execution timeout | Yes, but three figures: 240 s "request execution timeout"; 100 s when "invoked through a public function endpoint"; the SDK's timeout error says "currently 200 seconds" | S1, S6 | The desktop would call the public endpoint, so plan for 100 s per call, including commit retries. Which figure applies when must be measured |
| Concurrency, rate limits, throttling of UDF calls | **Not documented** on the UDF pages. Execution is metered as an **Interactive** operation. Fabric throttles interactive operations by stages: 20 s delay past 10 minutes of future use, rejection past 60 minutes | S11, S12 | Many desktops syncing at once could be delayed or refused when the capacity is busy. Mode A's OneLake writes are not UDF calls; how they are throttled is outside this note |
| Caller identity | Yes: `@udf.context` gives `executing_user` with `Oid` ("the verified identity of the user or service principal"), `TenantId` and `PreferredUsername` ("mutable"), plus `invocation_id` | S4 | Covers plan 4.3: stamp `tenant_id` and `user_id` from `TenantId` and `Oid`. Do not use `PreferredUsername` (mutable, and D3 forbids names) |
| Identity used to write (managed connection) | **Not documented** for `@udf.connection`. Only **generic** connections are documented to use "the item owner's identity", and only for `CosmosDb` and `KeyVault` audiences | S4, S3 | Requirement 5 is unproven. If a managed lakehouse connection acts as the caller, writers still need write rights and mode C gives no access benefit |
| Service principal or workspace identity for connections | Documented as **not supported**: "can't use managed identity or workspace identity to connect to UDF supported connections" | S1 | The writing identity can't be a workspace identity. If it is the owner, it is a person's account, and only the owner can edit and publish |
| Permission to execute | Partly. An item can be shared with "Share, Edit and/or Execute"; Execute means "execute or cancel execution of the item". The client tutorial asks for delegated `UserDataFunction.Execute.All` or `Item.Execute.All` and Public access on | S9, S10, S8, S13 | An Execute-only share exists. Whether Execute alone (no workspace role) is enough to invoke from a client app is not stated outright |
| Writers can't read the store | Follows only if the two rows above hold | — | The access rule in plan 4.3 holds only if the function writes as someone other than the caller and callers get no read role |
| CU per call | Metered, not priced. "User Data Functions Execution", meter "User Data Function Execution (CU/s)", Interactive. Plus static storage of function metadata, and a OneLake read "every time a function is executed after a period of inactivity". **No CU rate or formula is documented** | S11, S1 | Cost per call can only be measured (Capacity Metrics app), not looked up. Which OneLake meters apply to the function's own writes to the lakehouse is not stated |
| Capacity reporting | Yes: the Capacity Metrics app reports "creation, publishing, execution, and storage" of UDFs | S1 | OPT-009 can take the figure from Capacity Metrics after the fact; the function itself can't see its own CU |
| Region | Yes: UDFs aren't available in some Fabric regions | S1 | Setup must check the capacity's region before offering mode C |
| Parameter rules | Yes: camelCase, no underscores, no `req`/`context`/`reqInvocationId` names, `dict` parameters without defaults | S4 | Already learned and coded in `central/udf.py` (live runs 6 to 8) |
| Publish cadence | Yes: only the owner can edit and publish; two-minute cooldown between publishes | S1 | Schema upgrades of the collector are owner-only and slower than an app update |

### What the repo already knows

- `central/udf.py` and `central/client.py` (UDF_Platform) call
  `/workspaces/{id}/userDataFunctions/{id}/functions/{name}/invoke` with the person's sign-in
  (`Item.Execute.All`), keep batches under the payload limit, retry reads safely, and make
  changes once through `call_once` with a call id. Those patterns carry over.
- Fabric refused parameter names with underscores (live run 6), `dict` defaults (live run 7)
  and the name `context` (live run 8). These are now in `docs/live-testing.md`.
- UDF_Platform writes to a **Fabric SQL database**, which S2 lists for read/write. It does not
  write a lakehouse table, so it is no precedent for requirement 1.
- `docs/platform.md` ("What isn't proven yet") says calling the functions with the Fabric
  sign-in, and `executing_user` naming the caller, have not run on a live tenant.

## 4. Risks

1. **No documented Delta table write.** Building mode C as written would rely on writing a
   Delta log through the Files client, with a library whose install is not documented. If it
   works today, nothing in the documentation promises it keeps working.
2. **Writer identity is unknown.** If the managed connection acts as the caller, mode C costs
   more than mode A and gains nothing on access.
3. **Owner is a person.** Generic connections use the owner's identity; service principals and
   workspace identity are not supported. Rows would be written as one person's account, and the
   collector breaks if that person leaves or loses access.
4. **Timeout.** 100 s on the public endpoint must cover a MERGE plus retries after lost commit
   races, which mode A handles by waiting as long as it needs.
5. **Throttling.** Every sync becomes an interactive operation on the capacity. A busy capacity
   delays or rejects syncs; the outbox keeps them, but they arrive later.
6. **Cost on every call.** Mode C adds compute on the capacity that mode A does not have. The
   rate is not documented, so the overhead can't be estimated in advance.
7. **Network policy.** Calls need Public access on the function; tenants that block it, or allow
   only Private Link, need IT (as `docs/security.md` already says for UDF_Platform).

## 5. Comparison with modes A and B (qualitative)

No CU figures are documented for any of these paths, so none are given.

| | Mode A: direct MERGE (today) | Mode B: staged files + merge | Mode C: collector UDF |
| --- | --- | --- | --- |
| Documented write path | OneLake's ADLS-compatible API with delta-rs, from the desktop | Desktop writes Parquet to `Files/inbox/`; merge step writes tables | Files: yes. Tables: **not documented** |
| Writer's rights | Contributor; writers can read everything | Write access to `Files/inbox/` (the plan doesn't name a role); read exposure as in mode A unless roles split it | Execute only, **if** the function writes as someone else (not documented) |
| Capacity use | OneLake transactions only; no Fabric compute per sync | OneLake transactions, plus the merge step (notebook on capacity, or a desktop) | Interactive UDF execution per call (CU/s meter), plus OneLake operations |
| Contention | Commit races between installations, retried | None between installations; one merge step | Commit races move into the function, inside a 100 s limit |
| Throttling exposure | Not via UDF throttling | Merge step only | Every sync |
| Batch size | 5,000 rows per commit | Any file size | Under 4 MB of request per call |
| Moving parts | Desktop only | Desktop and a merge runner | Desktop, a Fabric item, its owner, its libraries, public access |
| Status | Built; ESI-108 live check pending | Not built | Not built |

A variant follows from the documentation: **C over B.** The function writes the caller's
batch as a Parquet or JSON file under `Files/inbox/` (a documented write), stamped with
`Oid` and `TenantId` from the context, and a merge step folds the files into the `fdopt_` tables.
This keeps every step on a documented path. It still depends on risk 2 (the writing identity)
and adds the merge runner from mode B.

## 6. What a live check would have to measure

Only with Chris's typed "yes, run it", only in the test workspace `kf_tenant_test`, and only
on a throwaway lakehouse and function made for the check.

1. **Writing identity.** A caller with only Execute on the function, and no workspace role,
   invokes it. Does the managed lakehouse connection write? Who does OneLake record as the
   writer: the caller or the owner?
2. **Execute-only invoke.** Does Execute alone let a client app call the public endpoint, or is a
   workspace role also needed?
3. **Read exposure.** Can that Execute-only caller read the lakehouse in any way?
4. **Table write.** Can a function write a Delta commit to a table (with `deltalake` installed
   from PyPI, or through the Files client), and do the SQL endpoint and other readers see it?
   If not, does a `Files/inbox/` write work?
5. **Library install.** Does `deltalake` install from Library management and import at run time?
6. **Payload.** Largest `fdopt_` batch that fits in 4 MB, per table.
7. **Timing.** Time per call for one batch, and with two and with several callers at once; how
   many commit races and retries; which timeout applies (100, 200 or 240 s).
8. **CU per call.** "User Data Functions Execution" CU seconds per call and per row, and the
   OneLake operations, from the Capacity Metrics app, beside ESI-108's figures for mode A.
9. **Throttling.** What the caller receives when the capacity delays or rejects the call (status
   and body), so the outbox can keep and retry it.

## 7. Recommendation

**Recommendation: do not build mode C as written now.** Keep mode A as the default and run
ESI-108 first.

Why: the one thing mode C is for is separating writers from readers. That depends on the function
writing with an identity other than the caller's, which is not documented for managed
connections. And writing Delta tables in a lakehouse from a function is not a documented path:
the documentation offers lakehouse Files (read/write) and the SQL analytics endpoint (read-only).
Mode C also adds an interactive capacity charge on every sync, at a rate that is not documented.

If an organization needs writers kept from readers before then, the documented route to look at
is **C over B** (function writes to `Files/inbox/`, a merge step writes the tables). It still needs
live check items 1 to 3 and 9 to pass first.

What would change this recommendation:

- Microsoft documents lakehouse **table** writes from UDFs, or the live check shows a supported
  table write, and the writing identity is the owner's or a connection's, not the caller's:
  then mode C as written becomes worth costing.
- The live check shows the managed connection writes **as the caller**: then mode C, and C over
  B, give no access benefit and should be dropped. Separation would then mean a separate
  lakehouse per group (as `docs/optimize/security.md` already suggests).
- Microsoft documents service principal or workspace identity for UDF connections: this removes
  risk 3 (owner is a person).
- ESI-108 shows mode A's commit contention is a real problem: then mode B comes first, and C over B
  becomes a smaller step on top of it.
- Measured CU per call is small next to the savings it records: then risk 6 drops.

Building any of this needs Chris's typed go.
