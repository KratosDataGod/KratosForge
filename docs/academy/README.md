---
site_group: Internal engineering
---

# KratosForge Academy (plan)

> **Internal — engineering working material.** This is a dated implementation, design, or planning record. Use the user guides for current instructions and check releases for shipped behavior.

KratosForge Academy is the practical, exercise-first learning and certification preparation part of
KratosForge. It grows out of [Learn](../learn.md): the same lessons, labs, lab runner, checks,
profile evidence and ranks, with certification paths on top. It is not a separate application.

The first certification path is **DP-700: Implementing Data Engineering Solutions Using Microsoft
Fabric**. Later paths (DP-600, Databricks, Snowflake, SQL, Python) are added as content packs and
execution adapters, not as copies of the Academy.

**Product principle.** Academy teaches people to engineer data solutions, not to memorize answers.
A learner who finishes the DP-700 path has built, run, broken, fixed and measured real artifacts,
and can see exactly which of them ran on this PC and which still need real Fabric experience.

The second path, **Databricks Certified Data Engineer Associate**, is planned in
[Databricks Associate path](databricks/README.md) on the same registry, format, validators and readiness.

## Status

Planning (2026-11-03). Nothing in this folder is a shipped feature yet.

The DP-700 objective registry (ACD-001) is data in
`sidecar/fabric_desktop/academy/packs/dp-700/certification.json`, read and checked by
`academy/registry.py`. CI fails if a published objective is missing or reworded without a new
snapshot, if an objective has no exercise, or if an objective that needs Fabric has no gap record. New Academy screens wait for
a mockup Chris has reacted to; the engine and content work goes first, as small PRs.

## The plan

| # | Output | Page |
| --- | --- | --- |
| 1 | Repository capability audit | [Capability audit and reuse](capability-audit.md) |
| 2 | Existing-feature reuse matrix | [Capability audit and reuse](capability-audit.md#reuse-matrix) |
| 3 | Complete DP-700 objective coverage matrix | [DP-700 coverage](dp-700-coverage.md) |
| 4 | Backlog for ACD-001 to ACD-020 | [Backlog](backlog.md) |
| 5 | Initial exercise curriculum (64 exercises, 12 broken environments, 3 capstones) | [Curriculum](curriculum.md) |
| 6 | Data and content architecture | [Architecture](architecture.md) |
| 7 | Ownership and thread assignments | [Delivery plan](delivery-plan.md#ownership) |
| 8 | PR dependency graph | [Delivery plan](delivery-plan.md#pr-dependency-graph) |
| 9 | Risks and gaps that need Fabric | [Fabric gaps and risks](fabric-gaps.md) |
| 10 | First implementation PR plan | [Delivery plan](delivery-plan.md#first-implementation-prs) |

## Ground rules

- **Honest verification.** Every exercise carries one class: LOCAL VERIFIED, SIMULATED,
  FABRIC VERIFIED or KNOWLEDGE ONLY ([what each means](architecture.md#verification-classes)).
  DuckDB, Polars and the local Spark API are never presented as Fabric Spark, Fabric Warehouse or
  Fabric SQL. Parity claims link evidence (for example the KUDP run: DuckDB matched Fabric Spark on
  9 of 10 queries; decimal division rounding differed).
- **Local first.** Exercises run on this PC by default. Fabric runs are optional, never needed for
  a locally executable lesson, and each one needs the learner's own go. Inside this project, any
  run that touches Fabric needs Chris's typed "yes, run it".
- **No official claims.** The readiness score is KratosForge's own estimate from evidence. It is
  never shown as Microsoft's exam score or a promise of passing. Questions are original; Academy
  does not copy Microsoft's exam interface or use real exam questions.
- **Results keep their version.** A learner's results stay tied to the certification version they
  were earned under. A new study guide version never rewrites them.
- **No placeholders.** An exercise counts as done only with an independently runnable reference
  solution and automated validation tests in CI.
