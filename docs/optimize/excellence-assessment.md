# Fabric Excellence Assessment: methodology (engine, Phase 1)

The Fabric Excellence Assessment reviews a workspace or a capacity against versioned **controls** in
ten dimensions and gives each a score you can trace back to the evidence behind it. This page explains
how the engine decides and scores, and the screen that shows it. The plan and phased stories are in
the [assessment plan](../product/fabric-excellence-assessment-plan.md).

The engine is in `sidecar/fabric_desktop/excellence/`. It's pure: it reads nothing from Fabric and
writes nothing. The services that collect evidence use the readers the app already has.

Ratings are **KratosForge product ratings**. They are not Microsoft certifications and they don't
attest compliance with any standard.

## Evidence

Every control decides from evidence records. Each record says what kind of evidence it is, which
resource it's about (a workspace, a capacity or an item), the API or file it came from, when it was
collected, and how sure it is:

| Confidence | Meaning | Counts toward the score |
| --- | --- | --- |
| Measured | Observed by a source that recorded it: Fabric job history, an imported Capacity Metrics export, a run on this PC | Yes |
| Verified configuration | Read from a definition or configuration API | Yes |
| Inferred | Worked out by a heuristic | No, unless the control says why it may. Shown as advice. |
| Insufficient | Something was read, but not enough to decide | No. The control isn't evaluated. |

Secrets are removed from evidence before it is stored. Each record carries a hash of its content, so a
record that was changed after collection is rejected and can't decide a control.

## Control outcomes

| Outcome | When | Scored |
| --- | --- | --- |
| Pass | The evidence is there and meets the rule | Yes |
| Fail | The evidence is there and doesn't meet the rule. A finding is created. | Yes |
| Not applicable | The rule doesn't apply here, or your profile turned it off with a reason | Left out |
| Not evaluated | The evidence is missing, unreadable or too weak, or no analyzer covers this kind of item yet | Counts against coverage and the assured score |
| Error | The control itself broke | As not evaluated, and reported as a defect |

A pass or a fail must cite the evidence it rests on. A control can't pass something it never looked
at: missing evidence always gives **not evaluated**. Every item in a workspace is reported as
inspected, not inspected (with the reason) or unsupported (no control covers that kind of item yet).

## How scores are worked out

Each control has one weight, from its severity:

| Severity | Weight |
| --- | ---: |
| Critical | 10 |
| High | 5 |
| Medium | 3 |
| Low | 1 |
| Informational | 0 |

A control that checks items (every notebook, for example) splits its weight across the items it
checked, so a workspace with many notebooks doesn't drown out its other controls.

For each dimension, over the controls that apply:

- **Score** = 100 x passed weight / evaluated weight. How good is what was checked.
- **Coverage** = evaluated weight / applicable weight. How much of it was checked.
- **Assured score** = 100 x passed weight / applicable weight. Controls that couldn't be checked count
  as not passed, so missing evidence can never raise it.

**Rankings and comparisons use the assured score.** A workspace where only 20% could be checked can't
rank above one checked at 95% just because fewer problems were found.

A dimension whose only applicable controls are informational (weight 0) has nothing to score, so it
shows as not applicable; its informational findings are still listed.

The **overall** score weights the dimension scores by the profile's dimension weights. A dimension with
nothing that applies is left out and the other weights are scaled back up to 100.

**Rating**, from the score:

| Score | Rating |
| --- | --- |
| 90-100 | Exemplary |
| 80-89 | Well architected |
| 70-79 | Needs attention |
| 50-69 | At risk |
| 0-49 | Critical improvement needed |

A rating is shown as is only when coverage is at least 80%. From 50% to 80% it is **Provisional**.
Below 50%, or when nothing in a dimension could be evaluated, no rating is shown: **Insufficient
evidence**. A score of 100 needs 100% coverage; otherwise it's shown as 99.

**Critical Risk** is a separate flag. Any failed critical control sets it, whatever the score.

**Confidence** of an assessment needs both of these:

| Confidence | Share of evaluated weight on measured or verified-configuration evidence | Coverage |
| --- | --- | --- |
| High | at least 80% | at least the rated coverage (80%) |
| Medium | at least 50% | at least the provisional coverage (50%) |
| Low | anything else | |

### Worked example

A workspace has four security controls, all High (weight 5): one passes, one fails, one couldn't be
checked because the role list couldn't be read, and one doesn't apply.

- Applicable weight 15, evaluated 10, passed 5.
- Score 50, coverage 67%, assured score 33.
- Rating At risk, shown as **Provisional** because coverage is under 80%.

The engine's tests run this example (`test_dimension_score_coverage_and_assured`).

## Profiles

A profile sets the dimension weights (they add up to 100), the severity weights, the coverage needed
for a rating, severity overrides for single controls, and controls it turns off (each with a reason;
they are then not applicable, never passed). Profiles are versioned and stored with each snapshot.

| Profile | Arch. | Perf. | Security | Gov. | Data eng. | Models | Capacity | Reliab. | DevOps | Local |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline (default) | 12 | 15 | 18 | 10 | 10 | 10 | 10 | 7 | 5 | 3 |
| Enterprise security | 8 | 5 | 35 | 20 | 5 | 5 | 5 | 7 | 8 | 2 |
| Data engineering excellence | 10 | 18 | 10 | 8 | 24 | 8 | 5 | 10 | 5 | 2 |
| Capacity optimization | 8 | 25 | 10 | 5 | 10 | 10 | 22 | 5 | 2 | 3 |
| KratosForge local-first | 10 | 15 | 15 | 8 | 12 | 8 | 10 | 5 | 5 | 12 |

These are KratosForge defaults, not Microsoft standards. The baseline uses the weights from the product brief and the applicable Microsoft guidance.

## Snapshots, rescoring and trends

Each assessment is a snapshot: the evidence it used, every control's outcome on every resource, the
findings, the scores, and the exact rule and profile versions. Assessing a stored snapshot again gives
the same result. If a rule has a new version since, rescoring is refused rather than mixing versions.
Comparing two snapshots gives score changes and new, resolved and recurring findings; when the two
used a different profile or rules, the comparison says so, because the change may come from the policy
rather than the environment.

## Findings

A failed control gives a finding with: title, affected resource, severity, dimension, what was
discovered, why it matters, the recommended solution and steps, the expected benefit (qualified, never
a promised saving), effort, risk of change, how to validate, how to roll back, references, evidence
confidence and the exact rule version. Findings are ranked by severity first, so a critical security
finding is never listed below a minor one with a bigger estimate.

## The engine call

`excellence_assess(workspace_id, days, profile, demo)` assesses one workspace, and its capacity when you
can see it. It reads only, with your own access, and makes no read the app didn't already make: the
optimization engine's reads (the workspace, its items, notebook and pipeline definitions, job history),
the workspace's role assignments (`GET /workspaces/{id}/roleAssignments`), the capacities you can see
(`GET /capacities`), each lakehouse's properties (`GET /workspaces/{id}/lakehouses/{id}`, the read the
app already makes before it writes a table; at most 50 per assessment), and the definition of each
semantic model in the workspace, at most 30, as TMSL (`getDefinition?format=TMSL`, the read the
Semantic model tab already makes). It also uses the Capacity Metrics exports already imported on this
PC (Optimize > Cost and savings). That is a local read, not a Fabric call. A read that fails is listed
in `reads` and leaves its controls not evaluated.
`demo=True` assesses a labelled demonstration fixture without calling Fabric.

## Controls

| Control | Dimension | Severity | Checks | Evidence |
| --- | --- | --- | --- | --- |
| FEA-SEC-001 | Security | Medium | At most 4 Admin assignments | Role assignments |
| FEA-SEC-002 | Security | High | No guest account from another organization is Admin, Member or Contributor | Role assignments |
| FEA-SEC-003 | Security | Medium | No app (service principal) is Admin | Role assignments |
| FEA-SEC-004 | Security | Low | At most 5 people have a write role through their own account rather than a group | Role assignments |
| FEA-GOV-001 | Governance | Low | The workspace has a description | Workspace settings |
| FEA-CAP-001 | Capacity and FinOps | Medium | The workspace's capacity is Active (only a capacity you can see) | Capacities |
| FEA-CAP-002 | Capacity and FinOps | Medium | Operations with status Failure used under 10% of the imported operations' CU | Capacities, imported Capacity Metrics export (Timepoint operations, with Status) |
| FEA-CAP-003 | Capacity and FinOps | Low | No item's refresh operations ran more than 24 times on one day (a pass needs at least a day of operations) | Capacities, imported Capacity Metrics export (Timepoint operations, with Operation) |
| FEA-CAP-004 | Capacity and FinOps | Medium | A running F capacity's items used at least 10% of its size over at least 7 days. Doesn't apply to trial, A or P SKUs or a capacity that isn't running | Capacities, imported Capacity Metrics export (Compute page items) |
| FEA-PERF-001 | Performance | Medium | A notebook doesn't run the same code on the same data as another (OE-DUP-NB) | Optimization engine |
| FEA-PERF-002 | Performance | Medium | A pipeline doesn't copy a source another pipeline also copies (OE-DUP-COPY, OE-REPEAT-EXTRACT) | Optimization engine |
| FEA-DE-001 | Data engineering | Medium | A scheduled notebook or pipeline doesn't rewrite whole tables (OE-FULL-REFRESH); not applicable when it isn't scheduled | Optimization engine |
| FEA-DE-002 | Data engineering | Low | A notebook's code isn't copied into another one per lakehouse (OE-DUP-NB-PARAM) | Optimization engine |
| FEA-SEC-005 | Security | High | No key, password or connection string is written in a notebook's code (NB001) | Definition inspection |
| FEA-DEVOPS-001 | DevOps | Low | No OneLake path in a notebook names a workspace or item id (NB002) | Definition inspection |
| FEA-REL-001 | Reliability | Medium | A notebook doesn't catch an error and ignore it (NB012) | Definition inspection |
| FEA-REL-002 | Reliability | Low | Every package a notebook installs has a version (NB009) | Definition inspection |
| FEA-DE-003 | Data engineering | Medium | No VACUUM in a notebook keeps less than 7 days of history (NB020) | Definition inspection |
| FEA-REL-003 | Reliability | Low | No pipeline activity is deactivated (skipped, and reported as Succeeded by default) | Definition inspection |
| FEA-DEVOPS-002 | DevOps | Low | No pipeline activity names a workspace id as text rather than a parameter or variable | Definition inspection |
| FEA-REL-004 | Reliability | Medium | Every pipeline activity that calls out (Copy, notebook, Spark job, Lookup, Get Metadata, Script, stored procedure, Web, Azure Function, Dataflow, semantic model refresh, KQL) has a retry above 0; not applicable when none calls out | Definition inspection |
| FEA-REL-005 | Reliability | Medium | Every pipeline activity that calls out has a timeout under the 12-hour default (0.12:00:00); not applicable when none calls out | Definition inspection |
| FEA-REL-006 | Reliability | Low | A pipeline with an activity that calls out has a failure path: some activity runs on another's Failed or Completed condition | Definition inspection |
| FEA-ARCH-001 | Architecture | Medium | One lakehouse per medallion layer: no lakehouse named for two of bronze, silver and gold, and no two named for the same layer | Item names (inferred, advice only) |
| FEA-ARCH-002 | Architecture | Low | A lakehouse is schema-enabled | Lakehouse properties |
| FEA-ARCH-003 | Architecture | Low | A workspace whose name says production holds no item named tmp, temp, test, staging, copy, old, backup or similar; not applicable when the name says Dev or Test | Workspace and item names (inferred, advice only) |
| FEA-ARCH-004 | Architecture | Medium | A notebook's default lakehouse is in its own workspace (a cross-workspace dependency otherwise); not applicable without a default lakehouse | Notebook definition and item list |
| FEA-SM-001 | Semantic models | Medium | No active relationship filters in both directions | Model definition |
| FEA-SM-002 | Semantic models | Informational | At least one security role filters rows (noted, not scored, because many models are meant for every reader) | Model definition |
| FEA-SM-003 | Semantic models | Low | A Direct Lake model is set to Direct Lake only, or reads through OneLake, so it doesn't fall back to DirectQuery silently; not applicable without Direct Lake tables | Model definition |
| FEA-SM-004 | Semantic models | Medium | Both columns of every relationship have the same type (SM001) | Model definition |
| FEA-SM-005 | Semantic models | Low | Every relationship key is a whole number (SM002) | Model definition |
| FEA-SM-006 | Semantic models | Low | The fact-side key of every relationship is hidden (SM004) | Model definition |

The thresholds (4 Admins, 5 direct writers) are KratosForge's: Microsoft's guidance asks for least
privilege but gives no number. A profile can turn any control off, with a reason, or change its
severity. Listing role assignments needs a workspace role that can see them; for anyone else the
security controls are not evaluated.

Role controls look at direct assignments only: members of a group aren't expanded. A user whose sign-in
name Fabric didn't send can't be told apart from a guest, so FEA-SEC-002 is then not evaluated. The
controls that compare an item with the others of its kind (FEA-PERF-001, FEA-PERF-002, FEA-DE-002)
pass an item only when every other notebook or pipeline in the workspace was read; if one wasn't (no
access, or the read budget ran out), they are not evaluated and name the ones that weren't read.

The architecture controls follow the KratosForge standing decision: one schema-enabled lakehouse per
medallion layer. FEA-ARCH-001 and FEA-ARCH-003 read layers, environment and left-overs from names, which
is **inferred** evidence: a name is what someone typed, not what the item is. Their results are shown as
advice and are never scored; when no name says a layer, or the workspace name doesn't say whether it is
production, they are not evaluated. Fabric's own Dataflow staging items aren't counted as left-overs.
FEA-ARCH-002 and FEA-ARCH-004 are verified: the lakehouse's properties (Fabric gives a default schema
only for schema-enabled lakehouses) and the notebook definition the optimization engine already reads. A
default lakehouse that isn't in the workspace's item list is in another workspace or was deleted;
either way a deployment won't rebind it. Dev/Test/Prod separation isn't checked yet: it needs
deployment pipeline or Git configuration the assessment doesn't read, and a name alone can't prove it.

### Notebook and pipeline inspection

The notebook and pipeline controls use the definitions the optimization engine already reads; they
make no Fabric call of their own. While the engine has a definition in memory, it is inspected on
this PC and only the result is kept as evidence (`item.inspection`):

- for a notebook, which [notebook best-practice rules](../notebook-best-practices.md) matched, how
  often, and the cell and line of each. For a secret (NB001), only that one was found and where,
  never what it was;
- for a pipeline, how many activities it has, and the names of those that are deactivated or name a
  fixed workspace id; of the activities that call out, the names of those with no retry and those
  with no timeout under 12 hours; and whether any activity runs when another fails or completes. Not
  the ids, and not the policy values themselves.

Notebook code and pipeline JSON never enter the evidence or the snapshot. A definition that couldn't
be read leaves these controls not evaluated.

Retry, timeout and failure paths are read from each activity's `policy` and `dependsOn` as the
definition states them. A setting the definition leaves out takes Fabric's default (no retry, a
12-hour timeout), so it fails. A retry or timeout given by an expression counts as set: its value is
only known at run time. An activity whose policy wasn't kept (an older engine) or can't be read leaves
FEA-REL-004 and FEA-REL-005 not evaluated, never passed. The same goes for FEA-REL-006 and `dependsOn`.
Outlook and Teams activities aren't counted as calling out, because a retry can send the message
twice, nor are child pipelines (their own activities carry the policy) or control-flow activities.
The 12-hour line is KratosForge's, because Microsoft gives the default, not a number to use, and only the
pipeline's owner knows how long a step should take, so any explicit timeout under the default passes.
Before adding a retry to a stored procedure or Web call, check the step can safely run twice.

### Semantic model inspection

The semantic model controls decide from the model's definition only. It is inspected on this PC and
only names and counts are kept (`item.model`): relationships that filter both ways, the roles that
filter rows and the tables they filter, how many tables are Direct Lake and the model's Direct Lake
behavior, and which [semantic model rules](../semantic-model-rules.md) (SM001, SM002, SM004) matched,
with the relationship or column names. DAX, M, a role's filter and members, and the definition text
never enter the evidence or the snapshot. A definition that couldn't be read, or a model past the 30
read, leaves these controls not evaluated.

What needs the model's data isn't checked here. High-cardinality columns, model size and the Direct
Lake guardrails need VertiPaq statistics or the Delta tables' numbers. The
[Direct Lake check](../direct-lake-check.md) on the Semantic model tab reads those, and the full Best
Practice Analyzer runs in Fabric only when you ask for it, and the assessment starts neither.

### Capacity usage evidence

FEA-CAP-002 to FEA-CAP-004 decide from the capacity's state and SKU (`GET /capacities`, verified
configuration) and from Capacity Metrics app exports you imported on this PC, which are **measured**.
Each row is an operation's Total CU (s), or an item's CU (s) over the period you entered. Nothing about
usage is read from Fabric. An export is matched to the capacity through the assessed workspace: the
Capacity Metrics app shows one capacity at a time, so an export that holds this workspace's use is
taken to be of its capacity. The evidence names the files and their period, so an export taken before
the workspace moved capacity can be spotted. Exports can be sampled, so a file may not hold every
operation. A fail (failures, refreshes) is then a lower bound, and a low utilization should be checked
against the Compute page. Compute-page exports whose periods overlap count once, and the longest is
kept. An export filtered to some workspaces holds only their use, so the unused capacity it shows is
from the imported rows, and the capacity's real use is higher.

Without an import that holds the workspace's use, these controls are not evaluated and ask for one. A
control whose column isn't in the import (Status, Operation, or the Compute page's items with a
period) is not evaluated, never passed. The exports the app reads don't say whether the capacity was
throttled or whether an operation was interactive or background, so there is no control for either
yet. A benefit is capacity freed, estimated; it becomes money only when a capacity is resized, paused
or retired. The 10%, 24-a-day and 7-day thresholds are KratosForge's.

## The scorecard

**Optimize > Assessment > Excellence** shows the assessment. Like the rest of Assessment, it is a Preview feature. Pick a workspace and choose
**Assess**, or try the demonstration fixture, which makes no Fabric call and is labelled everywhere.

- The overall score, the assured score, coverage and status sit side by side. The rating chip says
  **Provisional** or **Insufficient evidence** when coverage is under the profile's thresholds, and a
  **Critical Risk** banner stands above the score whenever a critical control failed.
- Ten dimension cards. A dimension no control covers yet shows as **Not applicable**, never as
  passing. Each card opens its controls, every outcome on every resource, and the evidence each one
  cites.
- **Next actions** lists up to five findings, severity first. Each opens the full finding: what was
  found, why it matters, the steps, the expected benefit, how to check it worked, how to undo it, the
  evidence, references and the exact rule version.
- **Gaps** lists the reads that failed, the controls that weren't evaluated with what they needed, and
  the items no control covers yet.
- **Opportunities** is the existing opportunity view, unchanged, one tab over.

Nothing on the screen changes anything in Fabric. The last result stays on screen until you assess
again or leave the app; snapshots aren't saved yet, so there is no trend or comparison on screen.

## Limitations

- Snapshots aren't stored yet, so the scorecard has no trend arrows and no "Compare with an earlier
  one". The engine can already compare two snapshots.
- The first controls cover what the app already reads. Estate X-ray (table layout), governance checks,
  the semantic model checks that need data, and more DevOps and reliability controls come in later
  stories; until then those dimensions show as not applicable or not evaluated, never as passing.
- Controls that need a Fabric administrator (tenant settings) are not evaluated for anyone else.
- Live capacity utilization has no public API. Capacity controls use imported Capacity Metrics
  exports only; without one they are not evaluated.
- Capacity freed is not money saved. Financial figures follow the
  [measurement model](../finops/plan.md): measured, estimated and verified figures are kept apart.
