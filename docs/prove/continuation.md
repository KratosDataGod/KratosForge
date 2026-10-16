# Prove this notebook: continuation note

The working state of the [program plan](plan.md). Whoever picks this up next starts here. Updated with every PR.

Last updated: 2026-10-16.

## Branches and PRs

| Stage | Branch | PR | State |
| --- | --- | --- | --- |
| 0 Plan | `claude/prove-this-notebook-plan-sxnf3y` | #55 | Open for review |
| 1 Foundations | `claude/prove-stage1-foundations` | #59 | Open for review; independent of #55 |

Merge order: stages merge in number order. Each stage's PR lists any PR it is stacked on.

## Completed backlog items

None yet.

## In progress

- Stage 1 is in review (#59): stable cell ids, `receipts.py`, `envprofile.py`.
- Stage 2: isolated proof runs and verification (engine).

## Decisions and assumptions

- Proof sandboxes live in the attached lakehouse under `Files/_fabric_desktop/proofs/<id>/<side>/`, so a proof creates no Fabric items or lakehouses (no cloud resources without Chris's OK).
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

## Blockers

- Live tenant checks: plan section 5, needs Chris.
- AI features: needs Chris's decision on service and data policy.

## Next action

Build stage 2 on `claude/prove-stage2-proofs`, stacked on #59 (it needs cell ids and receipts).
