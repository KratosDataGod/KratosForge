# Prove this notebook: continuation note

The working state of the [program plan](plan.md). Whoever picks this up next starts here. Updated with every PR.

Last updated: 2026-10-16.

## Branches and PRs

| Stage | Branch | PR | State |
| --- | --- | --- | --- |
| 0 Plan | `claude/prove-this-notebook-plan-sxnf3y` | #55 | Open for review |
| 1 Foundations | `claude/prove-stage1-foundations` | #59 | Open for review; independent of #55 |
| 2 Proofs engine | `claude/prove-stage2-proofs` | #62 | Open for review; stacked on #59 and #55 |
| 3 First-release UI | `claude/prove-stage3-ui` | #65 | Draft until Chris reacts to the mockup; stacked on #62 |

Merge order: stages merge in number order. Each stage's PR lists any PR it is stacked on.

## Completed backlog items

None yet.

## In progress

- Stage 1 is in review (#59): stable cell ids, `receipts.py`, `envprofile.py`.
- Stage 2 is in review (#62): `proof_runtime.py`, `proof.py`, `verify.py`, `measure.py`, `scorecard.py`, `prove_*` requests in `prove_service.py`; guide in [how-proofs-work.md](how-proofs-work.md).
- Stage 3 is a draft (#65): `app/ui/prove.js` (panel, diff grid, scorecard, receipt export), `app/ui/wizard.js` and `sidecar/fabric_desktop/wizard.py` (three fixed templates, proof contract in notebook metadata `fabric_desktop.proof`). Mockup: https://claude.ai/artifact/Jqhxo9LVmqocGYgmT2ok7W (private to Chris's account).

## Decisions and assumptions

- Proof sandboxes live in the attached lakehouse under `Files/_fabric_desktop/proofs/<id>/<side>/`, so a proof creates no lakehouses or tables. The Fabric run publishes one notebook item, `<notebook>_proof`, in the notebook's workspace (as Run in Fabric does), so the user's own notebook and its schedules are never changed.
- Inputs are pinned to the Delta version of every table at proof start; files are verified by SHA-256 on both sides rather than copied.
- First-release proofs support notebooks whose writes go through `fabric_desktop.lakehouse` (Python notebooks in Fabric). Spark writes in Fabric block a proof with a reason.
- No AI features until Chris decides on the AI service and data policy (backlog 11, 13, 16, 18, 20, 50).
- Side by side stays as it is; Prove is added next to it, so nothing that works today changes.
- Ownership split with the sister plan (decided by the project coordinator, 2026-10-16): this plan owns environment profiles (33, 34, 36), isolated comparisons (51–60), compatibility analysis (32), process-isolated kernels and debugging (41–50) and the run receipt schema (81). The sister plan owns large-table execution, guided ingestion, Git and release workflow (82, 86 build on it), pipeline handoff, remote agent control, lineage (97 builds on it) and monitoring.
- The workload management program (thread "Workload management workspace") reuses this plan's receipt schema, run planner (31), placement recommendation (35) and scorecards and cost model (71–80). Keep them as engine modules returning plain dicts. Cost settings stay in the existing store (`costs.py`, `Service._cost_settings`).
- Not ours to change: the concurrent-writes work (PR #54) and `meta/keys.py`, `meta/store.py`, `xray_service.py`.

## Validation results

- main at af11938: engine tests 322 passed, 2 skipped (Linux, Python 3.13).
- #59: 337 passed, 2 skipped; UI tests pass.
- Stage 2: 353 passed, 2 skipped; UI tests pass; docs build strict.
- Stage 3: 366 passed, 2 skipped; 6 UI tests pass; docs build strict; panel, dialogs and wizard rendered in headless Chromium against a real proof record (engine calls stubbed in the browser). The Fabric side was exercised by running the exact published notebook in a subprocess with a fake `notebookutils`, not on a tenant.

## Blockers

- Live tenant checks: plan section 5, needs Chris.
- AI features: needs Chris's decision on service and data policy.

## Next action

When Chris reacts to the mockup, apply his changes to stage 3 and mark it ready. Then stage 4 (process-isolated kernels and an RPC timeout), on `claude/prove-stage4-kernels`, stacked on stage 3. `cloud_service.py` `_publish` and `run_notebook_in_fabric` get a hook from the workload program (PR #60); keep it if those change.
