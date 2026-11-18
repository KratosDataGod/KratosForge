# Tools an AI tool can use

<!-- Generated from the capability registry. Do not edit by hand: from sidecar/, run python ../.github/scripts/mcp_tools_doc.py > ../docs/ai/tools.md -->

These are the tools a connected AI tool can use in KratosForge. This page is generated from the
capability registry, so it lists every tool there is. Your AI tool can also get the live list, with
each tool's inputs and outputs, from the `fabric_desktop_list_capabilities` tool. Tools marked
**Preview** say so when your tool asks for them.

- **Permission** is what you give the tool when you add it in **Settings > Integrations**.
- **Asks you** says whether KratosForge asks you to approve each call.
- **Reads or changes** says whether the tool only reads, or changes or runs something.
- **Where** is where the tool reads or changes things: this PC, Fabric, or both.
- **Status** says **Preview** for a tool still in Preview. Tools that need a permission this release
  doesn't offer are listed as **Not available yet**.
- **Resource** is the address the tool is also served at as an MCP resource.

| Tool | What it does | Permission | Asks you | Reads or changes | Where | Status | Resource |
|---|---|---|---|---|---|---|---|
| `fabric_desktop_cancel_execution` | Cancel an execution | Run code on this PC | No | Changes | This PC |  |  |
| `fabric_desktop_cancel_pipeline_run` | Cancel a local pipeline run | Run code on this PC | Every time | Changes | This PC | Preview |  |
| `fabric_desktop_cancel_proof` | Cancel a proof | Run code on this PC | Every time | Changes | This PC |  |  |
| `fabric_desktop_check_pipeline_compatibility` | Check what can run on this PC | Read metadata | No | Reads | This PC | Preview |  |
| `fabric_desktop_check_pipeline_run` | Check a pipeline can run on this PC | Read metadata | No | Reads | This PC | Preview |  |
| `fabric_desktop_check_proof` | Check a proof plan | Read metadata | No | Reads | This PC |  | `fabric-desktop://notebooks/{name}/proof-plan` |
| `fabric_desktop_check_run_risk` | Check a run's risk | Read metadata | No | Reads | This PC | Preview |  |
| `fabric_desktop_delete_proof_outputs` | Delete a proof's outputs | Write to OneLake | Every time | Changes | Fabric | Not available yet |  |
| `fabric_desktop_execute_python` | Run Python | Run code on this PC | Every time | Changes | This PC |  |  |
| `fabric_desktop_execute_sql` | Run a SQL query | Read data | Unless pre-approved | Reads | This PC |  |  |
| `fabric_desktop_export_pipeline` | Convert a pipeline definition | Read metadata | No | Reads | This PC | Preview |  |
| `fabric_desktop_export_receipt` | Export a receipt | Read data | Unless pre-approved | Reads | This PC |  |  |
| `fabric_desktop_export_semantic_model_ossie` | Export a semantic model to Apache Ossie | Read metadata | No | Reads | Fabric | Preview |  |
| `fabric_desktop_get_activity_registry` | List pipeline activity support | Read metadata | No | Reads | This PC | Preview |  |
| `fabric_desktop_get_artifact_metadata` | Get item metadata | Read metadata | No | Reads | Fabric |  | `fabric-desktop://workspaces/{workspace_id}/items/{item_id}` |
| `fabric_desktop_get_execution_logs` | Get execution logs | Read metadata | No | Reads | This PC |  |  |
| `fabric_desktop_get_execution_status` | Get execution status | Read metadata | No | Reads | This PC |  |  |
| `fabric_desktop_get_lineage` | Get column lineage | Read metadata | No | Reads | This PC |  | `fabric-desktop://projects/metadata/lineage/{entity}` |
| `fabric_desktop_get_local_table_history` | Get a local Delta table's history | Read metadata | No | Reads | This PC |  |  |
| `fabric_desktop_get_local_tables` | List a local data project's tables | Read metadata | No | Reads | This PC |  | `fabric-desktop://local-data/{project}/tables` |
| `fabric_desktop_get_notebook` | Get notebook | Read metadata | No | Reads | This PC |  | `fabric-desktop://notebooks/{name}` |
| `fabric_desktop_get_notebook_dependencies` | Get notebook cell dependencies | Read metadata | No | Reads | This PC |  | `fabric-desktop://notebooks/{name}/dependencies` |
| `fabric_desktop_get_notebook_environment` | Get notebook environment | Read metadata | No | Reads | This PC |  | `fabric-desktop://notebooks/{name}/environment` |
| `fabric_desktop_get_pipeline_run` | Get a local pipeline run | Read metadata | No | Reads | This PC | Preview |  |
| `fabric_desktop_get_project` | Get project | Read metadata | No | Reads | This PC |  | `fabric-desktop://projects/{id}` |
| `fabric_desktop_get_project_run` | Get a metadata project run | Read metadata | No | Reads | This PC, Fabric | Preview |  |
| `fabric_desktop_get_proof` | Get proof | Read metadata | No | Reads | This PC |  | `fabric-desktop://proofs/{id}` |
| `fabric_desktop_get_receipt` | Get receipt | Read metadata | No | Reads | This PC |  | `fabric-desktop://receipts/{id}` |
| `fabric_desktop_get_side_by_side` | Get side-by-side run | Read metadata | No | Reads | This PC |  | `fabric-desktop://side-by-side/{id}` |
| `fabric_desktop_get_status` | Get status | Read metadata | No | Reads | This PC |  | `fabric-desktop://status` |
| `fabric_desktop_get_table_schema` | Get table schema | Read metadata | No | Reads | Fabric |  | `fabric-desktop://workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables/{table}/schema` |
| `fabric_desktop_inspect_pipeline` | Inspect a pipeline definition | Read metadata | No | Reads | This PC | Preview |  |
| `fabric_desktop_list_artifacts` | List items in a workspace | Read metadata | No | Reads | Fabric |  | `fabric-desktop://workspaces/{workspace_id}/items` |
| `fabric_desktop_list_capabilities` | List capabilities | Read metadata | No | Reads | This PC |  | `fabric-desktop://capabilities` |
| `fabric_desktop_list_executions` | List executions | Read metadata | No | Reads | This PC |  |  |
| `fabric_desktop_list_local_projects` | List local data projects | Read metadata | No | Reads | This PC |  | `fabric-desktop://local-data` |
| `fabric_desktop_list_notebooks` | List notebooks | Read metadata | No | Reads | This PC |  | `fabric-desktop://notebooks` |
| `fabric_desktop_list_pipeline_runs` | List local pipeline runs | Read metadata | No | Reads | This PC | Preview |  |
| `fabric_desktop_list_project_runs` | List metadata project runs | Read metadata | No | Reads | This PC, Fabric | Preview |  |
| `fabric_desktop_list_projects` | List projects | Read metadata | No | Reads | This PC |  | `fabric-desktop://projects` |
| `fabric_desktop_list_proofs` | List proofs | Read metadata | No | Reads | This PC |  | `fabric-desktop://proofs` |
| `fabric_desktop_list_receipts` | List receipts | Read metadata | No | Reads | This PC |  | `fabric-desktop://receipts` |
| `fabric_desktop_list_semantic_models` | List semantic models | Read metadata | No | Reads | Fabric | Preview |  |
| `fabric_desktop_list_side_by_side` | List side-by-side runs | Read metadata | No | Reads | This PC |  | `fabric-desktop://side-by-side` |
| `fabric_desktop_list_tables` | List lakehouse tables | Read metadata | No | Reads | Fabric |  | `fabric-desktop://workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables` |
| `fabric_desktop_list_workspaces` | List workspaces | Read metadata | No | Reads | Fabric |  | `fabric-desktop://workspaces` |
| `fabric_desktop_placement_advice` | Advise where to run a notebook | Read metadata | No | Reads | This PC | Preview |  |
| `fabric_desktop_preview_ossie_import` | Preview an Apache Ossie import | Read metadata | No | Reads | Fabric | Preview |  |
| `fabric_desktop_run_notebook` | Run a notebook on this PC | Run code on this PC | Every time | Changes | This PC |  |  |
| `fabric_desktop_start_pipeline_run` | Run a pipeline on this PC | Run code on this PC | Every time | Changes | This PC | Preview |  |
| `fabric_desktop_start_proof` | Start a proof | Change Fabric items | Every time | Changes | This PC, Fabric | Not available yet |  |
| `fabric_desktop_validate_notebook` | Validate notebook | Read metadata | No | Reads | This PC |  | `fabric-desktop://notebooks/{name}/validation` |
| `fabric_desktop_validate_pipeline` | Validate a pipeline definition | Read metadata | No | Reads | This PC | Preview |  |
| `fabric_desktop_validate_project` | Validate project | Read metadata | No | Reads | This PC |  | `fabric-desktop://projects/metadata/validation` |
| `fabric_desktop_verify_proof_table` | Verify a proof table again | Read data | Unless pre-approved | Reads | Fabric |  |  |
| `notebook_outline_get` | Get notebook outline | Read metadata | No | Reads | This PC |  | `fabric-desktop://ext/notebook-outline/notebooks/{name}` |
