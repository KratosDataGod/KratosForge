# Academy: delivery plan

## Ownership

Each workstream is a thread in the KratosForge project. New threads are started by the project
coordinator; until one exists, its stories stay with the Academy lead thread.

| Workstream | Thread | Stories | Works with |
| --- | --- | --- | --- |
| Academy lead: registry, format, evidence, readiness, screens, change detection | Build KratosForge Academy for DP-700 (this plan) | ACD-001, 002, 003, 006, 007, 008, 015, 020 | UX (mockups), Platform skins (tokens), Learn curriculum and ranks |
| Academy content | New thread: DP-700 content authoring | ACD-009, 010, 016, 019 (projects) | Learn curriculum thread (reuse LM and LAB content), Data Factory and Orchestration (pipeline exercises) |
| Grading sandbox | New thread: Academy grading and sandbox | ACD-004, 005, 011, 012 | Bug fix and security QC (Security read), Notebook and Local data engine threads |
| Exam engine | New thread: Academy exam engine and question bank | ACD-013 | UX (mockup), Enterprise readiness (accessibility) |
| Fabric verification | Later thread, after Phase 2 | ACD-014 | Warehouse, Fabric workstream, Security |
| Extensibility and instructor view | Later | ACD-017, 018 | Lead Databricks and Snowflake parity |

The directive's agent roles map onto existing threads: Product Manager (Product check), Notebook
(Spark functions locally, Local data engine), Data Engineering (Local TPC-H, KUDP), Orchestration
(Data Factory, Orchestrator), UX (UX), Quality (Second quality reviewer), Release Manager (Release
management).

**Merge flow.** Owner, then coordinator, then Second quality, then Release management. Only Release
management merges. A Security read (Bug fix and security QC) is required before merge for learner
code execution and sandboxing (ACD-004, 005, 011, 012), anything that holds grading material, and
Fabric setup and teardown (ACD-014). Exercise content PRs go straight to Second quality and then
Release management. New screens wait for a mockup Chris has reacted to; their PRs stay draft until then.

**Limits.** At most two Academy PRs at review per workstream, one PR per story or content batch,
untested features labelled Preview.

## PR dependency graph

```
PR-A  docs: Academy plan (this folder)
PR-B  ACD-001 registry + DP-700 pack ─┬─ PR-C  ACD-002/003 exercise format + classes + first 3 exercises (E21, E36, E37)
                                      │       ├─ PR-D  ACD-006 evidence + readiness engine + RPC
                                      │       │      └─ PR-G  ACD-007/008 screens (after mockup) ── PR-J ACD-015 dashboard
                                      │       ├─ PR-E  ACD-004 validators ─┬─ content batches ACD-009 (domain 2, then 1, then 3)
                                      │       │                           └─ PR-H ACD-012 pipeline_lab
                                      │       └─ PR-F  ACD-005 python_lab sandbox ── PR-I ACD-011 ── ACD-010 broken environments
                                      ├─ PR-K  ACD-013 exam engine + question format ── question bank batches
                                      └─ PR-L  ACD-020 change detection workflow
Later: ACD-014 (Fabric verified), ACD-016, ACD-019, ACD-017, ACD-018
```

PR-B and PR-K can run in parallel; PR-E and PR-F can run in parallel once PR-C lands.

## First implementation PRs

The smallest end-to-end learner workflow: open the DP-700 path, pick objective **2.2.9 Handle
duplicate, missing, and late-arriving data**, start exercise **E37 Deduplicate a retried load**
(built on the LAB-04 lab), run it, validate it, see that it is LOCAL VERIFIED and what it doesn't
prove, and see 2.2.9 gain practical evidence in readiness.

| PR | Contents | Review |
| --- | --- | --- |
| PR-A | This plan in `docs/academy/` | Second quality, RM |
| PR-B | ACD-001: `academy/registry.py`, `packs/dp-700/` with all 49 objectives, gap records, tests; no UI, no RPC | Second quality, RM |
| PR-C | ACD-002/003: exercise format, classes, `uses` link to Learn labs; E21, E36, E37 as the first live exercises | Second quality, RM |
| PR-D | ACD-006: evidence rows (rules v4), `readiness.py`, RPC `academy_catalog`, `academy_exercise`, `academy_start`, `academy_validate`, `academy_readiness` (added with tests for the engine call surface) | Second quality, RM |
| Mockup | Academy Home, DP-700 path, Exercise Workspace, readiness; posted for Chris's reaction | Chris, UX |
| PR-G | ACD-007/008 screens, draft until Chris reacts to the mockup | UX, Second quality, RM |

PR-B to PR-D are engine and content only and need no screen; the mockup is made while they're at
review.
