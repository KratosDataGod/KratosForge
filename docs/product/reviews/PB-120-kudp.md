---
site_group: Internal engineering
---

# Implementation review: PB-120 Platform support levels and one engine contract (FEAT-EXT-05, FEAT-SQL-03)

> **Internal — engineering working material.** This is a dated implementation, design, or planning record. Use the user guides for current instructions and check releases for shipped behavior.

| Field | Value |
| --- | --- |
| Backlog item | PB-120 Platform support levels and one engine contract (KUDP-01, 02, 05 and 06) |
| Pull request | #1078 (KUDP-01), #1115 (KUDP-02), #1118 (KUDP-05), #1124 and #1125 (KUDP-06), the KUDP live runs (#1142, #1760), and the Databricks and Snowflake function work listed on the item, all merged; main at bddb5b27 |
| Package | none |
| Reviewer | Product management thread, which didn't write these PRs |
| Date | 2026-11-09 |
| Environment | Linux container, with the engine from source in a Python 3.12 venv. No Fabric, Databricks or Snowflake account was used, and no Windows app |

## Outcome

**Accepted with Documented Limitations**. The four criteria are met. Each engine adapter
passes one contract check. Each platform has a manifest whose levels come from evidence. SQL gets a
portability report per statement, and one query can be compared on two engines. The limits are about
how far it is proven: only Fabric has live evidence (level 2), Databricks and Snowflake are verified
at level 0, and the portability report and comparison have no screen yet.

Recommended by: Product management thread. Decided by (product owner): Chris, 'Accept with limits' on the decision card on 2026-11-10 (project message cmsg_018T4o2pQdpbLBNhHpgVNNGpYRYGUPQ2mE64P6mQ1JuT64).

## Acceptance criteria

The item's own line says the criteria are "owned and specified by the unified platform program
(KUDP)". These are the acceptance lines of the four KUDP stories the item's capability names, copied
from the KUDP proposal Chris approved on 2026-10-29. KUDP-03 (one run record) never had a go and isn't
part of this item's capability.

| # | Criterion (from the KUDP stories) | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | KUDP-01: the kit passes on every adapter; a deliberately overclaiming fake adapter fails it; the "every adapter is covered" test fails when an adapter is registered without a run | Met | `tests/test_engine_conformance.py`, 86 passed: `test_every_adapter_meets_the_contract` runs each check on each adapter, remote ones against their fakes; `test_an_adapter_that_overclaims_fails_the_checks`; `test_every_adapter_has_a_harness`. Also fails an engine that cuts a result and a remote engine that sends a refused cell. docs/engines.md says a new adapter must add a harness |
| 2 | KUDP-02: a platform can't reach a level its evidence doesn't support; the docs table matches the code; Fabric verified shows level 2, not 4 | Met | `tests/test_platforms.py`, 13 passed: `test_levels_today`, `test_a_level_needs_every_area_it_lists`, `test_verified_never_exceeds_in_use_and_in_use_never_exceeds_built`, `test_live_evidence_lifts_only_the_strength_it_meets`, `test_the_docs_table_is_the_codes`. docs/platforms.md: Fabric built 4, verified 2; Databricks built 2, verified 0; Snowflake 0 |
| 3 | KUDP-05: the Snowflake probe statements and a Fabric sample are graded as expected; every report says it is static analysis; it never rewrites the user's SQL | Met | `tests/test_sql_portability.py`, 73 passed: every Snowflake probe statement follows its label, Snowflake-only constructs are unsupported elsewhere, Fabric targets use the sqlcompat rules, unknown functions are "unverified" and never "compatible", a rewrite that doesn't read back isn't trusted, `test_every_report_says_it_is_static_and_lists_the_grades`. The rewrite is shown, never applied (`sql_portability.GRADES`) |
| 4 | KUDP-06: two engines' answers are compared and a planted difference is found; nothing remote runs without its confirm; each side is reported on its own | Met | `tests/test_engine_compare.py`, 21 passed: `test_duckdb_and_sqlite_agree`, `test_a_difference_is_found`, `test_a_remote_result_needs_the_callers_confirm`, DuckDB against Fabric Spark and a Databricks SQL warehouse on fakes (each refused until the copy is confirmed, nothing sent before). The comparison is verify.compare, Prove's comparer, whose null and float rules are tested in `tests/test_prove.py`. Each side keeps its own rows and seconds; nothing is summed. Live: the 2026-11-03 run matched 9 of 10 queries and reported the decimal division difference (docs/platforms.md, Live runs) |

## Function checks

Dbx parity reported the Databricks function checks done. docs/databricks-functions.md (generated,
CI-checked by `tests/test_databricks_coverage.py`, 366 passed here) at main 013281be:

| Status | Databricks SQL | Snowflake SQL (docs/snowflake-functions.md) |
| --- | ---: | ---: |
| Runs | 440 | 167 |
| Differs | 0 | 20 |
| Refused | 92 | 81 |
| Fails | 0 | 0 |
| Not checked | 57 | 227 |
| All | 589 | 495 |

Every one of the 92 Databricks refusals has its reason in the table. The 57 not checked are the
clock, random, session or user functions, the type-cast aliases (`int`, `string`, ...) and operators
with no reference example. Each value is checked against one example from the documentation, on this
PC, without a Databricks workspace: a first check, as the page says.

## Checks

| Check | Result | Evidence or note |
| --- | --- | --- |
| Scope matches the item | Yes | KUDP-01, 02, 05 and 06 as the capability says; the Databricks and Snowflake PRs on the item add engines and functions that pass the same kit |
| Full suites pass | Partly | Here: test_engine_conformance, test_platforms, test_sql_portability, test_engine_compare, test_databricks_sql_parity and test_engines, 467 passed; the Snowflake coverage and SQL tests, 571 passed; test_databricks_coverage, 366 passed; engine-picker browser test, 4 passed. The full suites run in main CI |
| Reachable where docs say; Preview behavior right | Yes, with a limit | Engines are picked from the notebook's engine picker (Preview). The platform levels are on docs/platforms.md. The portability report and the two-engine comparison are engine code only: no screen or RPC calls them yet, and the docs describe them as code (docs/sql-compatibility.md, docs/engines.md) |
| OneLake data safety | Yes | Nothing writes. A remote result is copied onto this PC only after a confirm made for that copy; both answers live in memory and go with the report |
| Authentication | Yes | Remote sessions need their authorization to open; the comparison refuses a remote side without the copy confirm, before anything runs |
| No unproven local-equals-platform claim | Yes | The levels can't exceed their evidence (test 2), "compatible" is said to mean no known problem, not the same results, and the live decimal difference is reported, never hidden |
| Docs match the code; strict docs build passes | Yes | docs/platforms.md is generated and kept equal by a test; docs/databricks-functions.md and docs/snowflake-functions.md are generated and CI-checked |
| Accessibility | Not applicable | No new controls |

## Findings

| # | Severity | Finding | Where | Status |
| ---: | --- | --- | --- | --- |
| 1 | Note | Two known Databricks follow-ups: `listagg` with a BINARY delimiter, and `time()` of a string with an offset | Dbx parity thread | With Dbx parity |
| 2 | Note | The item's acceptance line pointed at the KUDP program instead of saying what to check; it now lists the four KUDP acceptance lines | PRODUCT_BACKLOG.json | Fixed with this review |

None blocking.

## Documented limitations

| Limitation | Where users are told | Backlog item that tracks it |
| --- | --- | --- |
| Only Fabric has live evidence, to level 2; Databricks and Snowflake are verified at level 0, because no recorded run has used a workspace or account | docs/platforms.md, "Where each platform stands" | PB-002 (tenant smoke test) for Fabric; a Databricks live run needs a test workspace and Chris's typed yes |
| The portability report and the two-engine comparison have no screen; they run from engine code only | docs/sql-compatibility.md, docs/engines.md | none yet: a screen needs a mockup and a go |
| Function checks use one documented example per function on this PC; Snowflake has 20 that differ and 227 not checked | docs/databricks-functions.md, docs/snowflake-functions.md | this item's Databricks and Snowflake follow-ups |

## Inventory and backlog changes

- PB-120: In progress → In review. Acceptance lists the four KUDP lines; evidence links this review.
- PB-120: In review → Accepted, 2026-11-10.
- No feature row status changed.
