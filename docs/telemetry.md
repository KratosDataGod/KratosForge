# Usage data

Fabric Desktop by KratosBI can send a small daily summary of which actions were used, how often they failed and how long they took. It is **off** until you turn it on in **Settings > Usage data**. Your organization can turn it off, or on, for everyone. This feature is in Preview.

## What is sent

One record per day, after the day is over. **Settings > Usage data > See what's sent** shows the exact text waiting to go. Every field is listed here:

| Field | Example | What it is |
| --- | --- | --- |
| `day` | `2026-10-17` | The day the counts are for |
| `install_id` | `8f2c1e7a-5b0d-...` | A random id made on this PC. It isn't derived from you, your account, the PC or your tenant. **New install ID** replaces it |
| `build` | `58a298b1c2d3` | The app build |
| `channel` | `stable` | Stable or Preview |
| `windows` | `10.0.26100` | The Windows version |
| `python` | `3.12.7` | The engine's Python version |
| `features` | `{"run_sql": {"count": 9, "errors": 0, "seconds": 4.2}}` | For each app action that changes or runs something: how many times it ran, how many failed and the total seconds. The names are the app's own action names, the same ones the [audit trail](user-guide.md) uses |
| `errors` | `{"HTTPError": 1}` | How many failures of each kind. The kind is the error's type name only |

## What is never sent

- Your data, files, tables, previews or query results.
- Code, SQL, notebook contents or anything you typed.
- Names of workspaces, lakehouses, tables, files, notebooks or entities.
- Error messages, paths, URLs or ids from Fabric.
- Your name, email, account, tenant or organization.
- Read-only actions such as browsing workspaces or previewing a table. They aren't counted at all.

## Where it goes

The records go to Azure Monitor Application Insights over HTTPS, as one custom event named `daily_usage` per day.

- By default they go to KratosBI's Application Insights. A build without a KratosBI destination has the switch but sends nothing, and Settings says so.
- An organization can send them to its own Application Insights instead. Set `telemetry_connection_string` in the policy file. Nothing then goes to KratosBI.

## When it is sent

The app sends finished days once each time it starts, in the background.

- A day that fails to send waits for the next start.
- Unsent days older than 30 days are deleted.
- Turning usage data off deletes everything waiting to be sent.
- The headless agent never sends usage data.

The counts are kept in `%LOCALAPPDATA%\FabricDesktop\telemetry\usage.json` until they are sent.

## For IT admins

Set these in `%ProgramData%\FabricDesktop\policy.json` ([Enterprise install](enterprise-install.md)):

| Key | Value | Effect |
| --- | --- | --- |
| `telemetry` | `false` | Off for everyone. People can't turn it on |
| `telemetry` | `true` | On for everyone. People can't turn it off. Use this with your own `telemetry_connection_string` |
| `telemetry_connection_string` | `InstrumentationKey=...;IngestionEndpoint=https://...` | Sends usage data to your own Application Insights resource |

A connection string that isn't valid sends nothing, and **Settings > Help and about** lists it as ignored.

Allow the `IngestionEndpoint` host of the connection string through your proxy (for example `westeurope-5.in.applicationinsights.azure.com`). Without it, nothing is sent and nothing else is affected.

## For maintainers

A build sends to KratosBI only when the repository variable `TELEMETRY_CONNECTION_STRING` holds an Application Insights connection string. CI writes it into the engine's `engine.json`. Leave the variable unset to ship builds that never send. The code is in `sidecar/fabric_desktop/telemetry.py`.
