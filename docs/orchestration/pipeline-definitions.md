# Pipeline definitions (Preview)

Fabric Desktop by KratosBI can read a Microsoft Fabric Data Factory pipeline definition, check it, show what is in it and convert it between formats, all on your PC. Nothing in the pipeline runs, nothing is sent to Fabric, and no capacity is used. Running pipelines on your PC is planned ([orchestration plan](plan.md)).

This is Preview: the formats follow Microsoft's documentation and have been tested with hand-built definitions, not yet with exports from a live tenant.

## What it reads

| Format | What it is |
| --- | --- |
| `pipeline-content.json` | The pipeline as Fabric keeps it: `{"properties": {"activities": [...], "parameters": {...}, "variables": {...}}}`. Azure Data Factory and Synapse pipeline JSON has the same shape and is read too. |
| Item definition | What Fabric's `getDefinition` returns, or a create or update body: `{"definition": {"parts": [...]}}`, with base64 parts for `pipeline-content.json` and `.platform`. |
| Git folder | A `<name>.DataPipeline` folder from a workspace connected to Git, with `pipeline-content.json` and `.platform`. |

## What it checks

Each problem names the activity and the JSON path, and says what to fix:

- the definition isn't valid JSON (with the line and column), has no `properties` or `activities`, or a part can't be decoded
- an activity without a name or type, or two activities with the same name (nested ones included)
- a dependency on an activity that doesn't exist at the same level, a missing or misspelled dependency condition (`Succeeded`, `Failed`, `Skipped`, `Completed`), an activity that depends on itself, and dependency loops
- expressions that read an undeclared parameter or variable, an activity that isn't in the pipeline, or an activity that the reader doesn't wait for (a warning)
- Set variable and Append variable on an undeclared variable, `item()` outside a ForEach, and expressions that can't be read

Activity types it doesn't know are never a problem: they are listed and kept exactly as they are.

## Nothing is lost

Every property is kept, including ones this version doesn't know, unknown activity types and the `.platform` part. A definition you haven't changed is exported exactly as it was read, byte for byte.

## Using it

From the command line, with the engine installed:

```powershell
fabric-desktop pipeline validate .\pl_orders_daily.DataPipeline     # problems, exit code 1 on errors
fabric-desktop pipeline inspect .\pipeline-content.json             # activities, dependencies, expressions as JSON
fabric-desktop pipeline export .\pipeline-content.json --format item_definition
```

From your own AI tool, through MCP or the local API ([connect your AI tool](../ai/README.md)): `fabric_desktop_validate_pipeline`, `fabric_desktop_inspect_pipeline` and `fabric_desktop_export_pipeline`. They take the definition as text (up to 1 MB) and need only the read metadata permission. Default values of secure string parameters are hidden.

To see which activities could run on your PC and which need Fabric, see [Pipeline activity compatibility](activity-registry.md).

There is no pipeline page in the app yet.
