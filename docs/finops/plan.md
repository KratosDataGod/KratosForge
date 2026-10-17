# Administration and FinOps program (FINOPS-001 to FINOPS-005)

Fabric Desktop by KratosBI helps developers understand, predict and avoid Fabric capacity use without becoming a copy of the Fabric admin portal or the Capacity Metrics app. This program adds evidence to what the app already shows: where each CU and cost number comes from, what capacity a workload would run on, what is risky before a cloud run, and when this PC is the better place to run.

Rules for every PR in this program:

- No invented CU, savings, billing or performance numbers. Each figure is **measured**, **estimated**, **scenario** or **unknown** ([cost-evidence.md](cost-evidence.md)).
- Ordinary developers never need Fabric administrator rights. No capacity changes and no cloud runs happen on their own.
- Only documented Microsoft APIs. The Capacity Metrics semantic model is not a production dependency.
- No embedded AI; external tools reach these services through the AI integration thread's MCP and local API.
- No Git changes (the Git-native development program owns Git, deployment and validation).

## Audit (2026-10-17, main at 4079a41)

PRs #5, #32, #58 and #94 are merged and present on main.

| Feature | Existing | Partial | Missing | Evidence | Proposed action |
| --- | --- | --- | --- | --- | --- |
| FINOPS-001 Measurement and cost evidence | | ✓ | | `costs.py` estimates CU from run time x configured CUs and prices it; Monitor > Cost (`app/ui/estate.js` `costBody`) labels the whole panel "estimates" in a footnote. `scorecard.py` (Prove) already labels its figures measured, estimated or not available; side by side shows "(estimate)". Gaps: no shared schema; a metadata run without a run time counted as 0 CU (`seconds or 0`); Fabric jobs still running were dropped from the panel; the "saved" figure is a counterfactual but read like a measurement; no way to see how a number was worked out. | Shared `measurement.py`; `costs.py` emits classified measurements; Monitor > Cost shows a badge per figure and a "how this was worked out" dialog. **PR 1 (this PR).** |
| FINOPS-002 Capacity inventory and health | | ✓ | | `fabric_api.list_capacities` (paged; optional Capacity.Read.All, never prompted from Monitor); `readiness._capacity_check` (Active or not); `estate_service._env_capacities` (name and SKU per environment, cached without a timestamp); `FabricError` explains CapacityNotActive (#5); environments have a "Runs on" choice (#25, #32); run location shown (#58). Missing: region, a per-environment status with its observation time, stale marking, "no access" vs "not found", capacity context in the environment selector. | Extend `_env_capacities` into a capacity observation service with timestamps and access states; read-only summary in the environment selector and Monitor. |
| FINOPS-003 Measured CU import and reconciliation | | | ✓ | Nothing imports observed usage. Workloads plan item 62 records that there is no public per-capacity metrics API for non-admins. | Source-compatibility decision first (which Microsoft sources are supported and for whom), then a provider interface with a user-supplied export as the first provider. Likely a stop-and-ask point for Chris. |
| FINOPS-004 Predeployment risk analyzer | | ✓ | | `workload_service.workload_preflight` (Workload program), `meta_preflight` and the memory guard (#69), readiness checks, the `allow_fabric_runs` policy (#90), CapacityNotActive handling. Deployment validation (FD-113) is the Git program's. | A risk service that composes these checks into PASS, WARNING, BLOCKED or UNKNOWN with evidence, called from the existing preflight rather than a new one. |
| FINOPS-005 Local vs Fabric advisor | | ✓ | | Side by side history (`side_by_side.py`), Prove scorecards and receipts, notebook compatibility analysis, "Runs on" per notebook (#29). Prove's run planner (item 31) and placement recommendation are not built yet. | Build the advisor on Prove's receipts and side-by-side history, near the existing "Runs on" control; agree the split with the Prove program first. |

## Overlap with other workstreams

| Workstream (thread) | Owns | How FinOps uses it |
| --- | --- | --- |
| Workload management workspace | Workload identity (`workloads/model.py` ids), execution history, operations view, Monitor and Home, cost allocation (items 66 to 68) | Measurements carry its workload ids. Its cost allocation builds on the classifications here. Monitor and Home edits are announced to it first. |
| Prove this notebook | Receipts, scorecards, side by side, profiler and cost model (FD-119) | Scorecards already use measured, estimated and not available. Receipts are Prove's schema: a classification field is proposed to Prove, not added here. |
| Git-native development (DevOps) | Deployment, promotion, validation (FD-113, FD-120) | FINOPS-004 reads its validation results; nothing here changes Git. |
| SQL development | SQL query performance | Not used by FINOPS-001 to 005. |
| Enterprise governance and readiness | Policies (#90 policy keys), audit log (#89) | FINOPS-004 reads workspace policies; overrides are recorded through the audit log. |
| Product management | Feature inventory in `docs/product/` | FINOPS ids are registered there by that thread. |

## PRs

| PR | Feature | Status |
| --- | --- | --- |
| 1 | FINOPS-001 Unified CU measurement and cost evidence framework | Merged (#117) |
| 2 | FINOPS-002 Capacity inventory and health diagnostics ([capacity.md](capacity.md)) | Open for review |
| 3 | FINOPS-003 Measured CU import and reconciliation foundation | Not started (source decision first) |
| 4 | FINOPS-004 Predeployment capacity and CU risk analyzer | Not started |
| 5 | FINOPS-005 Local vs Fabric execution advisor | Not started |
