# Capacity of each environment

**Monitor > Cost** (Preview) lists the Fabric capacity each environment's workspace is on, and each environment card in **Data mesh > Environments** shows the same line. It answers one question before you run something in Fabric: is the capacity there and running?

Everything is read only, with your own sign-in. Nothing changes a capacity, and nothing asks you to sign in or grant access. Work on this PC never needs any of it.

## What it shows

| Field | Where it comes from |
| --- | --- |
| Capacity name, SKU, region and state | Fabric's capacity list (`GET /capacities`), for capacities you can see |
| Capacity id, region and assignment progress | The workspace (`GET /workspaces/{id}`), when Fabric sends them |
| Read | When it was read. Readings are kept for an hour; older ones are marked **Stale** until you press **Refresh** |

Only fields Fabric actually returned are shown. An SKU the app doesn't know has no CU size, rather than a guessed one.

## Running is not the same as having room

**Running** means Fabric reports the capacity as Active: work can start. It doesn't say how busy the capacity is; a running capacity can still slow down or delay jobs when it is overloaded. How busy a capacity is shows in Fabric's Capacity Metrics app to capacity admins. There is no public per-capacity utilization API for ordinary users, so the app shows utilization as unknown, never as 0.

**Not running** means the capacity is Inactive: paused, suspended or an expired trial. Fabric runs, schedules and new lakehouses fail until an admin resumes it. Runs on this PC still work.

## When it can't be read

| Shown | Means | What to do |
| --- | --- | --- |
| The capacity isn't visible to you | Fabric only lists capacities you administer or may assign workspaces to. The workspace still runs on it. | Ask your capacity admin for its status, or check the Capacity Metrics app. |
| Capacity access not granted | Your sign-in hasn't accepted `Capacity.Read.All`. The capacity id still shows. | Sign in again and accept it, or ask IT. See [Permissions](../permissions.md). |
| You can't open the workspace | Fabric refused the workspace (401 or 403). | Ask a workspace admin for access, or assign a workspace you can open. |
| The workspace doesn't exist any more | Fabric returned 404. | Assign another workspace in Data mesh > Environments. |
| The workspace isn't on a capacity | The workspace has no capacity. | Ask a workspace admin to assign one (Workspace settings > License info). |
| Signed out, or Couldn't reach Fabric | No sign-in, or no network. | The last good reading stays, with its age. Work on this PC carries on. |

Each environment is read on its own, so one failure doesn't hide the others. When Fabric can't be reached, the app stops trying after the first environment instead of waiting on each.

## For developers

`sidecar/fabric_desktop/capacity.py` builds the observations; `capacity_health(refresh=False)` in `estate_service.py` is the engine call (read only, audited as a read). The cost report includes the same document as `capacities`, and the capacity share per environment uses it. Readings are cached in `capacity_observations.json` in the app's data folder. `FabricError.status` now carries the HTTP status so "no access" and "not found" can be told apart.
