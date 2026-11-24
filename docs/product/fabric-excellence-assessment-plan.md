# Fabric Excellence Assessment: plan, gaps, risks and stories

Status: plan (2026-11-17). Built so far: FEA-101 to FEA-104, the pure engine, with the methodology in [excellence-assessment.md](../optimize/excellence-assessment.md) (#2941), and FEA-105 with its first 10 controls and the read-only `excellence_assess` call (#2957). FEA-109 adds the scorecard on Optimize > Assessment > Excellence, in Preview (#2990). FEA-203 (architecture controls, #3010), FEA-301 and FEA-302 (notebook and pipeline controls, #3013), FEA-202 (capacity controls, #3085) and FEA-304 (semantic model controls, #3066) are built too. FEA-302 adds retry, timeout and failure-path controls (#3162), and FEA-305 is built in part with the Git controls (#3165). Owner: Fabric Excellence Assessment thread. Requested by Chris (master
implementation prompt, 2026-11-17). Shared standards: the KratosForge mission prompt and its hard gates in
`KratosDataGod/ai-commons` (`prompts/kratosforge/mission-and-engineering-philosophy.md`).

This plan turns **Optimize > Assessment** from an optimization finder into an expert-guided
assessment and improvement system across ten dimensions. It covers section 1 of the brief:
what exists, what can be reused, what is missing, what could go wrong, and the phased stories
with acceptance criteria. No code ships with this document.

The standing rules apply to every story below: a mockup and Chris's reaction before any new
screen, untested features behind Preview, one PR per feature, the agent review gates, a typed
"yes, run it" and the `kf_tenant_test` workspace for any live Fabric run, no app registrations,
and no invented findings or savings.

## 1. What the assessment is (and is not)

- It is **read-only by default**. Assessing never writes to Fabric or OneLake and never starts a
  Fabric job. Remediation (Phase 4) is a separate, approval-gated step.
- It is **deterministic**. Every result comes from a versioned control applied to stored
  evidence. The same evidence and the same rule versions always give the same score. AI is
  optional and never changes evidence, severity or scores.
- It is **honest about coverage**. A control that could not be checked is *not evaluated*,
  never *passed*. A workspace with little evidence cannot outrank one with a lot.
- Its ratings are **KratosForge product ratings**, not Microsoft certifications or compliance
  attestations.

## 2. What exists today and what to reuse

The app already has most of the evidence readers and several rule engines. The gap is a common
control, evidence and scoring layer above them, not new readers for most dimensions.

| Existing piece | Where | What it gives the assessment | Reuse as |
| --- | --- | --- | --- |
| Optimization engine (OE-001, OE-003, OE-004) | `sidecar/fabric_desktop/opportunities/`, `opportunities_service.py`, RPC `optimization_assess`, `app/ui/optimize-assess.js` | Workspace and capacity inventory, notebook and pipeline definitions (getDefinition), job history, duplicate, repeated-copy and full-refresh analyzers, overlap-aware totals, read budget (1,500 reads, 4 minutes), demonstration fixture | Discovery and evidence collection for Performance and Capacity. Its opportunities become findings of Performance and FinOps controls. Its screen is the one we redesign (no new module). |
| Opportunity record | `opportunities/model.py` | Required fields: evidence, validation, rollback, effort, risk, confidence, next step, rule id | Basis of the finding record. Add severity, dimension, rule version, evidence confidence class, references. |
| Measurement model | `measurement.py`, `scorecard.py`, `calibration.py`, `opportunities/impact.py` | measured / estimated / scenario / unknown, CU never shown as money saved, `realizable: false` | The financial rule in section 3G of the brief. Used as is. |
| Capacity reads and history | `capacity.py`, `capacity_history.py` (`fdcap_`) | Capacity per workspace, SKU, state, region; settings history | Capacity dimension evidence. Utilization stays Unknown unless imported. |
| Usage imports | `usage/` (Capacity Metrics and Cost Management exports) | The only measured CU source | Capacity and FinOps evidence (throttling, background versus interactive, per-item CU) |
| Estate X-ray | `xray.py`, `xray_service.py` | Delta table layout (small files, VACUUM backlog, stale tables), guest and app access, left-over staging lakehouses | Performance (table layout), Security (access), Architecture (left-overs) controls |
| Workspace role review | `access.py` | People and their roles across Dev, Test, Prod; guest, direct-user-in-Prod and app-as-Admin flags; OneLake data access roles | Security controls |
| Governance checks registry | `governance_checks.py`, `best_practice_checkers.py`, `central/checks_service.py` | Registered checkers with rule ids, severity, fix, url, thresholds, and `outcome: unknown` when evidence is missing | Pattern for organization-defined policies (Section 7). Its rule ids become controls through an adapter. |
| Notebook best practices | `notebook_practices.py` (NB rules) | Static notebook rules (parsed, never run) | Data Engineering and Performance controls |
| SQL best practices | `sqlcompat/` practices (SQ rules) | Static T-SQL rules | Performance and Data Engineering controls |
| Semantic model rules | `model_practices.py` (BPA rules needing only the definition), `model_bpa.py` (full BPA in Fabric), `model_directlake.py`, `tmdl.py` | Model design, Direct Lake fallback and guardrails | Semantic Models dimension. `model_bpa` runs a Fabric notebook, so it needs a typed "yes, run it" and is never part of a default assessment. |
| Delta profile | `delta_profile.py` | Delta features Fabric readers misread | Performance and Reliability controls |
| Table maintenance | `maintenance.py`, Lakehouse optimize thread | OPTIMIZE / VACUUM / V-Order status and actions | Remediation actions (Phase 4); "Blocked" capacity hard-stops these |
| Workload health and data health | `workloads/health.py`, `meta/data_health.py` (#2912, not on main yet), `meta/freshness.py`, `workloads/sla.py`, incidents | Failures, lateness, freshness promises, incident history; "unknown" when a source can't be read | Reliability and Operations controls |
| Lineage, drift, depgraph | `meta/lineage.py`, `meta/drift.py`, `depgraph.py` | Declared versus inferred lineage, schema drift, dependencies | Governance (lineage completeness) and the blast radius of a remediation |
| Git and deployment | `fabric_git.py` (`git/connection`, `git/status`), `meta/promote.py`, `meta/release.py` | Whether a workspace is Git-connected, uncommitted changes, promotion records | DevOps dimension |
| Tenant settings history | `tenant_history.py` (`fdten_`, `GET /admin/tenantsettings`) | Publish to web, external sharing and other tenant settings, Fabric admin only | Security controls, *not evaluated* for non-admins |
| Risk check | `risk.py` | pass / warning / blocked / unknown, with evidence | Pattern for "blocked versus warning versus unknown" wording |
| Local placement | `advisor.py`, `proof.py`, `side_by_side.py`, `calibration.py`, `spark_compat.py` | Can this notebook run on this PC, measured local runs, CU avoided from Fabric baselines | Local Development dimension (J) and the "Open a local test" action |
| Optimize central lakehouse | `optimize/` (`fdopt_findings`, `fdopt_recommendations`, `shared.py`) | Shared findings by fingerprint, recommendation status, insert-only history | History and portfolio storage (Phase 5). Owned by the Optimize lakehouse thread. |
| Audit chain and redaction | `audit_chain.py`, `redact.py` | Tamper-evident audit records; secret redaction | Remediation audit trail; evidence redaction before storage |
| Academy | `academy/registry.py`, packs, DP-700 exercises | Exercises with sandboxed grading, lesson ids | Finding-to-lesson mapping (Phase 6) |
| F64 Elimination Planner | Not built. Planned as OE-040 (demand model from imported Capacity Metrics) and OE-041 (consolidation simulator) in [docs/optimize/engine-plan.md](../optimize/engine-plan.md) | | Capacity right-sizing controls read its output when it lands. The assessment does not build its own simulator. |

### What we keep unchanged

- `optimization_assess` and its screen keep working through every phase. The new engine wraps
  the optimization engine; it doesn't replace it. Existing browser tests
  (`app/tests/browser/optimize-assess.test.cjs`) stay green.
- The Opportunities, Cost and savings, and Settings history tabs are not touched by Phase 1.

## 3. Gaps

### 3.1 Engine gaps (Phase 1)

| Gap | Today | Needed |
| --- | --- | --- |
| Common control model | Each engine has its own finding shape (opportunity, X-ray kind, governance rule, health level) | One versioned control: id, version, dimension, severity, applicability, evaluation, requirement class, references, remediation template |
| Outcome states | Findings exist only when something is wrong; a clean area looks the same as an unread one | pass, fail, not applicable, not evaluated (with reason), error. Unsupported analyzers report not evaluated. |
| Evidence model | Evidence is free text with a source | Typed, versioned evidence records with a confidence class (measured, verified configuration, inferred, insufficient), collection time, source API and a content hash |
| Scoring | None (the optimization engine ranks, it doesn't score) | Dimension and overall scores, coverage, assured score, rating, Provisional / Insufficient evidence, Critical Risk flag |
| Profiles | None | Weights, severity overrides and control applicability per profile, versioned |
| Reproducibility | Results are recomputed live | A snapshot of evidence plus rule versions plus profile version that rescoring reproduces exactly |
| Coverage per artifact type | `coverage.not_analyzed_types` lists types | Every Fabric item type gets an explicit state: inspected, not inspected (why), unsupported |

### 3.2 Coverage gaps by dimension

"Without admin" means the signed-in developer's own workspace roles, which is the default the
brief requires. Every API permission below is to be confirmed against Microsoft's REST reference
in story FEA-106 before a control depends on it; until then the control is marked unverified
and doesn't count toward a score.

| Dimension | Evidence available today without admin | Missing | Admin-only or unavailable |
| --- | --- | --- | --- |
| A. Architecture | Items, environments (Dev/Test/Prod config), lakehouses per layer, shortcuts, X-ray left-overs, cross-workspace shortcuts | Naming policy controls, duplicate-data detection (OE-012), domain assignment, retention policy evidence | Domains (`/admin/domains`), tenant-wide workspace inventory (scanner API) |
| B. Performance | Job history, notebook and pipeline definitions, Delta logs, NB and SQ rules, Direct Lake check | Warehouse query insights reads, Spark session startup times, semantic model query metrics | Capacity-wide query telemetry has no public non-admin API |
| C. Security | Workspace role assignments (when the caller's role can list them), OneLake data access roles, guests, apps as Admin | Item-level sharing, connection credential type, RLS presence in models, sensitivity label on items | Tenant settings (Publish to web, external sharing): Fabric admin only. Labels across the tenant: scanner API, admin only |
| D. Governance | Endorsement where the API returns it, descriptions, lineage from metadata projects, freshness promises | Ownership controls, duplicate business definitions (OE-013), certification coverage | Tenant-wide lineage (scanner API) |
| E. Data Engineering | Notebook code (NB rules), pipeline activities (retry, timeout settings), schema contracts and drift in metadata projects, Git connection | Pipeline retry/timeout controls, idempotency heuristics, parameterization controls | None |
| F. Semantic Models | Model definitions (getDefinition / TMDL), definition-only BPA rules, Direct Lake guardrails, refresh history | Full BPA needs VertiPaq statistics from a Fabric notebook: typed "yes, run it" per run | Model query metrics: no non-admin API |
| G. Capacity and FinOps | Capacity SKU and state, imported Capacity Metrics and Cost Management exports, job run time | Throttling and background/interactive controls from imports, chargeback by workspace, F64 planner output | Live utilization: no public per-capacity metrics API; always Unknown unless imported |
| H. Reliability | Job failures, data health, freshness, incidents | Alert coverage controls, runbook presence, retry presence | Backup and DR settings are mostly not exposed by API: these controls are manual attestations or not evaluated |
| I. DevOps | Git connection and status, deployment pipelines (Fabric REST, not read yet), promotion records | Deployment pipeline reads, drift between stages | None for workspace members |
| J. Local Development | Placement advisor, compatibility check, measured local runs, Fabric baselines (calibration) | Workspace-wide placement classes (OE-030), transfer-cost model (OE-031) | None |

### 3.3 UX gaps

The Assessment tab today shows opportunities for one workspace or capacity. Missing: overall and
dimension scorecard, coverage, Critical Risk banner, highest-value next actions across
dimensions, findings explorer filters, remediation workspace, history and trends, enterprise
comparison. Every one of these is new UI and starts with a mockup (FEA-108, FEA-208, FEA-308,
FEA-408, FEA-508).

## 4. Proposed architecture

A new pure package, `sidecar/fabric_desktop/excellence/`, owned by this thread. It never calls
Fabric itself: services read evidence and hand it in, as `opportunities/` does today.

```
Discovery           existing readers (opportunities/inventory, capacity, xray, access, fabric_git ...)
   |
Evidence            excellence/evidence.py     typed records, confidence class, hash, schema version
   |
Normalization       excellence/adapters/       one adapter per existing engine -> evidence records
   |
Rule engine         excellence/controls.py     versioned controls, applicability, outcome
   |
Scoring             excellence/scoring.py      profiles, dimension and overall scores, coverage, rating
   |
Recommendation      excellence/findings.py     finding record (brief section 4), remediation template
   |
Remediation plan    Phase 4                    approval, blast radius (depgraph, lineage), rollback
   |
Validation          Phase 4/6                  reassess one control, before/after measurement
   |
History             Phase 5                    snapshots in fdopt_ tables through optimize/shared.py
```

### 4.1 Control outcome rules

- **pass**: the evidence the control needs is present and meets the rule.
- **fail**: the evidence is present and does not meet the rule. Produces a finding.
- **not applicable**: the control's applicability condition is false (no semantic models in the
  workspace, for example). Excluded from scoring.
- **not evaluated**: the evidence is missing, unreadable, insufficient, or the analyzer for that
  item type doesn't exist yet. Carries a reason and what access or evidence would let it run.
- **error**: the control raised. Treated as not evaluated for scoring and reported as an engine
  defect.

A control that has no evaluator for an item type can only return *not evaluated*. There is no
default pass.

### 4.2 Evidence confidence

| Class | Meaning | Counts toward the score |
| --- | --- | --- |
| measured | Observed by a source that recorded it: job history, an imported Capacity Metrics export, a local run | Yes |
| verified configuration | Read from a definition or configuration API | Yes |
| inferred | Derived by a heuristic (name patterns, code patterns that usually mean something) | No by default. Shown as advisory. A control may opt in, and its documentation says why. |
| insufficient | Not enough to decide | No: the control is not evaluated |

## 5. Scoring methodology (proposed defaults for Phase 1)

These defaults are implemented and tested in FEA-103 and published as user documentation in
FEA-107. Every number is configurable per profile and versioned.

**Control weight** by severity: critical 10, high 5, medium 3, low 1, informational 0.

For each dimension, over controls that are not *not applicable*:

- **Dimension score** = 100 x (weight of passed controls) / (weight of evaluated controls).
- **Coverage** = (weight of evaluated controls) / (weight of applicable controls). A dimension where
  nothing could be evaluated has no score and shows Insufficient evidence.
- **Assured score** = 100 x (weight of passed controls) / (weight of applicable controls).
  Not-evaluated controls count as not passed here, so missing evidence can never raise it.

**Overall** scores are the dimension scores weighted by the profile's dimension weights, with the
weights of dimensions that have no applicable controls removed and the rest re-normalized. The
default weights are the brief's (Security 18, Performance 15, Architecture 12, Governance 10,
Data Engineering 10, Semantic Models 10, Capacity and FinOps 10, Reliability 7, DevOps 5, Local
Development 3).

**Rating** comes from the score bands (Exemplary 90-100, Well Architected 80-89, Needs Attention
70-79, At Risk 50-69, Critical Improvement Needed 0-49) only when coverage is at least 80%.
Between 50% and 80% the rating is shown as **Provisional**. Below 50% no rating is shown:
**Insufficient evidence**.

**Rankings and comparisons always use the assured score**, so a workspace with 20% coverage
cannot rank above one with 95% coverage because fewer problems were found. Both numbers are shown.

**Critical Risk** is a separate flag, set by any failed critical control regardless of the score.

**Confidence** of an assessment needs both an evidence share and coverage: High when at least 80% of
evaluated weight rests on measured or verified-configuration evidence *and* coverage is at least 80%;
Medium when both are at least 50%; else Low.

**No perfect score without evidence**: a score of 100 is shown only when coverage is 100%;
otherwise it is capped at 99 and labelled with its coverage.

## 6. Risks

| # | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | False passes: missing evidence read as a healthy result | High without design | Severe (trust) | Outcome states (4.1), assured score, coverage gating, adversarial tests in FEA-103 and FEA-104 |
| R2 | Many valuable controls need Fabric admin (tenant settings, scanner API) | Certain | Medium | Default profile works without admin; admin-only controls are *not evaluated* with the reason; never require admin |
| R3 | No public capacity utilization API | Certain | Medium | Capacity controls read imported Capacity Metrics exports only; everything else is Unknown |
| R4 | Rules drift from Microsoft guidance, or links rot | Medium | Medium | Each control cites its reference and the date it was checked; FEA-106 keeps the capability matrix; a docs link check in tests |
| R5 | Score gaming (dismissing findings to raise scores) | Medium | Medium | Accepted risk and exceptions never turn a fail into a pass; they're shown separately with owner and expiry; Academy rewards only validated fixes |
| R6 | Prompt injection through resource names, notebook comments or imported metadata | Medium | High | Evidence is data: never executed, never passed as instructions; AI is optional and can't change evidence or scores; rendered as text, never HTML |
| R7 | Secrets in collected evidence (connection strings in notebooks) | Medium | High | `redact.secrets` before storage; evidence stores hashes and cited lines, not whole code; Security review on every evidence adapter |
| R8 | Rate limits and slow reads across hundreds of workspaces | High at scale | Medium | Reuse the optimization engine's read budget, bounded concurrency, paging, retry with backoff; partial results marked not read |
| R9 | Duplicate findings across engines (X-ray and optimization both see a table) | Medium | Low | Findings keyed by control id plus resource fingerprint; adapters own disjoint controls |
| R10 | Ownership overlap with other threads (Optimization engine owns `opportunities/`, Optimize lakehouse owns `optimize/shared.py`, BPA, Workload mgmt) | High | Medium | New code lives in `excellence/`; other engines are read through adapters; changes to their files go through their owners |
| R11 | Overwhelming UI | Medium | Medium | Mockups first; overview shows score, coverage, critical risks and 3-5 next actions; details on drill-in |
| R12 | Local execution recommended where Fabric behavior differs | Medium | High | Placement controls use compatibility checks and measured baselines only; every recommendation states limits, data movement and validation |
| R13 | Financial claims from freed CU | Medium | Severe | The measurement model's five separate figures; `realizable` false unless billing or capacity change evidence exists |
| R14 | Naming: "Microsoft MVP" is a Microsoft program name | Low | Medium | Keep "MVP in a Box" out of the product UI and docs; ratings say "KratosForge rating" |
| R15 | CI starvation slows delivery | Current | Medium | Small PRs, at most 2 open at once from this thread, no refresh pushes |

## 7. Phased stories

Ids are FEA-*phase*-*nn*. Every story is one PR unless it says otherwise. "Tests" lists the
minimum. Every engine story is pure and runs without Fabric; live checks against
`kf_tenant_test` are separate and need a typed "yes, run it".

### Phase 1: Assessment foundation (engine work, can start now)

**FEA-101 Evidence model.** `excellence/evidence.py`: evidence record (id, kind, subject
resource, source API or file, collected_at, confidence class, payload, content hash, schema
version) and validation that refuses a record without a source or confidence.
- Acceptance: records are JSON-serializable and hash-stable; an unknown confidence class or
  missing source raises; secrets passed in a payload are redacted before hashing.
- Tests: unit tests for each refusal; hash stability across key order.

**FEA-102 Control model and registry.** `excellence/controls.py`: control (id, version,
dimension, title, severity, requirement class: Microsoft requirement, Microsoft recommendation,
KratosForge recommendation or customer policy, applies-to item types, applicability, evaluate,
references, remediation template, academy link). Registry with duplicate-id refusal. Outcomes
pass, fail, not applicable, not evaluated (reason required), error.
- Acceptance: a control with no evaluator for an item type returns not evaluated; a raising
  control yields error, not a crash; a fail without evidence ids is refused.
- Tests: each outcome; registry refusals; a deliberately broken control.

**FEA-103 Scoring engine and profiles.** `excellence/scoring.py` with section 5's formulas,
the five default profiles (Baseline, Enterprise security, Data engineering
excellence, Capacity optimization, KratosForge local-first) as versioned data, re-normalized
weights for not-applicable dimensions, rating bands, Provisional and Insufficient evidence,
Critical Risk flag, 99 cap.
- Acceptance: identical input gives identical output; 20%-coverage input never has a higher
  assured score than the same estate at 95% coverage; a failed critical control sets Critical
  Risk at any score; weights must sum to 100 or the profile is refused.
- Tests: table-driven scoring cases, property tests on coverage monotonicity, mutation tests to
  the repo's coverage baseline.

**FEA-104 Assessment run and snapshot.** `excellence/assess.py`: run the applicable controls on
an evidence bundle with a profile, produce the snapshot (scope, timestamp, policy version, rule
versions, evidence hashes, outcomes, findings, scores, counts by outcome, critical and high
findings, confidence). `rescore(snapshot, profile)` reproduces it from stored evidence; `compare`
gives the trend since a previous snapshot.
- Acceptance: rescoring a stored snapshot gives byte-identical scores; a fixture run is labelled as
  not a finding; tampered evidence is rejected.
- Tests: snapshot round trip; fixture labelling; tampering.

**FEA-105 First adapters (no new Fabric reads).** Adapters from the optimization engine result,
X-ray result, workspace role review and governance checks into evidence and about 15 controls
across Performance, Security, Architecture and Capacity. Each control cites its reference.
Adds the read-only RPC `excellence_assess` with a labelled demonstration fixture.
- Acceptance: every control has a pass case, a fail case and a not-evaluated case in fixtures;
  an unreadable role list makes Security role controls not evaluated, never pass; the RPC writes
  nothing; the authorization matrix is regenerated; Security read (RPC, evidence redaction).
- Tests: one fixture trio per control; an adversarial fixture with injected text in names and
  notebook comments that must appear only as data.

**FEA-106 Fabric API capability matrix.** `docs/optimize/excellence-api-matrix.md` plus a
machine-readable copy: each endpoint the assessment reads, the scope, the workspace or admin
role it needs (checked against Microsoft's REST reference, with the date), paging and throttling
behavior, and which controls depend on it.
- Acceptance: no control is enabled in a default profile unless its endpoints are in the matrix
  as checked; admin-only endpoints are marked and their controls default to not evaluated.

**FEA-107 Methodology and limitations doc.** User documentation of the scoring model, outcome
states, evidence classes, profiles, rating meaning (product ratings, not certifications) and
known limitations.
- Acceptance: every formula in FEA-103 is documented with a worked example that the tests also
  run.

**FEA-108 Scorecard mockup.** Mockup of the redesigned Assessment overview (scope, overall
score, assured score, coverage, Critical Risk, ten-dimension scorecard with drill-in, 3-5 next
actions). Light shell, 40-60% whitespace. Via the UX thread; Chris reacts before build.

**FEA-109 Scorecard screen (Preview).** **Built** (#2990, Preview). Build FEA-108 into Optimize > Assessment behind Preview,
keeping the existing opportunity view reachable.
- Acceptance: existing `optimize-assess` browser tests pass; new browser tests for Provisional,
  Insufficient evidence and Critical Risk states; keyboard and screen-reader checks; narrow
  window layout.

Phase 1 outcome: one workspace can be assessed without admin, with a reproducible score,
coverage and Critical Risk flag built from evidence the app already reads.

### Phase 2: High-value controls

- **FEA-201 Security controls**: Admin count per workspace, direct users versus groups in Prod,
  guests, apps as Admin, Prod writers without Dev access, OneLake data access roles present when
  OneLake security is on. Tenant settings controls (Publish to web, external sharing) when the
  signed-in person is a Fabric admin; otherwise not evaluated.
- **FEA-202 Capacity and FinOps controls**: **Built** in part (#3085): the failed-operation CU share, refreshes more than hourly and unused paid F capacity, from imported Capacity Metrics exports. Capacity state, region alignment, throttling and
  background/interactive share from imported Capacity Metrics, idle capacity windows, repeated
  refreshes; the five financial figures kept separate; F64 planner output read when OE-040/041
  land.
- **FEA-203 Architecture controls**: **Built** in part (#3010): one lakehouse per layer, schema-enabled lakehouses, leftovers in production and a cross-workspace default lakehouse; Dev/Test/Prod separation, the naming policy and shortcut opportunities aren't checked yet. Dev/Test/Prod separation from environment config, one
  lakehouse per medallion layer, naming policy (customer pack), cross-workspace dependency count,
  left-over staging lakehouses, shortcut opportunities (with OE-012).
- **FEA-204 Performance controls**: small files, VACUUM backlog, V-Order on tables Direct Lake
  reads, full refreshes, duplicates, Delta profile features Fabric misreads.
- **FEA-205 Live check in `kf_tenant_test`**: run the Phase 1 and 2 assessment against the test
  workspace. Needs Chris's typed "yes, run it".
- **FEA-208 Findings explorer and finding detail mockup**, then **FEA-209** build (Preview).
- Acceptance for each control story: pass, fail and not-evaluated fixtures; reference cited;
  severity justified in the control's docstring; Security read for FEA-201.

### Phase 3: Artifact deep inspection

- **FEA-301 Notebooks**: **Built** in part (#3013): NB001, NB002, NB009, NB012 and NB020 as controls, not yet weighted by run time. NB rules as controls (error handling, parameterization, hard-coded
  paths, secrets, overwrite without replaceWhere), weighted by measured run time.
- **FEA-302 Pipelines**: **Built** in part (#3013, #3162): deactivated activities, hard-coded workspace ids, and retry, timeout and failure paths (FEA-REL-004 to FEA-REL-006); parameterization and overlapping schedules aren't checked yet. retry and timeout settings, failure paths, parameterization, overlapping
  schedules (OE-011).
- **FEA-303 Lakehouses and Warehouses**: schema-enabled, table maintenance, SQ rules on stored
  procedures and views, warehouse query insights where enabled.
- **FEA-304 Semantic models**: **Built** in part (#3066): bidirectional relationships, RLS presence, Direct Lake fallback, and BPA rules SM001, SM002 and SM004 from stored results. Definition-only BPA rules, Direct Lake fallback and guardrails,
  bidirectional filters, high-cardinality columns, RLS presence. Full BPA (VertiPaq) only as an
  explicit action with a typed "yes, run it".
- **FEA-305 DevOps**: **Built** in part (#3165): Git connection and changes on either side (FEA-DEVOPS-003 to FEA-DEVOPS-005); deployment pipeline stages and drift aren't checked yet. Git connection, uncommitted changes, deployment pipeline stages and drift.
- **FEA-306 Coverage per item type**: every Fabric item type the workspace holds gets inspected,
  not inspected (why) or unsupported in the result and on screen.
- **FEA-308 / FEA-309** artifact drill-in mockup and build.

### Phase 4: Remediation

- **FEA-401 Remediation plan builder**: permissions check, affected resources, blast radius from
  depgraph and lineage, dependencies, proposed change, rollback, validation steps.
- **FEA-402 Templates**: SQL, Python, notebook and TMDL change templates per control, generated
  deterministically and shown as proposals.
- **FEA-403 Local validation**: "Open a local test" through side by side and proof, with output
  comparison.
- **FEA-404 Accepted risk and exceptions**: owner, reason, expiry; never changes the outcome.
- **FEA-405 Approval-gated execution** through existing confirmed paths only (table maintenance,
  role changes), `confirmDialog` danger for destructive steps, typed name for Prod, audit chain,
  reassess the control after. Security read required. Live use needs a typed "yes, run it".
- **FEA-408 / FEA-409** remediation workspace mockup and build.

### Phase 5: Enterprise portfolio

- **FEA-501 Snapshots and history** in the central lakehouse through `optimize/shared.py`
  (with the Optimize lakehouse thread): new, resolved, recurring findings; score trends.
- **FEA-502 Workspace rankings** by assured score within workload categories.
- **FEA-503 Capacity scores** from capacity-relevant controls, not averages of workspace scores.
- **FEA-504 Policy packs**: versioned, auditable customer policies (naming, patterns,
  severity overrides, applicability, weights) built on the governance checks registry.
- **FEA-505 Scheduled assessments** on this PC through local schedules. Polling only, said so;
  no claim of event-driven monitoring.
- **FEA-506 Change-based reassessment** where a change can be detected by comparing reads (new
  item, changed definition hash, new role assignment, capacity reassignment).
- **FEA-508 / FEA-509** portfolio mockup and build.

### Phase 6: Continuous improvement

- **FEA-601 Academy mapping**: each control links to a lesson or exercise; progress only for
  validated fixes or passed exercises, never for dismissals.
- **FEA-602 Remediation verification**: before and after measurements; realized savings only
  with billing or capacity change evidence.
- **FEA-603 Trend analysis and regressions** across snapshots.
- **FEA-604 Optimization planning**: **Built** (engine only): `excellence/handoff.py`; planner inputs
  aren't consumed until OE-040/041 land. Hand approved findings to the optimization engine and the
  F64 planner without double counting.

## 8. Mapping to the brief's acceptance criteria

| Brief criterion | Delivered by |
| --- | --- |
| 1 Assess a workspace without tenant admin | FEA-104, FEA-105, FEA-106 |
| 2 Capacity information where allowed | FEA-202, FEA-503 |
| 3 Inspected, uninspected, unsupported | FEA-102, FEA-306 |
| 4 Every score explainable | FEA-103, FEA-104, FEA-107 |
| 5 Missing evidence can't pass | FEA-102, FEA-103 |
| 6 Critical risks prominent | FEA-103, FEA-109 |
| 7 Specific remediation | FEA-105 onward, FEA-401, FEA-402 |
| 8 Official documentation links | FEA-102 (references), FEA-106 |
| 9 Rules independently testable | FEA-102, every control story |
| 10 API failures handled | FEA-104, FEA-106, existing read budget |
| 11 Reproducible scoring | FEA-104 |
| 12 Compare over time | FEA-501, FEA-603 |
| 13 Safe, approval-driven remediation | FEA-401, FEA-405 |
| 14 Capacity savings not realized savings | FEA-202, FEA-602 (measurement model) |
| 15 Local execution considerations | FEA-403, Dimension J controls |
| 16 Developers and administrators | FEA-109, FEA-509 |
| 17 Existing Optimize keeps working | Section 2 "What we keep unchanged" |
| 18 No mandatory LLM | Section 1; no story adds one |
| 19 Successful, failing, incomplete, adversarial tests | FEA-105 fixtures, FEA-103 property tests |
| 20 Limitations documented | FEA-107 |

## 9. Dependencies on other threads

- **Optimization engine**: owns `opportunities/`. The assessment reads its result through an
  adapter. OE-012 (duplicate data), OE-013 (duplicate measures), OE-030/031 (placement), OE-040/041
  (F64 planner) feed later controls.
- **Optimize lakehouse**: owns `optimize/shared.py`; FEA-501 needs its agreement on table shape.
- **BPA**: owns `model_bpa.py`; FEA-304 reads its stored results only.
- **Workload mgmt**: owns health, data health and incidents; Reliability controls read them.
- **Governance**: owns the checks registry; FEA-504 builds on it.
- **UX**: reviews every mockup (FEA-108, 208, 308, 408, 508).
- **Academy**: FEA-601 lesson mapping.
- **Security QC**: reads FEA-104 (RPC), FEA-105 (evidence redaction), FEA-201, FEA-405.

## 10. Open questions

None block Phase 1. These come up later and will be batched into a card roundup:

- Phase 2: whether Fabric admins should see tenant-setting controls by default, or only in the
  Enterprise security profile.
- Phase 5: whether snapshots go to the existing central lakehouse (`FabricDesktop_Central`) or
  stay on this PC until an organization opts in.
