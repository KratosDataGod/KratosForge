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
| Microsoft Fabric | 4 Optimized | 2 Development | 0 Discovered | sign in, list what the platform holds |
| Azure Databricks | 2 Development | 0 Discovered | 0 Discovered | sign in, list what the platform holds |
| Snowflake | 0 Discovered | 0 Discovered | 0 Discovered | sign in, list what the platform holds |
| This PC | 4 Optimized | 4 Optimized | 4 Optimized | checked for an enterprise rollout |

Nothing remote is verified yet: the Fabric live test (`fabric-desktop live-test`), the tenant smoke
test and the Databricks live test are built, and each run needs a test workspace and the owner's go.
Snowflake has connection profiles and Snowflake SQL tried on this PC (see [Engines](engines.md)), but
no sign-in yet, so it stays at level 0.

## What each level rests on

`platforms.manifest("fabric")` (and `"databricks"`, `"snowflake"`, `"local"`) lists, for each area,
the evidence and where it comes from: an engine's own capability claims (see [Engines](engines.md)),
or a feature with the modules that do it and the tests behind it. The manifest also lists each
platform's sign-in methods, engines, SQL targets, notebook formats and, for Fabric, how many pipeline
activity types run on this PC.
