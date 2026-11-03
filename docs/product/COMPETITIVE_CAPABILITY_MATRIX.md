# Competitive capability matrix

Where KratosForge stands against the tools Fabric developers already use, and what
that means for the backlog. The aim is not to match every competitor: a capability belongs in the
backlog only when it serves the mission (develop and run data work on the PC instead of Fabric
compute, then create Fabric items and write to OneLake) or the enterprise services built on it.

## How to read it

- **KratosForge** status and verification come from [the feature inventory](FEATURE_INVENTORY.md)
  (main at `b7b04cc`). Partially Verified means tested with Fabric faked; nothing was run on a tenant.
- **Competitor** claims come only from the sources listed at the end, read on 2026-10-17. A claim
  marked *(search result only)* came from a search snippet, not a page that was opened. Nothing was
  installed or tried.
- **Classification:**

| Class | Meaning |
| --- | --- |
| Already available | KratosForge has it at comparable depth |
| Available but incomplete | KratosForge has part of it |
| Available through integration | Better reached through another tool Fabric developers already have; integrate or link, don't rebuild |
| Genuine gap | Missing, and the mission needs it |
| Potential differentiator | KratosForge has, or can uniquely have, something the others don't offer for Fabric |
| Not strategically relevant | Competitors have it, but it is off mission or against a standing decision |

## Matrix

### Local development and execution

| Capability | KratosForge | Who else offers it | Class | Mission fit and decision | Backlog |
| --- | --- | --- | --- | --- | --- |
| Run notebook code on the PC against OneLake tables, with no Fabric job started (OneLake reads and writes still use capacity) | Implemented, Partially Verified (FEAT-NB-02, FEAT-NB-04) | Fabric VS Code extension runs notebooks on remote Fabric Spark [S1]; Databricks runs on clusters via Databricks Connect [S2]; JupyterLab runs locally with no OneLake integration | Potential differentiator | Core of the mission and the main CU saving. Keep the claim honest: the Spark surface is a DuckDB shim, not Spark | PB-012, PB-015 |
| Fabric-compatible Delta writes from the PC | Implemented, Partially Verified (FEAT-ONE-05) | None found in the sources for Fabric | Potential differentiator | Needs live proof before it is marketed (PB-002, PB-003) | PB-002, PB-003 |
| Prove a notebook gives the same result on the PC and in Fabric | Implemented, Partially Verified (FEAT-PRV-01) | None found | Potential differentiator | Unique and on mission; it is also the guard against overclaiming local equivalence | PB-002 |
| Spark API coverage on the PC | Partially Implemented (MERGE, UPDATE and DELETE in Preview; no RDD, UDF, streaming) | Real Spark in Fabric [S1], Databricks [S2] | Available but incomplete | Publish what works (matrix) before widening it | PB-012, PB-015 |
| Notebook and code debugging | Missing (FEAT-EXT-04) | Databricks Connect debugging [S2]; Snowpark debugging in the Snowflake extension [S3]; JupyterLab debugger *(search result only)* [S12] | Genuine gap | Developers expect it on a desktop tool; after the process kernel is default | PB-026 |
| Reproducible environments and Fabric Environment items | Partially (import profile only) / Missing (FEAT-PRV-04, FEAT-FAB-09) | Fabric VS Code extension works with Environments [S1] | Genuine gap | Needed for trustworthy local runs; waits on the package-source decision | PB-016 |
| Run work unattended on your own machines (agent, PC schedules) | Implemented, Partially Verified (FEAT-ORC-01, FEAT-ORC-02) | None found for Fabric | Potential differentiator | Second mission pillar; needs a live run | PB-002, PB-044 |
| Desktop app on macOS and Linux | Windows only (engine and agent also run on Linux) | VS Code, DataGrip, DBeaver, JupyterLab are cross-platform | Genuine gap, not scheduled | A product-owner decision; not added to the backlog because the Windows-first choice is deliberate | Decision |

### SQL development

| Capability | KratosForge | Who else offers it | Class | Mission fit and decision | Backlog |
| --- | --- | --- | --- | --- | --- |
| SQL editor with completion and formatting | Plain textarea (FEAT-SQL-01) | DataGrip [S9], DBeaver [S10], SSMS [S11], VS Code MSSQL [S13], Snowflake extension [S3] | Available but incomplete | Needed for lakehouse SQL on the PC; SQL sprint owns it | PB-021 |
| Cancel, streaming results, query history | No cancel, 1,000-row page (FEAT-SQL-01) | Snowflake extension: cancel and history [S3] | Available but incomplete | SQL sprint owns it | PB-023 |
| Connect to Warehouse, SQL endpoint, Fabric SQL database | Missing (FEAT-FAB-11, planned) | SSMS [S11], VS Code MSSQL browses Fabric SQL DB and endpoints [S13], DBeaver PRO [S10] | Available through integration | Don't rebuild SSMS. Scope SQL-004 to what local development needs (run, compare, check) and link out for administration | PB-024 |
| Object explorer | Lakehouse table tree only | SSMS, DataGrip, Snowflake extension, VS Code MSSQL [S3][S9][S11][S13] | Available through integration | Same as above | PB-025 |
| Compile and check SQL without a warehouse | Spark SQL translated to DuckDB with no compatibility report | dbt Fusion compiles SQL locally and checks it against the warehouse dialect [S4] | Potential differentiator (for the Fabric dialect) | High CU and productivity value; the T-SQL surface area page lists what Fabric refuses [S8] | PB-022, PB-020 |

### Data engineering, governance and deployment

| Capability | KratosForge | Who else offers it | Class | Mission fit and decision | Backlog |
| --- | --- | --- | --- | --- | --- |
| Column-level lineage | Computed, no screen (FEAT-META-17) | dbt Fusion VS Code extension [S4] | Available but incomplete | Finish the view | PB-081 |
| Data tests, quarantine and replay | Implemented (FEAT-META-03, FEAT-GOV-02) | dbt tests on Fabric Warehouse through dbt-fabric [S5] | Already available | dbt-fabric targets Warehouse, not lakehouse Spark SQL [S5]; KratosForge covers lakehouses | none |
| Medallion metadata framework with promote gates | Implemented (FEAT-META-01..09) | dbt models (Warehouse) [S5] | Potential differentiator | Keep; verify promote on a tenant | PB-002 |
| Cross-workspace deployment of Fabric items | Metadata promote only; item deploy planned (FEAT-FAB-13) | fabric-cicd, officially supported, parameterized deploys [S6]; Databricks bundles [S2] | Available through integration | Recommend the Git-native program builds item deployment on fabric-cicd or its file format rather than a new deployer. Recorded as a recommendation to that program, not a new requirement | PB-041 |
| Git source control | Implemented, Phase 1 (FEAT-GIT-01) | VS Code, DataGrip, Databricks workspace sync [S2] | Already available | Owned by the Git-native program | PB-040 |
| Policy validation offline | Planned (GOV-001/002) | None found in the sources | Potential differentiator | Governance foundation owns it | PB-056, PB-057 |
| Local run of Fabric Data pipelines | Planned (ORCH-001..003) | None found; pipelines run in Fabric | Potential differentiator | High CU value, high risk; owned by the pipeline execution thread | PB-035..PB-037 |

### Administration, AI and extensibility

| Capability | KratosForge | Who else offers it | Class | Mission fit and decision | Backlog |
| --- | --- | --- | --- | --- | --- |
| CU cost before and after a run | Estimates only (FEAT-ADM-01) | Microsoft publishes CU rates per notebook type (Python 2 vCores = 1 CU; Spark starter pool 8 CU or more) [S7]; no tool in the sources advises before a run | Potential differentiator | FinOps sprint owns it; every number must say measured or estimated | PB-050, PB-090, PB-017 |
| MCP server for external AI tools | Planned (PR-AI-001..012) | Fabric MCP servers: Core remote, Fabric IQ read-only, local open-source [S14]; DataGrip MCP tools [S9]; DBeaver MCP configuration [S10] | Available through integration | Expose only what is local to KratosForge (local runs, proofs, metadata project). Leave Fabric item operations to Microsoft's Fabric MCP servers instead of duplicating them | PB-095 |
| Embedded AI assistant | None, by decision | SSMS Copilot Agent Mode [S11]; Snowflake CoCo agent [S3]; DataGrip AI chat [S9] | Not strategically relevant | Standing decision: AI-ready, not AI-dependent. No embedded assistant, no AI keys | none |
| Extensions and custom commands | Planned (FEAT-EXT-03) | VS Code and JetBrains ecosystems | Available but incomplete | Git-native program owns it | PB-097 |
| Real-time co-editing of notebooks | None | JupyterLab real-time collaboration *(search result only)* [S12]; Fabric web | Not strategically relevant | Collaboration happens in Fabric and Git; a desktop tool doesn't need it | none |
| Spark job definition authoring | None | Fabric VS Code extension creates and edits Spark job definitions [S1] | Genuine gap, low priority | Not added: notebooks and metadata entities cover the local mission. Revisit if users ask | none |

## What this changes in the backlog

- Nothing new was added only because a competitor has it.
- Two recommendations go to owning programs: the Git-native program should consider fabric-cicd for
  item deployment (PB-041), and the AI integration program should keep its MCP surface to local
  capabilities and point at Microsoft's Fabric MCP servers for item operations (PB-095).
- The SQL sprint's scope for Warehouse connectivity and object explorer should stay narrow, because
  SSMS and VS Code MSSQL already cover administration (PB-024, PB-025).
- macOS and Linux desktop support is a product-owner decision, not a backlog item.

## Sources

All read on 2026-10-17 unless marked otherwise. "Page date" is the date the page itself shows.

| Id | Source | Page date | What it was used for |
| --- | --- | --- | --- |
| S1 | Microsoft Learn, Fabric Data Engineering VS Code extension: https://learn.microsoft.com/fabric/data-engineering/setup-vs-code-extension | 2026-03-24 | Remote Spark notebook runs, lakehouse explorer, Spark job definitions, Environments |
| S2 | Databricks, VS Code extension: https://docs.databricks.com/aws/en/dev-tools/vscode-ext | 2026-09-11 | Databricks Connect debugging, bundles, workspace sync |
| S3 | Snowflake, VS Code extension: https://docs.snowflake.com/en/user-guide/vscode-ext | not shown | SQL execution with cancel, query history, object explorer, Snowpark debugging, CoCo agent |
| S4 | dbt Labs, dbt Fusion engine and VS Code extension: https://getdbt.com/blog/get-to-know-the-new-dbt-fusion-engine-and-vs-code-extension | 2025-05-28 | Local SQL compilation, column lineage, IntelliSense |
| S5 | dbt, Microsoft Fabric setup: https://docs.getdbt.com/docs/core/connect-data-platform/fabric-setup | not shown | dbt-fabric targets Fabric Warehouse; SQL analytics endpoint is read-only |
| S6 | Microsoft Fabric community blog, official support for fabric-cicd: https://community.fabric.microsoft.com/blog/fbc_fabricupdatesblogs/announcing-official-support-for-microsoft-fabric-cicd-tool/5172297 | 2026-02-19 | Officially supported, parameterized cross-workspace deployment |
| S7 | Microsoft Learn, Fabric notebook selection guide: https://learn.microsoft.com/en-us/fabric/data-engineering/fabric-notebook-selection-guide | 2026-06-17 | CU per notebook type |
| S8 | Microsoft Learn, T-SQL surface area in Fabric Data Warehouse: https://learn.microsoft.com/en-us/fabric/data-warehouse/tsql-surface-area | 2026-09-29 | What Fabric's T-SQL doesn't support |
| S9 | JetBrains, DataGrip 2026.2: https://blog.jetbrains.com/datagrip/2026/07/16/datagrip-2026-2-ai-agent-skills-mcp-tools-and-cli-commands-for-data-source-management-bundled-jdbc-drivers-and-improved-session-control/ | 2026-07-16 | MCP tools, agent skills, CLI |
| S10 | DBeaver 26.1: https://dbeaver.com/2026/06/10/dbeaver-26-1/ | 2026-06-10 | Fabric support in PRO editions, MCP server configuration |
| S11 | Microsoft Learn, SSMS 22 release notes: https://learn.microsoft.com/en-us/ssms/release-notes-22 | 2026-10-13 (22.11.0) | Fabric SQL database support, Copilot Agent Mode |
| S12 | JupyterLab documentation, real-time collaboration: https://jupyterlab.readthedocs.io/en/4.2.x/user/rtc.html | search result only | Real-time collaboration, debugger |
| S13 | Microsoft Learn, VS Code MSSQL extension Fabric integration: https://learn.microsoft.com/sql/tools/visual-studio-code-extensions/mssql/mssql-fabric-integration | 2026-08-27 | Browse Fabric, SQL database and SQL endpoints; Warehouse not mentioned |
| S14 | Microsoft Learn, What is the Fabric MCP server: https://learn.microsoft.com/en-us/rest/api/fabric/articles/mcp-servers/what-is-fabric-mcp-server | about 2026-09-23 | Core remote, Fabric IQ and local MCP servers |

Re-verify every source at the start of each development cycle; competitor tools change monthly.
