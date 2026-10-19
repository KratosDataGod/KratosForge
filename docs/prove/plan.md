# Prove this notebook: program plan and backlog

This is the delivery plan for making notebook creation, local and Fabric execution, comparison and value measurement the defining experience of Fabric Desktop by KratosBI. It tracks all 100 capabilities Chris listed on 2026-10-16, what exists today, and the order they will be built in. The live state of work (branches, PRs, what is next) is in [continuation.md](continuation.md).

A sister plan, owned by the "Staged roadmap for ten capabilities" work, covers large-table execution, guided ingestion, Git and release workflow, Fabric pipeline handoff, remote agent control, cross-project lineage and always-on monitoring. Items here that build on that work are marked **Dep: roadmap** and are not duplicated. Its plan lives in its own file in this repository once that work lands; this file links to it then.

Status words used below:

| Status | Meaning |
| --- | --- |
| Done | Implemented adequately today. |
| Enhance | Something exists but needs work to meet the item. |
| Missing | Nothing meaningful exists. |
| Blocked | Needs an API, access or decision we don't have. The blocker is named. |
| Not now | Not worth building in this program; the reason is given. |

## 1. What exists today (verified on main at af11938, 2026-10-16)

Checked by reading the code and running the engine's tests (322 passed, 2 skipped on Linux, Python 3.13).

### Notebook kernel and process model

- The UI is plain HTML/JS in `app/ui/`; the engine is one Python process (`fabric_desktop.server`) that the Tauri shell starts and talks to over JSON lines on stdin/stdout. **The shell serializes every RPC behind one mutex** (`app/src-tauri/src/main.rs`, `rpc`), with no timeout.
- The notebook kernel (`notebook.py`, `Kernel`) runs **inside the engine process**. Each cell runs on a thread in one shared namespace; `start`/`poll` stream output; Cancel raises `KeyboardInterrupt` in the cell's thread (and interrupts DuckDB). **Stop kills the whole engine process** (`stop_kernel`), which loses every variable and any other work in flight.
- Consequences: user code shares memory, imports and crashes with the engine; a cell that hangs in native code can only be stopped by killing the engine; only one notebook can run at a time; user packages cannot differ from the engine's.

### Dependencies and environment

- The engine ships pinned packages (`sidecar/constraints.txt`): DuckDB, Polars, deltalake, pyarrow, sqlglot, matplotlib and their dependencies. `%pip` in a cell is skipped on the PC with a note.
- Fabric Python notebooks offer Python 3.10, 3.11 and 3.12 (3.12 is Fabric's default) and come with DuckDB, Polars and delta-rs preinstalled; Microsoft doesn't publish package versions, and [Environments aren't available for Python notebooks](https://learn.microsoft.com/en-us/fabric/data-engineering/using-python-experience-on-notebook). Fabric Desktop by KratosBI publishes Python notebooks with the `python3.11` kernel.
- Nothing records which package versions a run used, on either side.

### Local versus Fabric execution contract

- Notebooks use `from fabric_desktop import lakehouse` (read/write tables and `Files/`), `spark` (DuckDB's PySpark API locally; every Spark write goes through `lakehouse.write_table`), `notebookutils`, `%%sql`.
- Publishing (`fabric_run.fabric_ipynb`) adds a first cell, a shim that defines the same `lakehouse` module with Polars, deltalake and DuckDB in Fabric. A notebook that uses Spark is published as a Spark notebook, where `spark` is Fabric's own; otherwise as a Python notebook.
- `fabric_run.compute_check` flags, per cell, code that can't run locally (Scala/R cells, RDDs, streaming, Fabric-only utilities, SQL MERGE/UPDATE/DELETE) or in Fabric (no lakehouse, `C:\` paths). It is a regex check; it has no notion of "unknown".

### Publishing and scheduling

- Publish creates or updates a Fabric notebook item in the notebook's saved workspace. Run in Fabric starts `RunNotebook` through the job scheduler and polls the job instance. Fabric schedules use Fabric's schedule API; PC schedules use Windows Task Scheduler. There is no preview of what a schedule will run.

### Input/output binding and side effects

- The attached lakehouse is the only binding. Tables are addressed by name; there is no version pinning, no declared inputs or outputs, and no environment-specific binding.
- Every write goes through `lakehouse.write_table` (locally) and is checked against the Fabric-safe Delta profile; tables managed by the metadata framework are refused (`write_guard`).
- Side effects outside the lakehouse API (direct `deltalake` writes, HTTP calls, files on disk) are neither detected nor contained.

### Comparison methodology (Side by side, PR #44)

- Runs the notebook on the PC, then publishes and runs it in Fabric, **against the same lakehouse, writing the real tables twice**. It snapshots every table's Delta version before, between and after, and compares the tables each run wrote by row count, column kinds and an order-independent checksum (doubles rounded to 10 significant digits, timestamps in UTC).
- Limits: shared outputs are overwritten (twice); the Fabric run starts from what the PC run left, so merge/append/update notebooks are not compared from equal state; inputs aren't pinned, so a writer elsewhere changes the result; a checksum mismatch says nothing about which rows differ; no tolerances, keys or ignored columns; files under `Files/` aren't compared; the environment isn't recorded; results live in a local JSONL with no integrity protection.
- Each comparison records the notebook's code revision, computed the same way as a Prove receipt's, so tools that weigh it (the FinOps placement advisor) count it only for the code that ran.

### Telemetry and cost

- PC: wall-clock per cell and per run; cores and memory of the machine. No CPU time or peak memory.
- Fabric: the job instance's start and end times, a timing cell inside the published copy (run time vs session start), and a CU estimate = job seconds x configured CUs (Python 1, Spark 8) x price per CU hour, labeled as an estimate. Fabric exposes no per-job CU consumption API; the Capacity Metrics app has billed numbers.

### Security, identity and protections

- Sign-in is delegated (the Fabric CLI public client with WAM); tokens are the user's own. Notebook code runs with the user's tokens, through `notebookutils.credentials.getToken`, so **notebook code can do anything the user can** — in the engine process, with no isolation.
- Secrets fetched through `getSecret` are hidden in the Variables pane. Managed tables are protected from notebook writes. Tenant and workspace boundaries are those of the user's own access.

### What else exists that this program builds on

Item preview, profiling (metadata entities), table history/restore, Monitor with a Cost panel, Explorer click-to-insert, kernel-namespace autocomplete, Variables pane, parameter cells, `%run` and `notebook.run`, the TPC-H benchmark notebooks, the metadata framework's tests and compare, and the headless agent.

## 2. Architectural decisions for this program

1. **One proof runtime, two sides.** The code that pins inputs, contains writes and records what a run read and wrote is one self-contained Python module. The engine imports it on the PC, and its exact source is embedded in the notebook published for the Fabric run. The two sides cannot drift.
2. **Equivalent inputs by pinning, not copying.** At the start of a proof the engine records the Delta version of every table in the attached lakehouse. Both runs read every table at that version. Files read under `Files/` are hashed (SHA-256) on each side; a mismatch makes the comparison inconclusive rather than wrong.
3. **Outputs never touch shared tables.** In a proof, `write_table` writes to a per-run sandbox, `Files/_fabric_desktop/proofs/<proof id>/<side>/tables/<schema>/<table>`, in the same lakehouse (no new cloud resources). A run that reads a table it wrote earlier reads its own sandbox copy. Shared tables are checked before and after: if any moved during the proof, the result says so.
4. **Unsupported side effects are refused, not guessed.** Before a proof runs, each cell is classified. Writes outside the lakehouse API (Spark writes in Fabric, direct `deltalake`, `notebookutils.fs` writes, SQL DML) block the proof with the cell and reason; network calls and file writes on disk are reported as "unknown side effect" and recorded in the receipt.
5. **Verification says what it proved.** Each table gets a verification level: `fingerprint` (rows + order-independent checksum), `complete` (every row compared, by business key or by full row) or `sample`. Rules (tolerances, ignored columns, timestamp precision, key columns) are explicit and saved with the result.
6. **Receipts are content-addressed.** A run receipt is canonical JSON identified by its SHA-256. The store refuses to replace a receipt with different content. Exports are rendered from the receipt, with redaction applied at export time.
7. **Stable cell IDs.** Cells carry nbformat 4.5 `id`s, kept across saves and edits, so receipts, diffs and later review comments can point at a cell.
8. **Preview by default.** Every new Fabric-facing piece is Preview until it has run on a tenant.

## 3. Stages

Effort: S (days), M (about a week), L (two weeks or more), for one engineer. Risk is implementation and product risk.

### Stage 0: plan (this document) — PR "Prove this notebook: program plan"

Outcome: a reviewed plan and backlog in the repository. No code.

### Stage 1: foundations — stable cells, run receipts, environment profiles

- **Outcome.** Every notebook keeps stable cell IDs; a run can be described by an immutable receipt that names its code revision, parameters, inputs, environment and outputs; the engine can say which packages a notebook imports and whether each is available on this PC and expected in Fabric.
- **Foundational changes.** nbformat 4.5 `id` on every cell, kept through load/save and preserved for notebooks that already have them; unknown notebook and cell metadata survives a save. `receipts.py` (schema v1, canonical JSON, SHA-256 id, write-once store, Markdown/HTML export with redaction). `envprofile.py` (Python and package versions in use, imports per cell by AST, availability on the PC, Fabric expectation as documented/observed/unknown).
- **Compatibility.** Old notebooks without IDs get them on first save; a notebook saved by an older app keeps working. Receipts are new files; nothing migrates.
- **Acceptance.** IDs survive save → load → edit → save; Jupyter-style IDs are kept; a tampered receipt is detected; writing a different receipt under an existing id is refused; every import of the TPC-H notebooks is classified; unknown packages say "unknown", never "available".
- **Tests.** Unit tests for each; round-trip of notebooks from Fabric's own export format.
- **Rollback.** Revert the PR; notebooks keep extra `id` fields, which nbformat 4.5 readers accept.
- **Effort/risk.** M / low. **Why first.** Everything after it records its evidence in receipts and points at cells.

### Stage 2: isolated proof runs and verification (engine)

- **Outcome.** `prove_start` runs a notebook on the PC and in Fabric against pinned inputs, with each side's outputs in its own sandbox, then verifies every output table under explicit rules, finds differing rows by key, scores the run and saves a receipt. The shared lakehouse is untouched by the notebook.
- **Foundational changes.** `proof_runtime.py` (self-contained; pins, sandbox, file hashing, read/write log, environment capture), hooks in `lakehouse.py` (`open_table`, `write_table`, `table_exists`, `local_path`) and in the Fabric shim, `verify.py` (rules, levels, keyed diff with absolute/relative error), side-effect classification in `fabric_run.py`, `prove_service.py` (orchestration, cancel, status, receipts), CPU time and peak memory for the PC run.
- **Compatibility.** Side by side keeps working unchanged; Prove is a separate RPC set. Notebooks not using the lakehouse API for writes are refused with a reason.
- **Acceptance.** A notebook that overwrites, appends and merges into a table proves "same" from equal starting state; the shared table's version is unchanged afterwards; a value changed by 1e-12 passes a 1e-9 tolerance and fails exact rules; a changed row is reported by key with the column and both values; an external commit to an input during the proof marks the result inconclusive; a file input changed between runs marks it inconclusive; cancellation in either phase leaves a receipt marked cancelled and no shared writes.
- **Tests.** The Fabric side is tested by executing the actual published notebook cells in a separate Python process against local Delta folders, with a fake job scheduler. This proves the generated code, not Fabric itself. Failure paths: failed cell on each side, cancellation, unreadable sandbox, vacuumed pinned version.
- **Live validation.** Section 5.
- **Rollback.** Sandboxes are folders under `Files/_fabric_desktop/proofs/`; deleting them removes everything a proof wrote.
- **Effort/risk.** L / medium (Fabric behaviour of `/lakehouse/default` mounts and `RunNotebook` on Python notebooks is unverified). **Why here.** It removes the honest review's "side by side writes the real lakehouse twice" problem and is the core of item 100.

### Stage 3: the first-release experience (UI, Preview) — mockup first

- **Outcome.** From Create or a lakehouse table, a wizard builds a notebook with parameters, inputs, outputs and checks. The notebook ribbon's **Prove** button shows the plan (inputs pinned, outputs sandboxed, environment, unsupported cells), runs it, and shows the verdict, scorecard, per-table verification, an aligned diff grid with changed cells highlighted, a column heatmap, a tolerance slider, and **Export** (HTML report with redaction choices).
- **Foundational changes.** `wizard.py` (deterministic templates; no AI), contract metadata in the notebook (`fabric_desktop.contract`), `prove.js` replacing the Side by side panel when Prove is used. Side by side stays available.
- **Acceptance.** Every visible control works or shows why it can't; keyboard reachable; loading, empty, partial, cancelled and failed states drawn; 100k-row diffs stay responsive (grid pages from the engine).
- **Tests.** Engine tests for the wizard's generated notebooks (they run and pass their own checks on sample data); `app/tests/ui.test.cjs` for rendering logic; screenshots of each state.
- **Effort/risk.** L / medium (UX). **Why here.** It is the first demonstration Chris asked for; it needs stages 1–2 to show real evidence.

### Stage 4: process-isolated kernels

- **Outcome.** Each notebook runs in its own Python process: Cancel and Stop end only that notebook; a crash restarts its kernel with a message; the engine and UI stay responsive; per-notebook environment profiles can add packages without touching the engine.
- **Changes.** Kernel subprocess speaking the same start/poll protocol; RPC timeouts in the shell; environment profiles materialized as per-profile virtual environments built from reviewed requirement lists, with offline packs as explicit, signed-off wheel folders.
- **Effort/risk.** L / high (Windows process management, packaging). **Why here.** It is the safety base for debugging (stage 5) and dependency repair (33), and fixes the UI freeze the honest review found.

### Stage 5: debugging and notebook state

Dependency map (41), stale outputs (42), conservative re-run (43), variable history (45), watches (46), failure replay bundles (47), clean-run diagnostics (48), breakpoints (44, needs stage 4).

### Stage 6: deeper comparison and measurement

Repeated benchmarks with medians (75), cell waterfall (72), local resource timeline (73, PC side), distributions (64), checkpoints and first divergence (66), cause classification (67), row tracing (68), explanations (69), comparison history (70), regression budgets (76), files and artifacts (58), mutation tests (57 beyond stage 2), scale experiments (78), tradeoff charts (79).

### Stage 7: authoring and exploration

Recipes (4), imported-notebook onboarding (5), typed parameter forms (6), synthetic data (7), bundles (8), walkthroughs (9), templates (10), translation (14), visual builder (15), suspicious-logic checks (17), shared components (19), backend filtering (21), exploration to code (22), join preview (23), profiling for notebooks (24), keys (25), schema timeline (26), linked charts (27), saved views (29), annotations (30).

### Stage 8: help from the developer's own AI tools

Items 11, 12 (AI part), 13, 16, 18, 20, 50. Fabric Desktop by KratosBI is AI-ready, not AI-dependent (Chris, 2026-10-17): no embedded chatbot or model, no AI provider integrations and no AI API keys. These items come from the developer's own tools (Claude, Codex, Copilot, Cursor and others) through the MCP server and local API that the "Bring your own AI integration" program builds. This plan's part is to register Prove capabilities with that program as plain capabilities, not AI features: checking and starting a proof, its status, the verified comparison, receipts, cell dependencies, compatibility and environment checks, and side by side. Every call keeps the existing safety checks (the side-by-side write confirmation, proof sandboxes, kernel execution controls), because they live in the engine services the adapters call. Capability shapes are agreed with that program through the project coordinator. Without an AI tool, every feature still works.

### Stage 9: production and collaboration

Notebook test suites (85), schedule previews (87), experiment matrices (88), notebook apps (89), lifecycle (90), shared baselines (84), review threads (83). 82 and 86 build on the sister plan's Git and release work.

### Stage 10: showcase

Experiment dashboard (91), presentation mode (92), domain kits (94), explain-this-result (95), what-if controls (96), data product handoff (97, Dep: roadmap lineage), reports (98), reproduction packages (99).

## 4. Backlog

Stage numbers refer to section 3. "Accept" is the acceptance criterion; "Check" is how it is validated (U unit, I integration with local Delta, M mocked Fabric, L live tenant, UI rendered UI).

### Notebook creation

| # | Capability | Status | Stage | Accept | Check | PR |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Outcome-driven creation wizard | Missing (Create > Notebook makes an empty notebook) | 3 | Wizard output runs and passes its own checks on the chosen data | U, I, UI | |
| 2 | Schema-aware starters from tables | Enhance (Explorer click inserts a read) | 3 | Starter reads each selected table with typed columns listed | U, UI | |
| 3 | Drag tables/columns/files into cells | Enhance (click to insert) | 7 | Drop inserts the same code click does, at the cursor | UI | |
| 4 | Recipe gallery | Missing | 7 | Searchable; each recipe inserts runnable code | U, UI | |
| 5 | Imported-notebook onboarding | Enhance (compute_check) | 7 | Import lists dependencies, bindings, compatibility per cell | U | |
| 6 | Typed parameter forms | Enhance (parameter cell tag) | 7 (contract types in 3) | Types validated before run; saved configurations | U, UI | |
| 7 | Synthetic datasets | Missing | 7 | Size, nulls, duplicates, bad rows controllable; seeded | U | |
| 8 | Project bundles | Missing | 7 | One action creates linked notebooks that run in order | I | |
| 9 | Guided walkthroughs | Missing | 7 | Steps check real state (table exists, check passed) | UI | |
| 10 | Versioned team templates | Missing | 7 (Dep: roadmap Git) | Template version recorded; upgrade notice shown | U | |

### Intelligent authoring

| # | Capability | Status | Stage | Accept | Check | PR |
| --- | --- | --- | --- | --- | --- | --- |
| 11 | Describe-to-build assistant | External AI tool (MCP) | 8 | The tool sees the plan before any change; changes arrive as a reviewed diff | U, UI | |
| 12 | Inline completion | Enhance (namespace + table names) | 7 / 8 | Completes columns of attached tables without AI | U | |
| 13 | Explain selected code | External AI tool (MCP) | 8 | The dependency analysis (side effects, reads, writes) is available to the tool as a capability | U | |
| 14 | Translation between engines | Enhance (Spark SQL → DuckDB via sqlglot) | 7 | Unsupported constructs flagged, never silently changed | U | |
| 15 | Visual transformation builder | Missing | 7 | Generated code visible and editable | UI | |
| 16 | Notebook-wide refactoring | External AI tool (MCP) for the AI part | 8 | Reviewed diff preview | U | |
| 17 | Suspicious logic detection | Missing | 7 | Join fan-out, filter-after-join on nulls flagged with evidence | U | |
| 18 | Test generation | External AI tool (MCP) | 8 | Generated tests from contracts runnable without AI | U | |
| 19 | Shared components | Missing | 7 | Inserted component versioned; update notice | U | |
| 20 | Explicit AI context controls | Owned by the AI integration program (permissions and approvals) | 8 | Nothing reaches a tool without the user's approval | U | |

### Data exploration

| # | Capability | Status | Stage | Accept | Check | PR |
| --- | --- | --- | --- | --- | --- | --- |
| 21 | Backend filtering beyond samples | Missing (1,000-row preview) | 7 | Filter runs on the engine; scope label says rows scanned | U, UI | |
| 22 | Exploration to code | Missing | 7 | Each action appends equivalent code | U | |
| 23 | Join preview | Missing | 7 | Unmatched keys, duplicate matches, multiplication shown | U | |
| 24 | Async profiling with provenance | Enhance (metadata profiling) | 7 | Scope and Delta version on every profile | U | |
| 25 | Suggested relationships | Missing | 7 | Confidence and validation counts | U | |
| 26 | Schema evolution timeline | Enhance (drift review for entities) | 7 | Per-table timeline linked to receipts | U | |
| 27 | Linked charts | Missing | 7 | Cross-filter between a chart and its table | UI | |
| 28 | Spatial/temporal visualizations | Not now | – | Matplotlib/Plotly already render in cells, and Power BI is where these teams build maps; a built-in spatial layer adds a large surface for little proof value | – | |
| 29 | Saved result views | Missing | 7 | A view reopens against a later run's receipt | U | |
| 30 | Annotations | Missing | 7 | Annotation tied to table version/column/key | U | |

### Local and Fabric execution

| # | Capability | Status | Stage | Accept | Check | PR |
| --- | --- | --- | --- | --- | --- | --- |
| 31 | Unified Run planner | Missing | 3 (proof plan), 7 (general) | Shows destination, identity, inputs, outputs, environment, unresolved items before running | U, UI | |
| 32 | Cell/line compatibility with unknown | Enhance (compute_check, no "unknown") | 2 | Each cell supported / unsupported / unknown with the line | U | |
| 33 | Reviewed environment repair | Missing | 4 | Proposed package change shown and applied only on approval | U | |
| 34 | Reproducible environment profiles | Missing | 1 (record), 4 (materialize) | Profile captured in every receipt; restorable on PC | U | |
| 35 | Destination recommendations | Missing | 6 | Recommendation cites the evidence it used | U | |
| 36 | Offline development packs | Missing | 4 | Only explicitly chosen, permitted packages; verified hashes | U | |
| 37 | Environment-specific bindings | Missing | 7 | Dev/Test/Prod resolve names to lakehouses without code edits | U | |
| 38 | Selected-section Fabric execution | Missing | 6 | Prerequisites packaged explicitly; boundary shown | M | |
| 39 | Remote logs and progress | Enhance (job status, link to Fabric) | 6 | Shows what the job API exposes; no promise beyond it | M, L | |
| 40 | Hybrid execution | Not now | – | Needs 38 and a trust model for handing data between engines; the proof sandbox already makes handoffs explicit. Revisit after stage 6 | – | |

### Debugging and state

| # | Capability | Status | Stage | Accept | Check | PR |
| --- | --- | --- | --- | --- | --- | --- |
| 41 | Cell dependency map | Missing | 5 | Detected vs declared edges distinguished | U | |
| 42 | Stale-output indicators | Missing | 5 | Output marked stale when code/params/inputs change | U, UI | |
| 43 | Conservative re-run | Missing | 5 | Never re-runs side-effecting cells without asking | U | |
| 44 | Breakpoints and stepping | Missing | 5 (after 4) | Works in an isolated kernel only | I | |
| 45 | Variable history | Missing | 5 | Per-cell snapshots of names, types, sizes | U | |
| 46 | DataFrame watches | Missing | 5 | Watched aggregate re-evaluated after each cell | U | |
| 47 | Failure replay bundles | Missing | 5 | Bundle holds code, params, input pins, environment; no data unless permitted | U | |
| 48 | Hidden-state detection | Missing | 5 | Reports names used but defined only by deleted/edited cells | U | |
| 49 | Process-isolated sessions | Enhance (threads in the engine; Stop kills the engine) | 4 | Cancel/terminate one notebook without affecting others | I | |
| 50 | Failure explanations and repairs | External AI tool (MCP) for repairs | 8 | Proposed repair as a diff plus a focused re-run | U | |

### Trustworthy comparisons

| # | Capability | Status | Stage | Accept | Check | PR |
| --- | --- | --- | --- | --- | --- | --- |
| 51 | Separate output destinations | Missing (writes shared tables twice) | 2 | Shared table versions unchanged by a proof | I, M | |
| 52 | Equivalent starting inputs | Missing | 2 | Both sides read the same Delta versions; file hashes match | I, M | |
| 53 | Seeds, time, timezone | Missing | 2 | Seed, clock and timezone fixed and recorded; `now()` reported as nondeterministic | U | |
| 54 | Environment differences in results | Missing | 2 | Python and package versions of both sides in the receipt | M, L | |
| 55 | Configurable equality rules | Enhance (fixed 10-digit rounding) | 2 | Tolerance, ignored columns, order, timestamp precision | U | |
| 56 | Business-key matching | Missing | 2 | Duplicates, missing, changed reported separately | U | |
| 57 | Append/merge/update/delete from equal state | Missing (Fabric starts from PC's result) | 2 | Each mode proven from the same pinned start | I, M | |
| 58 | File and artifact comparisons | Missing | 6 | Files written under Files/ compared by hash/content | U | |
| 59 | Detect external writes / changed inputs | Missing | 2 | Changed shared tables or file hashes make the result inconclusive | I | |
| 60 | Explicit verification levels | Missing | 2 | Every table result names its level and limits | U | |

### Understanding differences

| # | Capability | Status | Stage | Accept | Check | PR |
| --- | --- | --- | --- | --- | --- | --- |
| 61 | Aligned result grids | Missing | 2 (engine), 3 (UI) | Missing rows and changed cells highlighted, paged | U, UI | |
| 62 | Column difference heatmap | Missing | 2 (counts), 3 (UI) | Per-column differing-row counts | U, UI | |
| 63 | Numeric error and tolerance exploration | Missing | 2, 3 | Max abs/rel error per column; re-verify at a new tolerance without re-running | U, UI | |
| 64 | Distribution comparisons | Missing | 6 | Nulls, distinct counts, quantiles per side | U | |
| 65 | Reconciliation scorecards | Missing | 3 | Sums of declared measures side by side | U | |
| 66 | First-divergence detection | Missing | 6 | Checkpoints per cell; first differing checkpoint named | I | |
| 67 | Likely-cause classification | Missing | 6 | Each cause cites evidence (type, rounding, order, nulls) | U | |
| 68 | Output-to-input tracing | Missing | 6 | Supported for key-preserving transforms only, labeled | U | |
| 69 | Readable explanations | Missing | 3 (text from evidence), 6 | Every sentence backed by a measured fact | U | |
| 70 | Comparison history | Missing | 6 | Regressions linked to code/environment/input change from receipts | U | |

### Performance and value

| # | Capability | Status | Stage | Accept | Check | PR |
| --- | --- | --- | --- | --- | --- | --- |
| 71 | Run scorecard | Enhance (time and CU estimate) | 2, 3 | Correctness, duration, startup, CPU, memory, cost assumptions | U, UI | |
| 72 | Cell execution waterfall | Missing | 6 | Read/compute/write split where instrumented | U | |
| 73 | Resource timelines with Fabric metrics | Enhance (PC); Blocked (Fabric: no per-job metrics API) | 6 | PC CPU/memory sampled; Fabric shows only job timings | U | |
| 74 | Honest cost comparisons | Enhance (labeled estimate) | 2, 6 | Measured vs estimated vs marginal vs allocated separated | U | |
| 75 | Repeated benchmarks | Enhance (single runs) | 6 | Medians, spread, failures, warm/cold labeled | U | |
| 76 | Regression budgets | Missing | 6 | Budget breach flagged from receipts | U | |
| 77 | Optimization experiments | Missing | 6 | Variant must match baseline output before timing counts | U | |
| 78 | Scale experiments | Missing | 6 (Dep: roadmap large-table) | Observations vs extrapolations labeled | U | |
| 79 | Tradeoff charts | Missing | 6 | Runtime, resources, cost per run plotted from receipts | UI | |
| 80 | Team value reporting | Missing | 9 | No double counting across receipts | U | |

### Collaboration and production

| # | Capability | Status | Stage | Accept | Check | PR |
| --- | --- | --- | --- | --- | --- | --- |
| 81 | Immutable run receipts | Missing | 1 (schema), 2 (written by proofs) | Content-addressed; tamper detected; exportable | U | |
| 82 | Notebook-aware diffs | Missing (Dep: roadmap Git) | 9 | Cell-level diff by stable id | U | |
| 83 | Review discussions on cells | Missing | 9 | Comments anchored to cell id + revision | U | |
| 84 | Shared benchmark baselines | Missing | 9 | Baseline is a receipt others can verify | U | |
| 85 | Notebook test suites | Enhance (metadata tests only) | 9 | Contract checks run locally and in CI | I | |
| 86 | Promotion evidence packages | Missing (Dep: roadmap release) | 9 | Package = receipts + verification | U | |
| 87 | Schedule previews | Enhance (schedules exist) | 9 | Exact revision, parameters, overlap shown | U | |
| 88 | Experiment matrices | Missing | 9 | Concurrency limit enforced | U | |
| 89 | Notebook apps | Missing | 9 | Only approved notebooks; typed parameters | UI | |
| 90 | Lifecycle | Missing | 9 | Owner, certification, deprecation in metadata | U | |

### Showcase and adoption

| # | Capability | Status | Stage | Accept | Check | PR |
| --- | --- | --- | --- | --- | --- | --- |
| 91 | Live experiment dashboard | Enhance (Side by side steps panel) | 3 | Stages aligned; sequential runs shown as sequential | UI | |
| 92 | Presentation mode | Missing | 10 | Built from receipts and outputs | UI | |
| 93 | Optimization challenges | Not now | – | Gamified challenges need a shared leaderboard service and moderation; shared baselines (84) give teams the same comparison without it | – | |
| 94 | Domain demonstration kits | Missing | 10 | Synthetic data only, labeled | U | |
| 95 | Explain this result | Missing | 10 | Links output to receipt inputs and formula cells | UI | |
| 96 | What-if controls | Missing | 10 | Recompute boundary explicit | UI | |
| 97 | Data product handoff | Missing (Dep: roadmap lineage) | 10 | Validated output + receipt registered as a product | U | |
| 98 | Exportable reports | Missing | 1 (render), 3 (UI) | Methods, limits, charts; redaction selectable | U | |
| 99 | Reproduce on another machine | Missing | 10 | Package validated before run; differences reported | I | |
| 100 | Prove this notebook | Enhance (Side by side) | 2, 3 | Guided run → verify → explain → receipt, end to end | I, M, UI, L | |

## 5. Live verification needed (Chris's tenant)

Nothing below has run on a tenant. A minimal check for stages 1–3, about 20 minutes, on a workspace with a lakehouse nobody else writes to:

1. Attach the lakehouse to a new notebook made by the wizard from one small table (for example 1,000 rows).
2. Select **Prove** and confirm the plan lists the table at its current version and the outputs under `Files/_fabric_desktop/proofs/`.
3. Run it. In Fabric's Monitoring hub, confirm one notebook run in the notebook's workspace.
4. In the lakehouse, confirm no table under `Tables/` changed version (Fabric's table history) and that the proof folder holds a `pc` and a `fabric` folder.
5. Confirm the result reports both Python versions and the Polars/DuckDB/deltalake versions seen in Fabric.
6. Change one row of the input table in Fabric during a second proof's Fabric phase; confirm the result is inconclusive and names the table.
7. Export the report and check that the user and workspace names are hidden when redaction is on.

## 6. Risks and open questions

- `RunNotebook` with a Python notebook and the `/lakehouse/default` mount are unverified (shared with Side by side).
- Delta time travel to the pinned version fails if the table was vacuumed past it during the proof; this is detected and reported, not prevented.
- Spark notebooks in Fabric use Fabric's own `spark`, which the proof runtime can't redirect; proofs support notebooks whose reads and writes go through the lakehouse module (Python notebooks). Spark support needs a Spark-side adapter (stage 6).
- Notebook code runs with the user's own tokens; a proof contains what goes through supported APIs and reports what it can detect, but can't stop arbitrary code from writing elsewhere until kernels are isolated with scoped credentials (stage 4 and later).
