# Product management process

How Fabric Desktop by KratosBI decides what to build, builds it, and decides whether it worked. The
cycle repeats for every development cycle. Several Claude threads and people contribute; one role,
the master product manager, keeps the result coherent.

## Roles

| Role | Who | Owns |
| --- | --- | --- |
| Product owner | Chris | Approving items, decisions only he can make (D1, D2, D-C1..C3), final acceptance |
| Master product manager | The master product thread | The inventory, the consolidated backlog, deduplication, prioritization, architecture alignment, acceptance recommendations |
| Specialist workstreams | Git-native, Enterprise readiness, Staged roadmap, Prove, Workload management, AI integration, SQL, Governance, FinOps, Pipeline execution | Their own plans and ids, their designs, their PRs |
| Implementation threads | Any thread building an approved package | One PR per package, tests, docs |
| Reviewers | A thread or person who didn't build the change | Implementation reviews |

Specialist workstreams keep their own plans. The master product manager does not rewrite them; it
tracks their items in the backlog by id, flags overlaps, and ranks everything on one scale.

## The cycle

| Step | What happens | Who | Output |
| ---: | --- | --- | --- |
| 1 | **Re-take the inventory.** Pull main, run the engine, UI and Rust tests, smoke the engine and CLI, compare docs with code, record the commit. Fold in tenant results reported since the last cycle | Master product | `FEATURE_INVENTORY.json` with a new `assessed` block |
| 2 | **Collect inputs.** Users' issues and Discussions ideas, implementation reviews, tenant smoke results, each workstream's plan changes, competitor changes | Master product, workstreams | Notes in the cycle's thread |
| 3 | **Refresh the competitive matrix.** Re-read each source, update dates and classifications | Master product | `COMPETITIVE_CAPABILITY_MATRIX.md` |
| 4 | **Update the backlog.** Add items for new gaps; deduplicate against every program's plan; route owned work to its program by id | Master product, with each workstream confirming its rows | `PRODUCT_BACKLOG.json` |
| 5 | **Score and prioritize.** Workstreams propose scores for their items; master product normalizes them on one scale; run `product_data.py all` | Master product | `PRIORITIZED_ROADMAP.md` |
| 6 | **Check architecture alignment.** For each P0/P1 item: does it fit the shell-engine-RPC architecture, the AI principle (no embedded AI), the no-app-registration rule, the Preview rule, the rail limit, OneLake safety? Conflicts go to the product owner | Master product | Notes on the items; decisions requested |
| 7 | **Product owner review.** Chris approves, defers or rejects items and answers open decisions | Product owner | Items move to Approved, Deferred or Rejected |
| 8 | **Write packages.** Every Approved item without a program spec gets a PR-ready package; programs write their own specs in their own format | Master product, workstreams | `docs/product/pr-ready/PB-nnn.md` |
| 9 | **Build.** One thread per package; draft PR; CI green; mockup first for new UI | Implementation threads | Pull requests |
| 10 | **Review and accept.** Implementation review on each PR; product owner decides the outcome; inventory and backlog updated with the evidence | Reviewers, master product, product owner | `docs/product/reviews/*.md`, Accepted items |

Then the next cycle starts at step 1.

## Rules that hold in every step

- **Evidence before claims.** Every status, verification and score points at something observed or
  read: a test that ran, a smoke run, a file and line, a doc, or a named person's report. Inference is
  labelled as inference.
- **Observed, documented, assumed: keep them apart.** Inventory evidence kinds (`test`, `smoke`,
  `code`, `doc`, `report`, `absent`) make the difference visible.
- **No local-equals-Fabric claims** without a passing proof or a tenant test for that workload.
- **One requirement, one home.** Before adding an item, search every program's plan. Git
  functionality lives only in the Git-native program; AI capabilities only in the AI integration
  program, provider-neutral.
- **Mandatory work isn't ranked down.** Security, reliability and compliance blockers are P0; other
  required items are at least P1.
- **Merged is not accepted.** Only an implementation review followed by the product owner's decision
  moves an item to Accepted.
- **No production changes to establish process.** Process work is docs and data only.
- **No cloud resources or costly experiments** without the product owner's specific OK. Tenant tests
  run on the product owner's non-production workspace.

## How Claude threads contribute

- The **master product thread** runs steps 1 to 6, 8 and the recommendation in 10, and posts a short
  status to the product owner at steps 5 and 7.
- **Workstream threads** keep their own plans current and, when their plan changes, tell the master
  product thread through the project coordinator so the backlog row can be updated. They don't edit
  `PRODUCT_BACKLOG.json` rows owned by other programs.
- **Implementation threads** link the backlog id in the PR, update the feature rows they change, and
  run `python docs/product/tools/product_data.py all` before asking for review.
- **Reviewer threads** use [the review template](reviews/TEMPLATE.md) and never review their own work.

## Cadence

- Steps 1 to 7: once per development cycle, or when the product owner asks for a re-plan.
- Steps 8 to 10: continuously, as items are approved and PRs merge.
- The tenant smoke test (PB-002) re-runs before each Stable promotion.

## Files

| File | Purpose |
| --- | --- |
| [FEATURE_INVENTORY.md](FEATURE_INVENTORY.md) and `.json` | What exists, how sure we are |
| [PRODUCT_BACKLOG.md](PRODUCT_BACKLOG.md) and `.json` | Everything worth doing, with owners |
| [PRIORITIZED_ROADMAP.md](PRIORITIZED_ROADMAP.md) | The scoring model and ranking |
| [pr-ready/](pr-ready/README.md) | Implementation packages and their sequence |
| [IMPLEMENTATION_REVIEW_PROCESS.md](IMPLEMENTATION_REVIEW_PROCESS.md) | How work is accepted |
| [COMPETITIVE_CAPABILITY_MATRIX.md](COMPETITIVE_CAPABILITY_MATRIX.md) | Market context |
| [schema/](schema/feature-inventory.schema.json) and [tools/product_data.py](https://github.com/KratosDataGod/fabric-desktop/blob/main/docs/product/tools/product_data.py) | Validation, scoring and rendering |
