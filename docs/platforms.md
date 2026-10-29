# Platform support levels

KratosForge works with Microsoft Fabric, Azure Databricks and Snowflake, and runs work on this PC.
This page says how far each one is supported, and what evidence that rests on. It is generated from
the code (`fabric_desktop/platforms.py`), and a test keeps it equal to the code, so it can't claim more
than the app does.

## Levels

| Level | Name | What works |
| --- | --- | --- |
| 0 | Discovered | Known; nothing integrated yet. |
| 1 | Connected | Sign in, and list what the platform holds. |
| 2 | Development | Level 1, and run work. |
| 3 | Integrated | Level 2, and compare or validate results, and publish or deploy. |
| 4 | Optimized | Level 3, and measure usage and cost. |
| 5 | Enterprise ready | Level 4, and security, reliability and lifecycle checked for an enterprise rollout. |

Each level is worked out three times, from three strengths of evidence:

- **Built**: the code does it, with automated tests in this repository. Remote platforms are tested
  against fakes of their APIs.
- **In use**: it has been used against the real platform in normal work, without a recorded test.
- **Verified**: a recorded test against the real platform passed. On this PC the tests run the real
  engines, so tests count as verified there.

Planned work never counts toward a level.

## Where each platform stands

<!-- Generated from platforms.docs_table(); a test keeps this table equal to the code. -->
Manifest 1.

| Platform | Built | In use | Verified | To verify the next level |
| --- | --- | --- | --- | --- |
| Microsoft Fabric | 4 Optimized | 2 Development | 2 Development | publish or deploy |
| Azure Databricks | 2 Development | 0 Discovered | 0 Discovered | sign in, list what the platform holds |
| Snowflake | 0 Discovered | 0 Discovered | 0 Discovered | sign in, list what the platform holds |
| This PC | 4 Optimized | 4 Optimized | 4 Optimized | checked for an enterprise rollout |

Fabric is verified to level 2 by one recorded live run (below). Level 3 also needs publishing
or deploying verified live. The Fabric live test (`fabric-desktop live-test`), the tenant smoke
test and the Databricks live test are built, and each run needs a test workspace and the owner's go.
Snowflake has connection profiles and Snowflake SQL tried on this PC (see [Engines](engines.md)), but
no sign-in yet, so it stays at level 0.

## Live runs

Each live run that counts as evidence is recorded here: when, where, and what it showed.

**2026-10-29, the KUDP comparison.** Approved by the owner for a throwaway test workspace on a
Trial capacity. It ran 10 small Spark SQL queries, with inline data, on DuckDB on this PC (translated
by sqlglot) and in one Fabric Spark session, and compared the answers with `engines/compare.py`. The
session ran for 23 seconds and was closed, and nothing was written to the lakehouse.

| Query | Result |
| --- | --- |
| Literals, GROUP BY, integer division, dates, strings, NULLs, a window, `explode(sequence())` | Match |
| Decimal division (`CAST(1 AS DECIMAL(10,2)) / 3`) | Differs: Spark keeps 6 decimal places, DuckDB more. A real difference between the engines. |
| `date_trunc` on a Spark `TIMESTAMP` literal | Differs: a local bug. The translation made it a time-zone timestamp in the PC's zone, so it was 6 hours off. Being fixed. |

On this PC each query took about 2 ms. On Fabric Spark each took about 1.4 s, after 5 s to start the
session. That is run time only, not cost.

It verifies, for Fabric: signing in, listing workspaces and lakehouses, running SQL in a Spark
session, and comparing an answer from this PC with Fabric's.

## What each level rests on

`platforms.manifest("fabric")` (and `"databricks"`, `"snowflake"`, `"local"`) lists, for each area,
the evidence and where it comes from: an engine's own capability claims (see [Engines](engines.md)),
or a feature with the modules that do it and the tests behind it. The manifest also lists each
platform's sign-in methods, engines, SQL targets, notebook formats and, for Fabric, how many pipeline
activity types run on this PC.
