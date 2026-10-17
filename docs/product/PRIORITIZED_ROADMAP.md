# Prioritized roadmap

The order in which Fabric Desktop by KratosBI should build the items in
[the product backlog](PRODUCT_BACKLOG.md), and why. Every number on this page is computed from
[PRODUCT_BACKLOG.json](PRODUCT_BACKLOG.json) by `docs/product/tools/product_data.py`, so changing a
score or a weight and running `product_data.py all` re-ranks everything the same way each time.

## Scoring model

Each item gets seven scores from 1 to 5. Five are benefits and two are costs.

| Dimension | Kind | Weight | 1 means | 5 means |
| --- | --- | ---: | --- | --- |
| Developer Value | benefit | 0.25 | Few developers notice | Changes daily work for most developers |
| CU Reduction | benefit | 0.20 | No effect on Fabric capacity use | Moves a large, frequent workload off Fabric compute, or prevents waste |
| Enterprise Value | benefit | 0.25 | Irrelevant to IT, security or governance buyers | A condition for enterprise rollout |
| Differentiation | benefit | 0.15 | Every competitor has it | Nobody else offers it for Fabric |
| Strategic Alignment | benefit | 0.15 | Off mission | Core to "develop and run locally, deploy to Fabric" and to "AI-ready, not AI-dependent" |
| Complexity | cost | 0.50 | One small, local change | A program of its own, or new architecture |
| Risk | cost | 0.50 | Hard to get wrong | Could damage data, security or trust if wrong, or depends on unverified platform behavior |

The calculation:

```text
benefit  = weighted mean of the five benefit scores        (1 to 5)
cost     = weighted mean of complexity and risk            (1 to 5)
score    = benefit x (6 - cost) / 5                        (0.2 to 5)
priority = the product owner's decision, when one is recorded (priority_override)
           else P0 if mandatory is "blocker"
           P1 if score >= 2.6, or mandatory is "required"
           P2 if score >= 1.8
           P3 otherwise
```

`(6 - cost) / 5` turns cost into a multiplier from 1.0 (cost 1) down to 0.2 (cost 5), so a cheap,
safe item keeps its full benefit and an expensive, risky one keeps a fifth of it. The weights and
thresholds live in the JSON `scoring` block; changing them is a product-owner decision and is
recorded in the change log below.

**Priority classes**

| Class | Meaning |
| --- | --- |
| P0 | Blocks the Stable launch to about 5,000 people. Do now. |
| P1 | Next development cycle. High value for its cost, or mandatory. |
| P2 | Following cycles, in score order, when capacity frees up. |
| P3 | Valuable but expensive, risky or waiting on a decision. Revisit each cycle. |

**Confidence** says how sure the scores and effort are given the evidence (High: code read and tests
run; Medium: some inference; Low: depends on a decision, a tenant behavior or another program's
design). **Effort**: S is one focused PR, M is one larger PR or two, L is three to five PRs, XL is a
program of its own.

## Mandatory work is ranked separately

Security, reliability and compliance work is not allowed to sink because its commercial score is
low. An item is `blocker` when the launch can't happen without it and `required` when shipping
without it would leave a known security, data-safety or reliability defect. Blockers are P0 and
required items are never below P1, whatever their score. Each states why.

<!-- BEGIN GENERATED: mandatory -->
| Id | Item | Kind | Commercial score | Priority | Why it is not ranked down |
| --- | --- | --- | ---: | --- | --- |
| [PB-002](PRODUCT_BACKLOG.md#pb-002) | Tenant smoke test of ten core flows | blocker | 2.76 | P0 | Launch to about 5,000 people needs evidence the core flows work on a real tenant. |
| [PB-001](PRODUCT_BACKLOG.md#pb-001) | Sign the app and engine | blocker | 2.48 | P0 | Without signing the app does not run on many managed PCs; the launch audience is about 5,000 people. |
| [PB-013](PRODUCT_BACKLOG.md#pb-013) | Fix local-parity defects that give wrong answers | required | 2.56 | P1 | Wrong-answer defects in local execution undermine the core promise and the rule that local results are never claimed equivalent without proof. |
| [PB-071](PRODUCT_BACKLOG.md#pb-071) | Run the Rust tests in CI | required | 2.45 | P1 | The watchdog, sign-in and update integrity code is security- and reliability-critical and untested in CI. |
| [PB-078](PRODUCT_BACKLOG.md#pb-078) | Preview tags match the docs | required | 2.45 | P1 | AGENTS.md requires untested Fabric integrations to keep Preview labels; enterprises rely on hiding Preview by policy. |
| [PB-063](PRODUCT_BACKLOG.md#pb-063) | Assistive technology audit | required | 2.21 | P1 | Enterprise procurement often requires an accessibility conformance statement. |
| [PB-010](PRODUCT_BACKLOG.md#pb-010) | Security and network docs match the shipped app | required | 2.2 | P1 | Security reviewers and firewall teams act on these documents; wrong docs block or mislead deployment. |
| [PB-074](PRODUCT_BACKLOG.md#pb-074) | Release pipeline hardening | required | 2.2 | P1 | Supply-chain controls are only as strong as the weakest workflow. |
| [PB-051](PRODUCT_BACKLOG.md#pb-051) | Fabric REST client resilience | required | 2.16 | P1 | Unsafe or missing retries are a reliability defect on every Fabric call; AGENTS.md asks reviewers to flag unsafe retries. |
| [PB-003](PRODUCT_BACKLOG.md#pb-003) | Prove OneLake leases and MERGE writes live | required | 2.08 | P1 | If OneLake ignores conditional requests, concurrent writers can overwrite data. |
| [PB-007](PRODUCT_BACKLOG.md#pb-007) | Audit trail of runs and changes | required | 2.08 | P1 | Enterprise compliance needs an audit record of changes to shared data. |
| [PB-005](PRODUCT_BACKLOG.md#pb-005) | Encrypt the token cache | required | 1.96 | P1 | Refresh tokens on disk unencrypted are a security finding any enterprise review will raise. |
| [PB-008](PRODUCT_BACKLOG.md#pb-008) | Keep the embedded Fabric view on Fabric hosts | required | 1.84 | P1 | The embedded view holds saved Fabric sign-in cookies. |
| [PB-052](PRODUCT_BACKLOG.md#pb-052) | Verify the central platform on a tenant | required | 1.8 | P1 | Central business keys are shared data; a defect would corrupt keys across every domain. |
| [PB-009](PRODUCT_BACKLOG.md#pb-009) | Local data retention | required | 1.72 | P1 | Sensitive data protection policy needs bounded local copies. |
| [PB-064](PRODUCT_BACKLOG.md#pb-064) | Per-build release assets, pinning and rollback | required | 1.72 | P1 | Enterprise update control needs rollback. |
| [PB-072](PRODUCT_BACKLOG.md#pb-072) | Long engine calls stop blocking the app | required | 1.48 | P1 | A frozen app with no way out is the most visible reliability failure. |
| [PB-006](PRODUCT_BACKLOG.md#pb-006) | Content Security Policy for the main window | required | 1.47 | P1 | Defense in depth for a webview that renders user and notebook content. |
<!-- END GENERATED: mandatory -->

## Product-owner priority decisions

The product owner can set an item's priority directly. The computed score stays visible so the
trade-off is recorded, and the decision is stored in the item's `priority_override`.

<!-- BEGIN GENERATED: overrides -->
| Id | Item | Computed score | Priority | Decided by | Reason |
| --- | --- | ---: | --- | --- | --- |
| [PB-099](PRODUCT_BACKLOG.md#pb-099) | Local data engine and OneLake publishing program | 2.3 | P0 | Chris (product owner), 2026-10-17 | Local execution without Fabric capacity is the product's core mission; build FD-LDE-001..020 as P0 despite its size |
<!-- END GENERATED: overrides -->

## Sequencing by dependency

An item that a higher-priority item depends on has to be built first, whatever its own score. These
items are sequenced at the priority of the work that needs them:

<!-- BEGIN GENERATED: pulled -->
| Id | Item | Own priority | Sequenced as | Because of |
| --- | --- | --- | --- | --- |
| [PB-035](PRODUCT_BACKLOG.md#pb-035) | Pipeline definition engine | P2 | P1 | [PB-036](PRODUCT_BACKLOG.md#pb-036) |
| [PB-056](PRODUCT_BACKLOG.md#pb-056) | Enterprise policy registry | P2 | P1 | [PB-057](PRODUCT_BACKLOG.md#pb-057) |
| [PB-095](PRODUCT_BACKLOG.md#pb-095) | MCP server and local API for external AI tools | P2 | P1 | [PB-096](PRODUCT_BACKLOG.md#pb-096) |
| [PB-024](PRODUCT_BACKLOG.md#pb-024) | Native Warehouse and SQL database connectivity | P3 | P2 | [PB-025](PRODUCT_BACKLOG.md#pb-025) |
<!-- END GENERATED: pulled -->

## Recommended sequence

This is the reading of the ranking below, not a separate list. Items owned by specialist programs
are sequenced by those programs; the order here is what the master product view recommends.

1. **Launch blockers (P0).** Chris creates the Azure Trusted Signing account so PB-001 can ship, and
   runs the tenant smoke test (PB-002) on his non-production workspace. Neither needs new product
   code beyond the package in [pr-ready/PB-002.md](pr-ready/PB-002.md).
2. **Trust and correctness (mandatory P1, unassigned).** Small, high-confidence fixes that remove
   wrong answers and wrong documents: local-parity defects (PB-013), Preview labels (PB-078),
   security and network docs (PB-010), Rust tests in CI (PB-071) and Fabric client retries
   (PB-051). Each has a PR-ready package.
3. **Enterprise readiness already merged.** Run the implementation review on the merged enterprise
   PRs (audit trail #89, retention #95, backup #91, Ready to work #94), then token encryption
   (PB-005), CSP (PB-006), embedded-view hosts (PB-008) and release pinning (PB-064).
4. **Local-first differentiation (P1, programs).** The Spark API matrix (PB-012) and the AST
   analyzer (PB-014) from Prove, CU labelling (PB-050), preflight risk (PB-090) and the execution
   advisor (PB-017) from FinOps, and the SQL dialect analyzer (PB-022). Together they let a
   developer see, before running, what will work on the PC and what it saves.
5. **Reliability of the app itself.** Long engine calls stop blocking the app (PB-072) after Rust
   tests run in CI.
6. **P2 in score order**, starting with the run dialog (PB-067), capacity health (PB-088), docs
   truth for users (PB-077) and the SQL editor (PB-021).

## Ranking

<!-- BEGIN GENERATED: ranking -->
### P0 (3 items)

| Id | Item | Dev | CU | Ent | Diff | Align | Cplx | Risk | Benefit | Cost | Score | Mandatory | Effort | Confidence | Depends on | Owner |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| [PB-002](PRODUCT_BACKLOG.md#pb-002) | Tenant smoke test of ten core flows | 3 | 2 | 5 | 2 | 5 | 2 | 2 | 3.45 | 2.0 | 2.76 | blocker | M | High | none | Master product (unassigned) |
| [PB-001](PRODUCT_BACKLOG.md#pb-001) | Sign the app and engine | 3 | 1 | 5 | 1 | 5 | 2 | 2 | 3.1 | 2.0 | 2.48 | blocker | S | High | none | Enterprise readiness |
| [PB-099](PRODUCT_BACKLOG.md#pb-099) | Local data engine and OneLake publishing program | 5 | 5 | 4 | 4 | 5 | 4 | 3 | 4.6 | 3.5 | 2.3 | none | XL | Low | none | Local data engine and OneLake publishing |

### P1 (28 items)

| Id | Item | Dev | CU | Ent | Diff | Align | Cplx | Risk | Benefit | Cost | Score | Mandatory | Effort | Confidence | Depends on | Owner |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| [PB-012](PRODUCT_BACKLOG.md#pb-012) | Published Spark API compatibility matrix for this PC | 4 | 4 | 3 | 4 | 5 | 2 | 1 | 3.9 | 1.5 | 3.51 | none | M | High | none | Prove |
| [PB-050](PRODUCT_BACKLOG.md#pb-050) | Label every CU number measured, estimated, scenario or unknown | 3 | 5 | 5 | 4 | 5 | 2 | 2 | 4.35 | 2.0 | 3.48 | none | M | High | none | Administration and FinOps sprint |
| [PB-017](PRODUCT_BACKLOG.md#pb-017) | Local vs Fabric execution advisor | 4 | 5 | 4 | 5 | 5 | 3 | 2 | 4.5 | 2.5 | 3.15 | none | M | Medium | PB-050, PB-014 | Administration and FinOps sprint |
| [PB-081](PRODUCT_BACKLOG.md#pb-081) | Column lineage view | 4 | 1 | 4 | 4 | 4 | 2 | 1 | 3.4 | 1.5 | 3.06 | none | M | High | none | Staged roadmap |
| [PB-090](PRODUCT_BACKLOG.md#pb-090) | Preflight risk analyzer | 4 | 5 | 4 | 4 | 5 | 3 | 2 | 4.35 | 2.5 | 3.04 | none | M | Medium | PB-050 | Administration and FinOps sprint |
| [PB-057](PRODUCT_BACKLOG.md#pb-057) | Offline policy validation | 4 | 2 | 5 | 4 | 5 | 3 | 2 | 4.0 | 2.5 | 2.8 | none | M | Medium | PB-056 | Enterprise governance foundation |
| [PB-036](PRODUCT_BACKLOG.md#pb-036) | Pipeline activity compatibility registry | 3 | 3 | 3 | 4 | 5 | 2 | 2 | 3.45 | 2.0 | 2.76 | none | M | Medium | PB-035 | Local Fabric pipeline execution |
| [PB-086](PRODUCT_BACKLOG.md#pb-086) | Ready to work checklist | 3 | 2 | 4 | 2 | 4 | 2 | 1 | 3.05 | 1.5 | 2.75 | none | S | High | none | Enterprise readiness |
| [PB-096](PRODUCT_BACKLOG.md#pb-096) | Run the AI client checklist for each named tool | 3 | 1 | 3 | 3 | 4 | 1 | 1 | 2.75 | 1.0 | 2.75 | none | S | High | PB-095 | Bring your own AI integration |
| [PB-014](PRODUCT_BACKLOG.md#pb-014) | AST-based compatibility analyzer with an Unknown outcome | 4 | 4 | 3 | 4 | 5 | 3 | 2 | 3.9 | 2.5 | 2.73 | none | M | Medium | PB-013 | Prove |
| [PB-022](PRODUCT_BACKLOG.md#pb-022) | Fabric SQL dialect compatibility analyzer | 4 | 4 | 3 | 4 | 5 | 3 | 2 | 3.9 | 2.5 | 2.73 | none | M | Medium | none | SQL development sprint |
| [PB-098](PRODUCT_BACKLOG.md#pb-098) | One list of engine methods | 2 | 1 | 4 | 2 | 4 | 1 | 1 | 2.6 | 1.0 | 2.6 | none | S | Medium | PB-095 | Bring your own AI integration |
| [PB-013](PRODUCT_BACKLOG.md#pb-013) | Fix local-parity defects that give wrong answers | 4 | 2 | 3 | 2 | 5 | 2 | 2 | 3.2 | 2.0 | 2.56 | required | M | High | none | Master product (unassigned) |
| [PB-071](PRODUCT_BACKLOG.md#pb-071) | Run the Rust tests in CI | 2 | 1 | 4 | 1 | 4 | 1 | 1 | 2.45 | 1.0 | 2.45 | required | S | High | none | Master product (unassigned) |
| [PB-078](PRODUCT_BACKLOG.md#pb-078) | Preview tags match the docs | 2 | 1 | 4 | 1 | 4 | 1 | 1 | 2.45 | 1.0 | 2.45 | required | S | High | none | Master product (unassigned) |
| [PB-063](PRODUCT_BACKLOG.md#pb-063) | Assistive technology audit | 2 | 1 | 4 | 1 | 4 | 2 | 1 | 2.45 | 1.5 | 2.21 | required | M | Medium | none | Enterprise readiness |
| [PB-010](PRODUCT_BACKLOG.md#pb-010) | Security and network docs match the shipped app | 1 | 1 | 4 | 1 | 4 | 1 | 1 | 2.2 | 1.0 | 2.2 | required | S | High | none | Master product (unassigned) |
| [PB-074](PRODUCT_BACKLOG.md#pb-074) | Release pipeline hardening | 1 | 1 | 4 | 1 | 4 | 1 | 1 | 2.2 | 1.0 | 2.2 | required | S | High | none | Enterprise readiness |
| [PB-051](PRODUCT_BACKLOG.md#pb-051) | Fabric REST client resilience | 3 | 1 | 4 | 1 | 4 | 2 | 2 | 2.7 | 2.0 | 2.16 | required | S | High | none | Master product (unassigned) |
| [PB-003](PRODUCT_BACKLOG.md#pb-003) | Prove OneLake leases and MERGE writes live | 1 | 1 | 5 | 2 | 4 | 2 | 2 | 2.6 | 2.0 | 2.08 | required | S | Medium | PB-002 | Enterprise readiness |
| [PB-007](PRODUCT_BACKLOG.md#pb-007) | Audit trail of runs and changes | 1 | 1 | 5 | 2 | 4 | 2 | 2 | 2.6 | 2.0 | 2.08 | required | M | High | none | Enterprise readiness |
| [PB-005](PRODUCT_BACKLOG.md#pb-005) | Encrypt the token cache | 1 | 1 | 5 | 1 | 4 | 2 | 2 | 2.45 | 2.0 | 1.96 | required | S | High | none | Enterprise readiness |
| [PB-008](PRODUCT_BACKLOG.md#pb-008) | Keep the embedded Fabric view on Fabric hosts | 1 | 1 | 4 | 1 | 3 | 1 | 2 | 2.05 | 1.5 | 1.84 | required | S | Medium | PB-071 | Enterprise readiness |
| [PB-052](PRODUCT_BACKLOG.md#pb-052) | Verify the central platform on a tenant | 2 | 1 | 5 | 3 | 4 | 3 | 3 | 3.0 | 3.0 | 1.8 | required | M | Medium | PB-002 | Master product (unassigned) |
| [PB-009](PRODUCT_BACKLOG.md#pb-009) | Local data retention | 1 | 1 | 5 | 1 | 4 | 2 | 3 | 2.45 | 2.5 | 1.72 | required | S | High | none | Enterprise readiness |
| [PB-064](PRODUCT_BACKLOG.md#pb-064) | Per-build release assets, pinning and rollback | 1 | 1 | 5 | 1 | 4 | 3 | 2 | 2.45 | 2.5 | 1.72 | required | M | Medium | none | Enterprise readiness |
| [PB-072](PRODUCT_BACKLOG.md#pb-072) | Long engine calls stop blocking the app | 4 | 1 | 4 | 1 | 4 | 4 | 3 | 2.95 | 3.5 | 1.48 | required | L | Medium | PB-071 | Master product (unassigned) |
| [PB-006](PRODUCT_BACKLOG.md#pb-006) | Content Security Policy for the main window | 1 | 1 | 5 | 1 | 4 | 3 | 3 | 2.45 | 3.0 | 1.47 | required | M | Medium | none | Enterprise readiness |

### P2 (47 items)

| Id | Item | Dev | CU | Ent | Diff | Align | Cplx | Risk | Benefit | Cost | Score | Mandatory | Effort | Confidence | Depends on | Owner |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| [PB-067](PRODUCT_BACKLOG.md#pb-067) | Bounded-memory runs: run dialog and orphan-file cleanup | 4 | 4 | 3 | 3 | 4 | 3 | 2 | 3.6 | 2.5 | 2.52 | none | M | High | none | Staged roadmap |
| [PB-088](PRODUCT_BACKLOG.md#pb-088) | Capacity inventory and health | 2 | 4 | 5 | 3 | 4 | 3 | 2 | 3.6 | 2.5 | 2.52 | none | M | Medium | PB-050 | Administration and FinOps sprint |
| [PB-094](PRODUCT_BACKLOG.md#pb-094) | Measure the process kernel, not the engine | 3 | 3 | 2 | 2 | 4 | 2 | 1 | 2.75 | 1.5 | 2.48 | none | S | Medium | none | Prove |
| [PB-043](PRODUCT_BACKLOG.md#pb-043) | Cancel a Fabric run from the app | 3 | 3 | 2 | 1 | 3 | 1 | 1 | 2.45 | 1.0 | 2.45 | none | S | High | none | Master product (unassigned) |
| [PB-077](PRODUCT_BACKLOG.md#pb-077) | User and agent docs match the code | 3 | 1 | 3 | 1 | 4 | 1 | 1 | 2.45 | 1.0 | 2.45 | none | S | High | none | Master product (unassigned) |
| [PB-021](PRODUCT_BACKLOG.md#pb-021) | SQL editor with IntelliSense and formatting | 5 | 2 | 3 | 3 | 4 | 3 | 2 | 3.45 | 2.5 | 2.42 | none | M | High | none | SQL development sprint |
| [PB-068](PRODUCT_BACKLOG.md#pb-068) | Key lookups without reading whole key tables | 3 | 4 | 4 | 2 | 4 | 3 | 2 | 3.45 | 2.5 | 2.42 | none | M | High | none | Staged roadmap |
| [PB-034](PRODUCT_BACKLOG.md#pb-034) | Start the notebook wizard from a table | 3 | 2 | 2 | 2 | 3 | 1 | 1 | 2.4 | 1.0 | 2.4 | none | S | High | none | Prove |
| [PB-091](PRODUCT_BACKLOG.md#pb-091) | Agent dashboard | 2 | 1 | 5 | 3 | 4 | 2 | 2 | 3.0 | 2.0 | 2.4 | none | M | High | none | Workload management |
| [PB-056](PRODUCT_BACKLOG.md#pb-056) | Enterprise policy registry | 3 | 1 | 5 | 4 | 4 | 3 | 2 | 3.4 | 2.5 | 2.38 | none | M | Medium | none | Enterprise governance foundation |
| [PB-082](PRODUCT_BACKLOG.md#pb-082) | Versioned data contracts with consumer declarations | 3 | 1 | 5 | 4 | 4 | 3 | 2 | 3.4 | 2.5 | 2.38 | none | M | Medium | none | Staged roadmap |
| [PB-040](PRODUCT_BACKLOG.md#pb-040) | Fabric workspace Git integration | 4 | 2 | 5 | 3 | 5 | 3 | 3 | 3.85 | 3.0 | 2.31 | none | L | High | none | Git-native development |
| [PB-044](PRODUCT_BACKLOG.md#pb-044) | Agent queue pruning and dead-job requeue | 2 | 1 | 4 | 1 | 3 | 1 | 1 | 2.3 | 1.0 | 2.3 | none | S | High | none | Master product (unassigned) |
| [PB-062](PRODUCT_BACKLOG.md#pb-062) | Save a support bundle from Settings | 2 | 1 | 4 | 1 | 3 | 1 | 1 | 2.3 | 1.0 | 2.3 | none | S | High | none | Enterprise readiness |
| [PB-025](PRODUCT_BACKLOG.md#pb-025) | SQL object explorer | 4 | 1 | 3 | 1 | 3 | 2 | 1 | 2.55 | 1.5 | 2.29 | none | M | Medium | PB-024 | SQL development sprint |
| [PB-023](PRODUCT_BACKLOG.md#pb-023) | Managed SQL execution with cancel and streaming | 4 | 3 | 3 | 2 | 4 | 3 | 2 | 3.25 | 2.5 | 2.27 | none | M | High | none | SQL development sprint |
| [PB-047](PRODUCT_BACKLOG.md#pb-047) | Incident screens | 3 | 1 | 5 | 3 | 4 | 3 | 2 | 3.25 | 2.5 | 2.27 | none | M | High | none | Workload management |
| [PB-015](PRODUCT_BACKLOG.md#pb-015) | Write semantics for MERGE, UPDATE and DELETE on this PC | 5 | 5 | 3 | 5 | 5 | 4 | 3 | 4.5 | 3.5 | 2.25 | none | L | Medium | PB-012 | Prove |
| [PB-027](PRODUCT_BACKLOG.md#pb-027) | Column completion for attached tables | 4 | 1 | 2 | 2 | 3 | 2 | 1 | 2.45 | 1.5 | 2.21 | none | S | High | none | Prove |
| [PB-049](PRODUCT_BACKLOG.md#pb-049) | Run the pipeline mailbox once on a tenant | 2 | 3 | 4 | 4 | 3 | 2 | 3 | 3.15 | 2.5 | 2.21 | none | S | Medium | PB-002 | Staged roadmap |
| [PB-019](PRODUCT_BACKLOG.md#pb-019) | Late-arriving rows and source deletes in incremental loads | 4 | 3 | 4 | 3 | 4 | 3 | 3 | 3.65 | 3.0 | 2.19 | none | M | Medium | none | Staged roadmap |
| [PB-018](PRODUCT_BACKLOG.md#pb-018) | Publish matches Fabric items by id, not display name | 4 | 1 | 3 | 1 | 4 | 2 | 2 | 2.7 | 2.0 | 2.16 | none | S | Medium | none | Master product (unassigned) |
| [PB-035](PRODUCT_BACKLOG.md#pb-035) | Pipeline definition engine | 4 | 4 | 4 | 5 | 5 | 4 | 3 | 4.3 | 3.5 | 2.15 | none | L | Medium | none | Local Fabric pipeline execution |
| [PB-053](PRODUCT_BACKLOG.md#pb-053) | Scheduled shortcut health check with alerts | 2 | 2 | 4 | 2 | 3 | 2 | 2 | 2.65 | 2.0 | 2.12 | none | S | Medium | none | Master product (unassigned) |
| [PB-054](PRODUCT_BACKLOG.md#pb-054) | Screens for platform revoke and source list | 2 | 1 | 4 | 1 | 3 | 1 | 2 | 2.3 | 1.5 | 2.07 | none | S | High | none | Master product (unassigned) |
| [PB-070](PRODUCT_BACKLOG.md#pb-070) | UI tests for the notebook editor and command bar | 3 | 1 | 3 | 1 | 3 | 2 | 1 | 2.3 | 1.5 | 2.07 | none | M | High | none | Master product (unassigned) |
| [PB-065](PRODUCT_BACKLOG.md#pb-065) | Separation of duties by signed-in identity | 1 | 1 | 4 | 1 | 3 | 1 | 1 | 2.05 | 1.0 | 2.05 | none | S | High | none | Enterprise readiness |
| [PB-089](PRODUCT_BACKLOG.md#pb-089) | Measured CU import and reconciliation | 2 | 5 | 5 | 4 | 5 | 4 | 3 | 4.1 | 3.5 | 2.05 | none | L | Low | PB-050 | Administration and FinOps sprint |
| [PB-031](PRODUCT_BACKLOG.md#pb-031) | Schema evolution without full rewrites | 3 | 3 | 4 | 3 | 4 | 3 | 3 | 3.4 | 3.0 | 2.04 | none | M | Medium | none | Staged roadmap |
| [PB-085](PRODUCT_BACKLOG.md#pb-085) | Declare cell dependencies and mark stale on new table versions | 3 | 2 | 2 | 3 | 3 | 2 | 2 | 2.55 | 2.0 | 2.04 | none | S | Medium | none | Prove |
| [PB-028](PRODUCT_BACKLOG.md#pb-028) | Package install magics that work on this PC | 4 | 2 | 2 | 1 | 3 | 2 | 2 | 2.5 | 2.0 | 2.0 | none | S | Medium | PB-016 | Prove |
| [PB-069](PRODUCT_BACKLOG.md#pb-069) | Compare large tables in batches | 3 | 3 | 3 | 2 | 3 | 3 | 2 | 2.85 | 2.5 | 1.99 | none | M | Medium | none | Staged roadmap |
| [PB-059](PRODUCT_BACKLOG.md#pb-059) | Governance exception requests and approvals | 2 | 1 | 4 | 2 | 3 | 2 | 2 | 2.45 | 2.0 | 1.96 | none | M | Medium | PB-056 | Enterprise governance foundation |
| [PB-061](PRODUCT_BACKLOG.md#pb-061) | Missing policy keys | 1 | 1 | 5 | 1 | 4 | 2 | 2 | 2.45 | 2.0 | 1.96 | none | M | High | none | Enterprise readiness |
| [PB-042](PRODUCT_BACKLOG.md#pb-042) | Release rollback and the committed-revision rule | 3 | 1 | 5 | 3 | 4 | 3 | 3 | 3.25 | 3.0 | 1.95 | none | M | Medium | none | Staged roadmap |
| [PB-095](PRODUCT_BACKLOG.md#pb-095) | MCP server and local API for external AI tools | 4 | 2 | 4 | 5 | 5 | 4 | 3 | 3.9 | 3.5 | 1.95 | none | XL | Medium | none | Bring your own AI integration |
| [PB-041](PRODUCT_BACKLOG.md#pb-041) | Deployment preview and item deploy | 4 | 2 | 5 | 3 | 5 | 4 | 3 | 3.85 | 3.5 | 1.93 | none | L | Medium | PB-040 | Git-native development |
| [PB-016](PRODUCT_BACKLOG.md#pb-016) | Reproducible environments and Fabric Environment items | 4 | 3 | 4 | 4 | 4 | 4 | 3 | 3.8 | 3.5 | 1.9 | none | L | Low | none | Prove |
| [PB-083](PRODUCT_BACKLOG.md#pb-083) | Endorsed items scope in the catalog | 2 | 1 | 3 | 1 | 2 | 1 | 1 | 1.9 | 1.0 | 1.9 | none | S | High | none | Master product (unassigned) |
| [PB-087](PRODUCT_BACKLOG.md#pb-087) | Clean up downloaded preview files | 2 | 1 | 3 | 1 | 2 | 1 | 1 | 1.9 | 1.0 | 1.9 | none | S | Medium | none | Master product (unassigned) |
| [PB-073](PRODUCT_BACKLOG.md#pb-073) | Process kernel by default with limits | 4 | 1 | 4 | 2 | 4 | 3 | 3 | 3.1 | 3.0 | 1.86 | none | M | Medium | none | Prove |
| [PB-079](PRODUCT_BACKLOG.md#pb-079) | Decide the future of the TOML job runner | 2 | 2 | 2 | 1 | 2 | 1 | 1 | 1.85 | 1.0 | 1.85 | none | S | High | none | Master product (unassigned) |
| [PB-048](PRODUCT_BACKLOG.md#pb-048) | Schedule editing and safer schedule removal | 3 | 1 | 3 | 1 | 3 | 2 | 2 | 2.3 | 2.0 | 1.84 | none | M | High | none | Master product (unassigned) |
| [PB-066](PRODUCT_BACKLOG.md#pb-066) | Backup and restore of local state | 2 | 1 | 4 | 1 | 3 | 2 | 2 | 2.3 | 2.0 | 1.84 | none | S | High | none | Enterprise readiness |
| [PB-084](PRODUCT_BACKLOG.md#pb-084) | One late-table alert, not two | 2 | 1 | 3 | 1 | 3 | 2 | 1 | 2.05 | 1.5 | 1.84 | none | S | High | none | Workload management |
| [PB-060](PRODUCT_BACKLOG.md#pb-060) | Sensitive data classification that complements Purview | 2 | 1 | 5 | 3 | 4 | 3 | 3 | 3.0 | 3.0 | 1.8 | none | L | Low | PB-056 | Enterprise readiness |
| [PB-076](PRODUCT_BACKLOG.md#pb-076) | Retire side by side into Prove | 2 | 2 | 3 | 1 | 3 | 2 | 2 | 2.25 | 2.0 | 1.8 | none | S | Medium | none | Prove |

### P3 (10 items)

| Id | Item | Dev | CU | Ent | Diff | Align | Cplx | Risk | Benefit | Cost | Score | Mandatory | Effort | Confidence | Depends on | Owner |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| [PB-092](PRODUCT_BACKLOG.md#pb-092) | Shared workload ownership | 2 | 1 | 4 | 2 | 3 | 3 | 2 | 2.45 | 2.5 | 1.72 | none | M | Medium | none | Workload management |
| [PB-100](PRODUCT_BACKLOG.md#pb-100) | Fuller What's new dialog | 2 | 1 | 2 | 1 | 2 | 1 | 1 | 1.65 | 1.0 | 1.65 | none | S | Medium | none | Fuller What's new dialog |
| [PB-026](PRODUCT_BACKLOG.md#pb-026) | Notebook debugger | 5 | 2 | 2 | 3 | 4 | 4 | 3 | 3.2 | 3.5 | 1.6 | none | L | Medium | PB-073 | Prove |
| [PB-030](PRODUCT_BACKLOG.md#pb-030) | Data pipeline and Copy job scope after decision D2 | 3 | 2 | 4 | 3 | 4 | 4 | 3 | 3.2 | 3.5 | 1.6 | none | L | Low | none | Staged roadmap |
| [PB-024](PRODUCT_BACKLOG.md#pb-024) | Native Warehouse and SQL database connectivity | 4 | 1 | 4 | 2 | 4 | 4 | 3 | 3.1 | 3.5 | 1.55 | none | L | Medium | PB-021 | SQL development sprint |
| [PB-037](PRODUCT_BACKLOG.md#pb-037) | Local DAG execution engine | 4 | 5 | 4 | 5 | 5 | 5 | 4 | 4.5 | 4.5 | 1.35 | none | XL | Low | PB-035, PB-036 | Local Fabric pipeline execution |
| [PB-020](PRODUCT_BACKLOG.md#pb-020) | Local Warehouse simulation | 4 | 4 | 3 | 5 | 5 | 5 | 4 | 4.05 | 4.5 | 1.21 | none | XL | Low | PB-022 | SQL development sprint |
| [PB-097](PRODUCT_BACKLOG.md#pb-097) | Extensions | 3 | 1 | 4 | 4 | 3 | 4 | 4 | 3.0 | 4.0 | 1.2 | none | XL | Low | none | Git-native development |
| [PB-093](PRODUCT_BACKLOG.md#pb-093) | Telemetry decision | 1 | 1 | 3 | 1 | 2 | 2 | 3 | 1.65 | 2.5 | 1.15 | none | M | Low | none | Enterprise readiness |
| [PB-045](PRODUCT_BACKLOG.md#pb-045) | Shared queue for several workers | 2 | 3 | 4 | 3 | 3 | 5 | 4 | 3.0 | 4.5 | 0.9 | none | L | Low | PB-003 | Workload management |
<!-- END GENERATED: ranking -->

## Change log

| Date | Change | By |
| --- | --- | --- |
| 2026-10-17 | First ranking: weights 0.25/0.20/0.25/0.15/0.15, costs 0.5/0.5, P1 at 2.6, P2 at 1.8 | Master product thread |
