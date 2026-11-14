# Development status

The state of development at the end of the last product management cycle. It's replaced each
cycle, and the history is in Git. The counts come from `python docs/product/tools/product_data.py summary`.

**Product check of 2026-11-13 23:42 UTC, main af7d786b.** Covers the PRs merged from #135 to #2609.

## Summary

- Evening check, 2026-11-13: 38 PRs merged after 6cab0dc3. 3 were product data and operations PRs, and the other 35
  were linked. Notebooks now run in a process of their own by default (PB-136, now In review: the Windows trial on
  Python 3.12 passed in every area). Settings has a color mode IT can set, and audit trails and receipts take an IT
  time limit (PB-061; the Group Policy templates are left). The SQL editor gained run-at-cursor, hover help, Excel and
  JSON export and Open in SSMS (PB-106). Notebooks export as PDF and render Markdown tables and math (PB-103). The
  optimization engine assesses a workspace for duplicate work (PB-104, Preview), skins choose their look from fixed
  choices (PB-102, Preview), and the engine can live in an admin-only folder by policy (PB-124, Preview).
- Acceptance, 2026-11-13: the product owner accepted PB-073 (notebook cell time and memory limits) with documented
  limitations. Notebooks still share the in-engine kernel unless **Run notebooks in their own process** is on; making
  it the default is PB-136, which waits on a clean Windows trial and the product owner's go. The 19 feature request
  issues moved to a roadmap label, so open issues now track bugs only.
- Morning check, 2026-11-13: 8 PRs merged after 36844275. 2 were product data and docs audit PRs, and the other 6
  were linked. The policy key docs now match what the app reads, with a test to keep them in step (PB-061). Agent
  schedules that are edited or removed in agent.toml run once, as configured now (PB-124). No item changed state;
  PB-073's acceptance waits on the product owner.
- Evening check, 2026-11-12: 16 PRs merged after 7c3dbd0e. 1 was a product data PR, and the other 15 were linked.
  Mutation testing reached batch 10, covering the policy and approval gates, SQL translation and local PySpark
  (PB-101). Spark on this PC handles extract on text, NaN literals and read.csv's default comma, and SQL cells that
  end in comments run (PB-103). Set-aside copies made in the same instant no longer replace each other (PB-124). No
  item changed state; PB-073's acceptance waits on the product owner.
- Morning check, 2026-11-12: 18 PRs merged after 86c07814. 1 was a product data PR, and the other 17 were linked.
  Spark SQL's session, clock and random functions answer as in Fabric, and unnamed columns are named as Spark names
  them (PB-103). A failed Delta write no longer leaves the next query stuck (PB-099). Blocked files name AppLocker or
  WDAC, Windows FIPS mode is explained, and errors cite a request id only when one was sent (PB-124). Mutation
  testing now pins the write, commit and clear-up paths. No item changed state; PB-073's acceptance waits on the
  product owner.
- Evening check, 2026-11-11: 19 PRs merged after 47416473. 2 were product data and docs audit PRs, and the other 17
  were linked. App Studio gained its Model connections screen and a working Publish dialog (PB-127, Preview). Local
  data projects read Delta tables under folders with spaces or non-ASCII names and refuse a write Windows can't read
  back (PB-099). CI installs from hashed lock files, workflows pin their actions, and the shell's Rust crates are
  gated by clippy and cargo-deny (PB-124). No item changed state; PB-073's acceptance waits on the product owner.
- Morning check, 2026-11-11: 72 PRs merged after 17e7ea06. 2 were product data and docs audit PRs, and the other 70
  were linked. Incidents gained their measures (time to acknowledge, time to resolve, SLAs met) in the engine
  (PB-105); what's left there is the reports screen, which needs a mockup, and service desk tickets, which need the
  product owner's go. Estate X-ray Optimizes ticked small-files findings together (PB-104). Sign-in survives a
  damaged saved sign-in and an unreachable Microsoft sign-in, the app follows the Windows proxy, and tools start by
  full path (PB-124). Runs that ended mid-write list their leftover files (PB-067), and support bundles are proven to
  carry no secrets (PB-062). PB-073 moved to In review: its cell time and memory limits shipped, and the review
  recommends Accept with documented limitations. The default flip moves to the new PB-136, waiting on a clean Windows
  trial and the product owner's go.
- Evening check, 2026-11-10: 21 PRs merged after 5d2fc045. 3 were product data PRs, and the other 18 were linked.
  The secret scan no longer flags commit ids and warns reviewers about changes to its own filters, the agent's crash
  log tells a crash from a handled fault, and expired local copies are swept daily (PB-124). VS Code says when a cell
  is too large before sending it and asks again after a dropped check (PB-109). Living Experience now defaults to
  Subtle, and only achievements celebrate (PB-102, Preview). PB-120 was accepted with documented limitations.
- Acceptance, 2026-11-10: the product owner accepted PB-120 (platform support levels and one engine contract) with
  documented limitations. Only Fabric is proven live, to level 2; Databricks and Snowflake are verified at level 0.
  The SQL portability report and the two-engine comparison have no screen yet, and function checks use one
  documented example per function on this PC.
- Morning check, 2026-11-10: 17 PRs merged after d5a867c0. 1 was a product data PR, and the other 16 were linked.
  Every outbound request now retries throttling or says why it is sent once, Fabric lists follow both kinds of
  continuation, CI scans for secrets, and a short memory soak runs in CI on Linux and Windows (PB-124). Notebooks on
  this PC answer spark.catalog about the lakehouse and read or write Unity Catalog style names, or refuse by name
  (PB-103). No item changed state.
- Evening check, 2026-11-09: 54 PRs merged after 2bf8ea1b. 5 were product data, docs audit and operations PRs, and
  the other 49 were linked. The weekly review's hardening stories landed: retries go through one module, Fabric
  errors and the app's errors name a next step, a write whose reply was lost is never made twice, IT can check audit
  exports, and every shipped lock file is scanned (PB-124). A seeded kill test for local Delta writes runs in CI on
  Linux and Windows (PB-099). The Databricks Associate track gained seven exercises (PB-135). PB-120 moved to In
  review with its implementation review, which recommends Accept with documented limitations; the decision card waits
  on the product owner. Docs audit 16 marked the 28 features whose only verification gap is a live tenant run.
- Acceptance, 2026-11-09: the product owner accepted PB-128 (Academy) for the DP-700 track with documented
  limitations. 48 of 49 objectives have built exercises, eventstream errors has a recorded gap, and objectives that
  need Fabric complete only with Fabric runs. The Databricks track moves to PB-135 (In progress), because 7 of its 33
  objectives have neither an exercise nor a gap.
- Morning check, 2026-11-09: 41 PRs merged after e5e6bec4 (nothing merged on 2026-11-08). 2 were product data and
  docs audit PRs, and the other 39 were linked. Academy DP-700 gained three capstones (PB-128), and the last failing
  Databricks SQL functions now run or are refused by name (PB-120). Local Delta's first-append race, slowdown from run
  count and stores without limits are fixed (PB-099). Expired sign-ins, HTTPS inspection and the agent's install folder
  are hardened (PB-124). The app works at 200% zoom and in Windows contrast themes (PB-063). No item changed state.
- Evening check, 2026-11-07: 74 PRs merged after 56e3826b. 4 were product data PRs, and the other 70 were linked.
  Audit lines for Prod and confirmed actions are now written first, and the action doesn't run without one. An
  authorization matrix lists every state-changing call (PB-124). The Living Experience Home hero, Forge Activity view,
  empty-state art and motion modes landed in Preview (PB-102). Databricks and Snowflake SQL answer as their docs show
  in more places (PB-120), and Academy's question bank is re-checked and grows to 498 (PB-128). The Data mesh owner's
  own scores moved PB-130 from P2 to P3 (#2182) before it was accepted. PB-134's owner is now the mastery thread, and
  its scores stay provisional until that owner sends its own.
- New item, 2026-11-07: PB-134 Developer classes and mastery (In progress), from the product owner's brief of 14:17 UTC.
  Classes and talent trees build on the profile's verified evidence, and mastery levels never come from time spent.
  The first PR is engine-only (#2206, draft). Screens wait for the owner's reaction to the mockup.
- Acceptance, 2026-11-07: the product owner accepted PB-130 (data product promises, status and change windows) with
  documented limitations. All three screens are Preview and untested on a Fabric tenant, Status is only as current as
  the team's own incident check, and the change window warns but never blocks a promote.
- Morning check, 2026-11-07: 83 PRs merged after c750da45. 2 were product data and docs audit PRs; the other 81 were
  linked, 54 of them type checks, tests and hardening (PB-101). The engine's type-check baseline is now empty, so every
  module type-checks. A stopped local Delta write cleans up after itself, and each commit is flushed to disk before the
  write returns (PB-067). Academy gained 20 more DP-700 exercises (PB-128), and credentials stay out of the engine log
  and backups. No item changed state.
- Evening check, 2026-11-06: 97 PRs merged after de881016. 3 were product data PRs; the other 94 were linked, 52 of
  them type checks, tests and hardening (PB-101). Screen readers now hear dialog descriptions, command bar results and
  page changes (PB-063; the Windows Narrator pass is still to come). Every outbound call works behind a corporate
  proxy, and the build pins its base image and build tools (PB-124). Academy gained Git, database project and access
  control exercises (PB-128), the skin color tokens are finished (PB-129), and workloads and incidents are stricter
  about unknown states (PB-105).
- Acceptance, 2026-11-06: the product owner accepted PB-129 (platform skins) with documented limitations: appearance
  and workflow come in one skin, there's no Import skin screen yet, and the demonstration mocks the cloud parts. The
  first two follow as PB-132 and PB-133 (Proposed).
- Review, 2026-11-06: PB-129 (platform skins) moved to In review; its review recommends Accept with documented
  limitations (docs/product/reviews/PB-129-skins.md).
- Morning check, 2026-11-06: 46 PRs merged after a7631920. 2 were product data and docs audit PRs; the other
  44 were linked. Platform skins recorded their end-to-end demonstration with mocked cloud parts and check
  keyboard, axe and reduced motion in every skin (PB-129). Axe now runs on every dialog with an empty baseline,
  and a Narrator checklist is ready for the Windows pass (PB-063). Notebooks gained df.show(), cube, rollup and
  more Column helpers as in Spark 3.5, with a PySpark API coverage list (PB-103); T-SQL functions on this PC
  have a generated page (PB-106). The MSI's silent install and detection are documented (PB-124).
- Evening check, 2026-11-05: 78 PRs merged after 7c7ed9e2. 1 was a product data PR; the other 77 were linked.
  App Studio can plan an app from a description, apply asked-for changes as one undoable step and publish a
  Power BI report behind a check and a typed confirm (PB-127). The Databricks-familiar and Snowflake-familiar skins
  landed (PB-129, Preview). Embed Studio's Power BI page is in Workspaces (PB-121, Preview). Academy runs exercise
  scripts in the AppContainer by default on Windows, with more DP-700 exercises (PB-128). Local T-SQL, Spark SQL and
  Snowflake answer as those platforms do in more places, and local T-SQL is about 3x faster. Audit remediation
  (PB-124, score 64): one transaction helper, narrower Tauri permissions, the previous build kept until the new
  engine answers (#1596). Axe covers the most-opened dialogs, with a guard for new ones (PB-063).
- Morning check, 2026-11-05: 6 PRs merged after 03d2b53a. 2 were product data PRs; #1828 (a data product's page
  no longer has a Business terms card) updated its own rows, and the other 3 hardened Academy's AppContainer (PB-128).
- Acceptance, 2026-11-05: the product owner accepted PB-131, now titled "Let people remove Academy's runtime
  access grants", after a Windows test in #1823 showed the grants removed with none left.
- Evening check, 2026-11-04: 16 PRs merged after 1ca71e38. 1 was a product data PR; the other 15 were linked.
  Databricks gained Preview screens (PB-120): job runs and Lakeflow updates in Monitor, DBUs from billing after a
  confirm, notebook folder sync and Asset Bundle validate, plan and deploy; their parity levels were re-assessed.
  PB-131 is In review: Settings > Advanced > Exercise sandbox removes the Academy sandbox's permission after a
  confirm, and Security chose no uninstall step (review docs/product/reviews/PB-131-1820.md, Accept with
  documented limitations recommended). Also Academy AppContainer hardening and UDF_Platform fixes.
- Morning check, 2026-11-04: 13 PRs merged after 9b7848eb. 2 were product data and docs audit PRs; the other
  11 were linked. Academy (PB-128) added PySpark, pipeline and Python exercises run from the Academy page, and
  an opt-in AppContainer for exercise scripts on Windows. Its access grants on the person's Python runtime aren't
  removed on uninstall yet, so new PB-131 (Proposed, required) tracks that before it is on by default.
  Incidents stage 3 (PB-105): assigned incidents in the bell and Teams or email delivery. PB-016's package
  source is recorded as decided (IT wheel folder, then PyPI if policy allows; #1765). PB-128 stays P3 on its
  owner's scores: Chris's directive is the go and sets no priority.
- Evening check, 2026-11-03: 55 PRs merged after 5f8144f7. 2 were product data PRs; the other 53 were
  linked. Three new In progress items record work already merged: PB-128 KratosForge Academy (DP-700 and
  Databricks Data Engineer Associate plans, objective registries, exercises graded on what a learner writes,
  an exam engine, Assessments, and Academy replacing Learn in the rail, all Preview), PB-129 platform skins
  (P0 from Chris's brief: skin manifest, the KratosForge and Fabric-familiar skins, parity levels) and PB-130
  data product promises (Kept or Broken, Status, change window). PB-040 Fabric workspace Git moved to In
  progress (link, differences, Update from Git), and PB-123 too (Profile Impact tab). Also: incidents
  stage 2, App Studio's page and local preview, Databricks and Snowflake CLI connections, Spark result
  fixes, a per-user MSI and environments from the IT wheel folder.
- Morning check, 2026-11-03: 35 PRs merged after 9367abf7. 5 were product data, docs audit and queue PRs;
  the other 30 were linked. Fabric App Studio started (new PB-127): its design, the app specification
  with undo, model connections that use the person's own model and key, sanitized semantic model
  metadata and a compiler to a Power BI report definition. It is the first place KratosForge itself
  calls a model, and it stays off until a model is connected. Also: the Optimize plan (PB-104), a team
  guidance level set by policy (GD-09), an installable VS Code extension on Preview (PB-109), window
  zoom with every page fitting at 200% (PB-063), shortcut requests made in every environment, and
  schedules that run while you're signed out.
- Acceptance, 2026-11-03: the product owner accepted PB-014, PB-125 and PB-126, and PB-084 with documented
  limitations (no Windows or live Teams run).
- Evening check, 2026-11-02: 58 PRs merged after 32cbd98f (#1166 to #1699). 7 were product data,
  docs audit and operations PRs; the other 51 were linked. The whole Learn curriculum landed (PB-126,
  six batches with the Forge Trial capstone lab), and Profile has ranks earned from verified work and
  a profile picture (PB-125). The notebook names the cells the compatibility check can't read, from
  the approved mockup (PB-014). A late table now reaches Teams once (PB-084, #1553). More enterprise
  audit fixes merged (PB-124), and a keyboard-only pass covers every rail page. PB-014, PB-125 and
  PB-126 moved to In review.
- Decisions, 2026-11-02: the product owner accepted PB-119 (Learn v1) and PB-005 (token cache, with documented
  limitations until a Windows check with a second account), chose fix (a) for PB-084 (only incidents post to
  Teams, #1553), and asked for a UX mockup of Unknown cells before PB-014 is re-reviewed.
- Morning check, 2026-11-02: 66 PRs merged after 761a7949 (#1550 to #1638). 3 were product data
  PRs; the other 63 were linked. An enterprise engineering audit (#1571, docs/audits/) scored
  readiness 54/100 and opened the EA remediation backlog, and its first fixes landed: Spark casts,
  division by zero, dates, CSV options and hash() now answer as Spark does or refuse, Cancel stops
  queries a notebook opened, and a killed scheduled run no longer holds its schedule. The engine
  build installs from hashed pins and checks its Python and source hashes. Local T-SQL casts, LIKE
  ranges, DATEDIFF(week) and window averages answer as T-SQL does. OneLake leases are judged on
  OneLake's clock, and publish overwrites commit against the version they read. The sign-in cache
  is changed under a lock (PB-005 finding 1). Lab time and memory limits now have tests (#1552),
  so PB-119's criterion 2 names them.
- PB-119 split (2026-11-01, #1551): Learn v1 is In review, Guided Development is PB-122 (In progress,
  8 of 10 rows merged) and Learn phase 2 is PB-123 (Proposed; each slice needs the product owner's go).
- Evening check, 2026-11-01: 35 PRs merged after 063b84ed. 5 were product data and docs PRs; the other 30 were linked.
  The engine's pyproject allows DuckDB 2 (#1401, decision D-E10), while the app keeps 1.5.6. Learn
  adds Query profile notes, decision patterns and a guidance level per subject (GD-04, GD-05,
  GD-08, Preview). The rest is hardening: redaction of Basic credentials and Databricks secrets,
  reply text from Fabric, Databricks, OneLake and Graph kept to one short line, files and
  connections closed, and type-check fixes.
- Acceptance, 2026-11-01: the product owner accepted PB-027, PB-096, PB-098, PB-116, PB-117 and
  PB-118, three of them with documented limitations. PB-098's criterion now reads "a test fails
  when lists meant to agree disagree".
- Morning check, 2026-11-01: 103 PRs merged after b3c5b223 (#1359 to #1523), all hardening and tests.
  The remaining screens and their writes have real-browser tests, and several gained the danger or
  risk-check confirm they lacked. Databricks sign-in, profiles and discovery refuse replies that
  aren't the documented ones. More engine readers work on DuckDB 2.0, and CI runs on it, though 2.0
  isn't allowed yet. Errors stay one bounded line. App state files are written safely. Engine HTTPS
  trusts the Windows certificate store, and support bundles leave out tenant and OneDrive names.
  No backlog item finished. PB-014's review asks for Unknown cells in the notebook (#1362).
- Evening check, 2026-10-31: 99 PRs merged after f3750b9d (#1205 to #1357), the full-speed day.
  Most are hardening and tests. The engine is ready for DuckDB 2.0 (#1341's parts), OneLake reads,
  writes, shortcut checks and leases wait out throttling, governance requests say what stays in
  Fabric after a decline or withdrawal, and ten more screens have real-browser tests. Also: the
  compute check follows %run and notebook.run and reports Unknown (PB-014), support bundle slice 1
  (PB-062), Learn GD-03, GD-06 and GD-10, and Databricks, Spark and Snowflake SQL that answers as
  those platforms do. PB-014 and PB-062 are now In progress.
- Morning check, 2026-10-31: three more PRs merged. Snowflake functions are checked value by
  value against Snowflake's docs (PB-117, #1202), a time limit or early Cancel says truthfully
  whether a remote statement stopped (#1201), and every platform role write leaves an audit row
  (PB-052, #1200). PB-117 moved to In review in the queue refresh (#1196).
- Evening check, 2026-10-30: 23 PRs merged after 02118fa4, besides the product data PRs.
- Acceptance batch 2 (2026-10-30, #1163): the product owner accepted 19 items, 10 of them with
  documented limitations, and approved the changed criteria for PB-031, PB-049 and PB-072.
- Merged:
  - Learn guidance levels, with Professional the default (GD-01, #1161), and Try the fix and Compare
    beside SQL findings (GD-02, #1162). Also Explain and Lesson buttons (#1160) and the Microsoft
    Fabric path (#1159). All Preview.
  - The Getting started panel on Home (#1147).
  - Column completion in notebook `%%sql` cells, offline too (PB-027, #901).
  - Prove takes Spark notebooks with MERGE, UPDATE and DELETE (PB-015, #1157).
  - The AI client checklist run with Claude Code, Codex CLI and curl (PB-096, #1185).
  - The remaining allow_ policy switches fail closed (PB-061, #1150). The engine's method lists are
    checked against each other (PB-098, #1152). HD-050 slices 6 and 7 (#1153, #1156).
  - Databricks SQL whole results and functions (#1180, #1183), and retries for remote Spark and
    Databricks calls (#1191).
- PB-027 and PB-096 moved to In review. PB-061 is In progress: the keys FD-105 lists are still to
  decide. PB-015 stays In progress for its live Prove run.
- Inventory: 143 features. 121 are Implemented, 13 Partially Implemented, 3 Implemented but Not Exposed, 2 Missing and 4 Planned.
  3 are Verified, 129 Partially Verified and 11 Unverified. Fabric sign-in, listing and Spark SQL now have one recorded live run.
- Backlog: 124 items (7 P0, 33 P1, 60 P2, 24 P3). 14 are Proposed, 30 In progress, 3 In review and 77 Accepted.
- PB-125 (Learn ranks and profile pictures) and PB-126 (Learn curriculum content batches) are new and Proposed, from the
  product owner's ask of 2026-11-02; they build once the rank ladder is approved.
- PB-124 (new, In progress, P0 like PB-101): the enterprise audit's EA remediation backlog (docs/audits/), tracked as its own item.
  44 of its PRs are merged; it tracks fixes only.

## Workstreams

| Workstream | Owner thread | Backlog | State |
| --- | --- | --- | --- |
| Local execution and compute | Local data engine and OneLake publishing | PB-094, PB-068, PB-015 | Kernel measured (#895); key lookups merged (#913); #933 open |
| Fabric, SQL and warehouse | Build full warehouse development experience | PB-002, PB-043, PB-106 | Smoke-test harness merged (#884, #919); the live run waits on a yes |
| Data Factory and pipelines | Data Factory; Local Fabric pipeline execution | PB-107, PB-037 | #799 draft; DF-8 and P5 need a go |
| Semantic models | Best Practice Analyzer; TMDL and OSI conversion | PB-110, PB-112 | BPA live run next |
| Governance and mesh | Enterprise governance foundation; data mesh | PB-057, PB-053, PB-083 | Block policies stop promote (GOV-003, #924); endorsed filter (#889) |
| Developer experience | UX oversight; action-first; VS Code | PB-078, PB-077, PB-070, PB-063 | Audit fixes (#926, #951), tab rows (#988), High Contrast (#990), Browse buttons (#987) |
| Testing and security | Codebase audit; engineering quality | PB-074, PB-072, PB-051, PB-101 | Long calls bounded (#898); mypy and ruff in CI (#1011, #1032); HD-050 slices 3 and 4 |
| Databricks and Snowflake | Fabric and Databricks unified development | PB-116, PB-117, PB-118 | Livy (#891) and the loss report (#1053) merged; engine picker (#1096); Fabric verified to level 2 |
| Release | Release management | | Working |

## Open pull requests

See GitHub for the live list. Every queued story with an open PR is In progress in the backlog.

## Blocked on the product owner

Trusted Signing (PB-001) and a non-production workspace for the live harness (PB-002, PB-003) are the
two release gates. Also waiting: acceptance of the 3 In review items (PB-024, PB-045, PB-136), a tenant run of
the Data Factory writes and the BPA run, governance steps 8 and 12, Direct Lake on zstd, and the privacy
and support details.

## Orchestration

Development runs when the product owner or a thread wakes the project coordinator. A product
check twice a day (11:42 and 23:42 UTC) refreshes this data and proposes assignments. Pull request events can reach the thread that opened the PR. Quality review gates every PR,
and only Release management merges.
