# Development status

The state of development at the end of the last product management cycle. It's replaced each
cycle, and the history is in Git. The counts come from `python docs/product/tools/product_data.py summary`.

**Product check of 2026-10-28 23:42 UTC, main b19b7456.** Covers the PRs merged from #135 to #972.

## Summary

- 38 PRs merged since the morning check (#828 to #972). Fabric runs cancel from Monitor > Runs and the
  tray (#875, #972); schedules edit in place with every Windows time zone (#881); the agent checks
  shortcuts on a schedule (#902); Check works offline from a hash-checked policy package (#882);
  compares above 5,000,000 rows stream a checksum (#897); Preview waits for the dependency audit (#867)
  and Stable carries its SBOMs (#911); every page has an automated accessibility audit (#907); and
  data product groups (#828) and the policy link listener fix (#843) are in.
- 14 items moved to In review on that work (PB-010, PB-042, PB-043, PB-048, PB-051, PB-053, PB-057,
  PB-069, PB-070, PB-074, PB-077, PB-078, PB-087 and PB-094). PB-042 was already built (#508, #707).
  PB-068 (#913) and PB-019 (#914) merged while this check waited on #997, so they are In review too.
- Two acceptance criteria were changed and need the product owner: PB-049 checks the pipeline run a
  request names, since OneLake reports every writer as $superuser, and PB-072 limits quick calls only,
  since runs can take hours and Stop ends them.
- Backlog items now link their story issues, and the three new stories have ids: PB-116 Fabric Spark
  sessions, PB-117 Snowflake research and PB-118 the Databricks loss report.
- Inventory: 142 features. 119 are Implemented, 13 Partially Implemented, 4 Implemented but Not Exposed, 2 Missing and 4 Planned.
  3 are Verified, 128 Partially Verified and 11 Unverified. None has been verified on a Fabric tenant end to end.
- Backlog: 106 items (5 P0, 31 P1, 54 P2, 16 P3). 19 are Proposed, 27 In progress, 20 In review and 40 Accepted.

## Workstreams

| Workstream | Owner thread | Backlog | State |
| --- | --- | --- | --- |
| Local execution and compute | Local data engine and OneLake publishing | PB-094, PB-068, PB-015 | Kernel measured (#895); key lookups merged (#913); #933 open |
| Fabric, SQL and warehouse | Build full warehouse development experience | PB-002, PB-043, PB-106 | Cancel merged (#875); smoke test #884 waits on a yes |
| Data Factory and pipelines | Data Factory; Local Fabric pipeline execution | PB-107, PB-037 | #799 draft; DF-8 and P5 need a go |
| Semantic models | Best Practice Analyzer; TMDL and OSI conversion | PB-110, PB-112 | BPA live run next |
| Governance and mesh | Enterprise governance foundation; data mesh | PB-057, PB-053, PB-083 | Offline policy check (#882) and product groups (#828) merged; GOV-003 #924 draft |
| Developer experience | UX oversight; action-first; VS Code | PB-078, PB-077, PB-070, PB-063 | Preview tags, docs and command bar tests merged; accessibility audit part 1 (#907) |
| Testing and security | Codebase audit; engineering quality | PB-074, PB-072, PB-051, PB-101 | Audit gate (#867), 401 refresh (#923) and SBOMs (#911) merged; #898 open |
| Databricks and Snowflake | Fabric and Databricks unified development | PB-116, PB-117, PB-118 | Livy draft #891; Snowflake engine and SQL merged as Preview (#993, #995) |
| Release | Release management | | Working |

## Open pull requests

At 23:50 UTC there were 56 open PRs, 31 of them drafts. They include the stories in the work queues
(#884 PB-002, #891 PB-116, #898 PB-072, #913 PB-068, #924 GOV-003, #931 PB-049 and others), two
Snowflake drafts (#993, #995), this check and #997, and the fork PRs #288, #289 and #290, reviewed
FAIL and recommended to close.

## Blocked on the product owner

Trusted Signing (PB-001) and a non-production workspace for the live harness (PB-002, PB-003) are the
two release gates. Also waiting: acceptance of the 20 In review items and the PB-049 and PB-072 criteria changes, a tenant run of
the Data Factory writes and the BPA run, governance steps 8 and 12, Direct Lake on zstd, and the privacy
and support details.

## Orchestration

Development runs when the product owner or a thread wakes the project coordinator. A product
check twice a day (11:42 and 23:42 UTC) refreshes this data and proposes assignments. Pull request events can reach the thread that opened the PR. Quality review gates every PR,
and only Release management merges.
