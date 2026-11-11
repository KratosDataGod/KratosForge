---
site_group: Internal engineering
---

# Databricks Data Engineer Associate path (plan)

> **Internal — engineering working material.** This is a dated implementation, design, or planning record. Use the user guides for current instructions and check releases for shipped behavior.

The second KratosForge Academy certification path, after [DP-700](../README.md). It is a content
pack and an execution adapter on the Academy that DP-700 built, not a second Academy: the same
registry, exercise format, validators, learner workspace, sandbox, evidence rows, readiness and
exam engine.

**Certification:** Databricks Certified Data Engineer Associate. **Exam:** published May 4, 2026;
**exam guide:** updated August 6, 2026 (the version the certification page links today; the May 4
guide is its earlier text, see [coverage](coverage.md#which-guide)). 45 scored multiple-choice
questions, 90 minutes, 7 sections, 33 objectives.

**Product principle.** Learners become data engineers who know how Databricks works, not people who
memorised answers. Every pass says what ran, where, and what it does not prove.

## Status

Planning and first engine PRs (2026-11-03). Nothing here is a shipped feature. New screens wait for
a mockup Chris has reacted to. No Databricks compute, workspace call or chargeable resource is used
without Chris's typed OK inside this project, and each learner's own go in the product.

## The plan

| # | Deliverable | Page |
| --- | --- | --- |
| 1 | Repository capability audit | [Capability audit](capability-audit.md#what-exists) |
| 2 | Reuse and gap analysis against the DP-700 Academy | [Capability audit](capability-audit.md#reuse-and-gaps) |
| 3 | Official objective coverage matrix | [Coverage](coverage.md) |
| 4 | Backlog DBA-001 to DBA-016 | [Backlog](backlog.md) |
| 5 | Inventory of 64 exercises (12 troubleshooting labs), 4 supplementary, 3 capstones | [Curriculum](curriculum.md) |
| 6 | Databricks execution adapter architecture | [Adapter](adapter.md) |
| 7 | Implementation dependency graph | [Delivery plan](delivery-plan.md#dependency-graph) |
| 8 | Agent and thread assignments | [Delivery plan](delivery-plan.md#threads) |
| 9 | Initial implementation PR plan | [Delivery plan](delivery-plan.md#first-prs) |
| 10 | Acceptance and verification strategy | [Verification](verification.md) |

## Ground rules

- **Four classes, one more name.** LOCAL VERIFIED, SIMULATED, DATABRICKS VERIFIED, KNOWLEDGE ONLY.
  DATABRICKS VERIFIED is the Databricks pack's platform-verified class, the twin of FABRIC VERIFIED;
  only the `databricks` adapter can give it. Local work never counts as Databricks-verified.
- **No Spark where there is no Spark.** The notebook's `spark` on this PC is DuckDB's
  PySpark-compatible API, not Apache Spark ([engines](../../engines.md)). Exercises that grade a
  table's contents can be LOCAL VERIFIED on it, labelled "DuckDB's PySpark API on this PC". Exercises
  that teach Spark's own behaviour (shuffle partitions, broadcast thresholds, the Spark UI, skew,
  spill) are SIMULATED from recorded Spark metrics until real Apache Spark runs on this PC (an open
  decision, [DBA-003](backlog.md#dba-003)) or the learner runs them in Databricks.
- **Original content only.** Practice questions are KratosForge's own. They are never presented as
  Databricks exam content and no exam dumps are used. No Databricks interface assets are copied.
- **Readiness is an estimate.** It never predicts an official result.
- **Results keep their version.** Evidence stays tied to the exam guide version it was earned under.
