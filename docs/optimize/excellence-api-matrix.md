# Fabric Excellence Assessment: API capability matrix

Every read the [excellence assessment](excellence-assessment.md) makes, the permission each one needs,
and the controls that depend on it. Each row was checked against Microsoft's REST reference page it
links to, on the date in its last column. The same matrix is in the engine as data
(`sidecar/fabric_desktop/excellence/api_matrix.py`), and a test keeps this page identical to it.

No read needs a Fabric administrator. Every one uses the signed-in person's own workspace role, so
the assessment works without tenant admin rights. A read the person's role doesn't allow fails, is
listed under the assessment's reads, and leaves the controls that need it **not evaluated**. It never
counts as a pass. A control can't ship unless every read it relies on is in this matrix, checked and
not admin-only.

## What each workspace role can assess

- **Admin or Member:** everything below.
- **Contributor:** everything except the security controls (FEA-SEC-001 to 004). Listing a workspace's
  role assignments needs Member or higher.
- **Viewer:** the workspace's settings, items and capacities, plus lakehouse and warehouse properties.
  Not item definitions (notebook, pipeline and semantic model controls), which need write permission
  on the item. Not Git status or connection, which need Contributor. Not lakehouse tables through
  OneLake, unless a OneLake security role gives the Viewer read access.

The capacity controls need a capacity the person can see: one they're an administrator or
contributor of. The capacity usage controls also use Capacity Metrics exports imported on this PC,
which is a local read with no Fabric call.

## Throttling

Fabric limits calls per identity in fixed one-minute windows. Platform APIs allow 500 calls a minute,
Job Scheduler APIs 200 and long-running operations 500. A single API can have a lower limit; the Git APIs
allow 50. Over a limit, Fabric answers 429 with Retry-After. The assessment reads one workspace and
caps its own reads (at most 50 lakehouses, 50 warehouses, 30 semantic models, 300 OneLake folder
listings and 100 Delta logs), so it stays well under these. See Microsoft's
[throttling article](https://learn.microsoft.com/rest/api/fabric/articles/throttling).

## The reads

<!-- api-matrix:start -->
| Read | Scopes (any one) | Role or permission | Paging | Long-running | Throttling | Controls | Checked |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [`GET /v1/workspaces/{workspaceId}`](https://learn.microsoft.com/rest/api/fabric/core/workspaces/get-workspace) | `Workspace.Read.All`, `Workspace.ReadWrite.All` | Viewer or higher workspace role | none (one object) | No | Platform APIs, 500 calls a minute per identity; 429 with Retry-After | FEA-ARCH-001, FEA-ARCH-003, FEA-GOV-001 | 2026-11-26 |
| [`GET /v1/workspaces/{workspaceId}/items`](https://learn.microsoft.com/rest/api/fabric/core/items/list-items) | `Workspace.Read.All`, `Workspace.ReadWrite.All` | Viewer or higher workspace role | continuationToken and continuationUri | No | Platform APIs, 500 calls a minute per identity; 429 with Retry-After | FEA-ARCH-001, FEA-ARCH-003, FEA-ARCH-004, FEA-DE-001, FEA-DE-002, FEA-PERF-001, FEA-PERF-002, FEA-PERF-003, FEA-PERF-004, FEA-PERF-005, FEA-WH-001, FEA-WH-002 | 2026-11-26 |
| [`POST /v1/workspaces/{workspaceId}/items/{itemId}/getDefinition`](https://learn.microsoft.com/rest/api/fabric/core/items/get-item-definition) Viewers can't read a definition, so the notebook, pipeline and semantic model controls are not evaluated for them. Notebooks are read as ipynb, semantic models as TMSL. An item with an encrypted sensitivity label needs the right to remove the label. | `Item.ReadWrite.All`, `<ItemType>.ReadWrite.All` | Read and write permission on the item | none | Yes | Platform APIs, 500 calls a minute per identity; 429 with Retry-After | FEA-ARCH-004, FEA-DE-001, FEA-DE-002, FEA-DE-003, FEA-DEVOPS-001, FEA-DEVOPS-002, FEA-PERF-001, FEA-PERF-002, FEA-REL-001, FEA-REL-002, FEA-REL-003, FEA-REL-004, FEA-REL-005, FEA-REL-006, FEA-SEC-005, FEA-SM-001, FEA-SM-002, FEA-SM-003, FEA-SM-004, FEA-SM-005, FEA-SM-006 | 2026-11-26 |
| [`GET /v1/workspaces/{workspaceId}/items/{itemId}/jobs/instances`](https://learn.microsoft.com/rest/api/fabric/core/job-scheduler/list-item-job-instances) Most items keep at most 100 recently completed runs. | `Item.Read.All`, `Item.ReadWrite.All`, `<ItemType>.Read.All`, `<ItemType>.ReadWrite.All` | Not stated on the reference page (it lists InsufficientPrivileges when the caller can't access the item) | continuationToken and continuationUri | No | Job Scheduler APIs, 200 calls a minute per identity; 429 with Retry-After | FEA-DE-001, FEA-DE-002, FEA-PERF-001, FEA-PERF-002 | 2026-11-26 |
| [`GET /v1/workspaces/{workspaceId}/roleAssignments`](https://learn.microsoft.com/rest/api/fabric/core/workspaces/list-workspace-role-assignments) A Contributor or Viewer is refused, so the security controls are not evaluated for them. | `Workspace.Read.All`, `Workspace.ReadWrite.All` | Member or higher workspace role | continuationToken and continuationUri | No | Platform APIs, 500 calls a minute per identity; 429 with Retry-After | FEA-SEC-001, FEA-SEC-002, FEA-SEC-003, FEA-SEC-004 | 2026-11-26 |
| [`GET /v1/capacities`](https://learn.microsoft.com/rest/api/fabric/core/capacities/list-capacities) A capacity the caller can't see leaves the capacity controls not evaluated. | `Capacity.Read.All`, `Capacity.ReadWrite.All` | Capacities the caller is an administrator or contributor of (a capacity role, not tenant admin) | continuationToken and continuationUri | No | Platform APIs, 500 calls a minute per identity; 429 with Retry-After | FEA-CAP-001, FEA-CAP-002, FEA-CAP-003, FEA-CAP-004 | 2026-11-26 |
| [`GET /v1/workspaces/{workspaceId}/lakehouses/{lakehouseId}`](https://learn.microsoft.com/rest/api/fabric/lakehouse/items/get-lakehouse) Gives defaultSchema (schema-enabled lakehouses only) and the SQL analytics endpoint's provisioningStatus (InProgress, Success or Failed). | `Lakehouse.Read.All`, `Lakehouse.ReadWrite.All`, `Item.Read.All`, `Item.ReadWrite.All` | Read permission on the lakehouse | none (one object) | No | Platform APIs, 500 calls a minute per identity; 429 with Retry-After | FEA-ARCH-001, FEA-ARCH-002, FEA-ARCH-003, FEA-WH-001 | 2026-11-26 |
| [`GET /v1/workspaces/{workspaceId}/warehouses/{warehouseId}`](https://learn.microsoft.com/rest/api/fabric/warehouse/items/get-warehouse) Gives properties.collationType. The connection string it also returns is never kept. | `Warehouse.Read.All`, `Warehouse.ReadWrite.All`, `Item.Read.All`, `Item.ReadWrite.All` | Read permission on the warehouse | none (one object) | No | Platform APIs, 500 calls a minute per identity; 429 with Retry-After | FEA-WH-002 | 2026-11-26 |
| [`GET /v1/workspaces/{workspaceId}/git/connection`](https://learn.microsoft.com/rest/api/fabric/core/git/get-connection) | `Workspace.Read.All`, `Workspace.ReadWrite.All` | Contributor or higher workspace role | none (one object) | No | Platform APIs; this API is limited to 50 calls a minute; 429 with Retry-After | FEA-DEVOPS-003, FEA-DEVOPS-004, FEA-DEVOPS-005 | 2026-11-26 |
| [`GET /v1/workspaces/{workspaceId}/git/status`](https://learn.microsoft.com/rest/api/fabric/core/git/get-status) Needs the caller's Git credentials configured in Fabric, and shouldn't run during an update from Git. The assessment asks for its scope silently and never prompts. | `Workspace.GitUpdate.All`, `Workspace.GitCommit.All` | Contributor or higher workspace role | none (one object) | Yes | Platform APIs; this API is limited to 50 calls a minute; 429 with Retry-After | FEA-DEVOPS-003, FEA-DEVOPS-004, FEA-DEVOPS-005 | 2026-11-26 |
| [`GET https://onelake.dfs.fabric.microsoft.com/{workspaceId}?resource=filesystem`](https://learn.microsoft.com/fabric/onelake/onelake-api-parity) Lists the folders under a lakehouse's Tables. Storage tokens only; asked for silently. | `https://storage.azure.com/user_impersonation` | Admin, Member or Contributor workspace role (a Viewer only through OneLake security roles) | x-ms-continuation header | No | ADLS Gen2 limits: 503 when over the account's limits; no 429 documented | FEA-PERF-003, FEA-PERF-004, FEA-PERF-005 | 2026-11-26 |
| [`GET https://onelake.dfs.fabric.microsoft.com/{workspaceId}/{itemId}/Tables/.../_delta_log`](https://learn.microsoft.com/fabric/onelake/security/get-started-security) Reads only each Delta table's log and checkpoint, never a data file. | `https://storage.azure.com/user_impersonation` | Admin, Member or Contributor workspace role (a Viewer only through OneLake security roles) | none | No | ADLS Gen2 limits: 503 when over the account's limits; no 429 documented | FEA-PERF-003, FEA-PERF-004, FEA-PERF-005 | 2026-11-26 |
| Capacity Metrics exports imported on this PC (Optimize > Cost and savings): a local read, no Fabric call, no permission | none | none | none | No | none | FEA-CAP-002, FEA-CAP-003, FEA-CAP-004 | n/a |

| Evidence | Built from |
| --- | --- |
| `workspace.info` | `get-workspace` |
| `workspace.inventory` | `list-items` |
| `workspace.roles` | `list-role-assignments` |
| `workspace.naming` | `get-workspace`, `list-items`, `get-lakehouse` |
| `workspace.git` | `get-git-connection`, `get-git-status` |
| `workspace.warehouses` | `list-items`, `get-warehouse` |
| `capacity.state` | `list-capacities` |
| `capacity.usage` | `list-capacities`, `local:capacity-metrics-imports` |
| `item.optimization` | `list-items`, `get-item-definition`, `list-job-instances` |
| `item.inspection` | `get-item-definition` |
| `item.model` | `get-item-definition` |
| `item.binding` | `get-item-definition` |
| `lakehouse.properties` | `get-lakehouse` |
| `lakehouse.tables` | `list-items`, `onelake-list`, `onelake-read` |
| `sqlendpoint.properties` | `list-items`, `get-lakehouse` |
| `warehouse.properties` | `get-warehouse` |
<!-- api-matrix:end -->

A `<ItemType>` scope means the item-specific scope, for example `Notebook.ReadWrite.All`. The app
signs in with the scopes in [permissions](../permissions.md); the assessment asks for nothing new.
