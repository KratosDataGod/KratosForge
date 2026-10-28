# Engine call limits

Which engine calls can hold the app up, and what bounds them (PB-072). The engine answers the app over one pipe in two lanes (`sidecar/fabric_desktop/server.py`):

- **Quick lane.** Reads of local state and the kernel (`QUICK`). The shell ends the engine if one of these takes more than 60 seconds (`app/src-tauri/src/watchdog.rs`). They never wait behind a long call (`tests/test_server_lanes.py`).
- **Ordered lane.** Everything else, one call at a time, in order. Writes need that order: a publish must not overtake the autosave before it. These calls have no time limit in the shell, because runs and publishes can rightly take hours. While one runs, every other ordered call waits, and most pages make one when they open.

Long work that starts a background job returns at once and has its own Cancel: git fetch, pull and push, SQL runs, Prove, side by side, validate, pipeline runs, environment installs and X-ray scans.

## Bounded in this change

| Call | Before | Now |
| --- | --- | --- |
| Monitor's run status refresh (`fabric_runs`) | Asked Fabric about every open run, one after another. Offline or throttled, 50 open runs could take about an hour | Stops after 30 seconds, or after two failures in a row. The rest keep their last known status |
| Entity page drift card (`meta_drift`) | Read the whole upstream table to compare its columns | Reads the columns only |
| Sign-in window (`sign_in`, and any call that needs a new token) | A browser sign-in whose tab was closed waited forever | Gives up after 10 minutes; the person signs in again |
| Task Scheduler (`schtasks`) | No limit | 60 seconds |

## Still unbounded, needing a design decision

These run inside the call, with no limit and no Stop except stopping the kernel, which ends the whole engine. Each needs either a background job with Cancel (the pattern above) or an app-wide "Still working" banner with Stop. Both are UI changes, so they wait on Chris's go.

| Call | What makes it long | How it is reached |
| --- | --- | --- |
| `meta_tests` (`run`), `meta_promote_check`, `meta_promote` | Runs the project's own tests in the engine | Tests tab when they never ran; promote dialog |
| Notebook frame views (`notebook_frame_profile`, `_query_profile`, `_page`, `_chart`, `_wrangle`, `_download`) with the in-process kernel | Whole frame in the call (the process kernel limits them to 30 minutes) | Buttons in the result viewer |
| `meta_run` with local compute, `workload_act` run | The entity pipeline | Run buttons |
| `access_cache_refresh`, `catalog` | Every workspace in the tenant, one after another (`catalog` 8 at a time) | Button; `catalog` on page load |
| `platform_scan_endorsement` | Up to 300 seconds of polling per 100 workspaces | Button |
| `save_sql`, `meta_compare`, `meta_profile`, `meta_optimize` (local), `prove_verify` | Grow with the data | Buttons |
| Platform calls (`platform_*`, `governance_*`, `mesh_*`) | 300 seconds per catalog call, several per page | Platform pages |

One Fabric REST request can itself take 6 to 9 minutes at worst: 5 tries of up to 60 seconds, with waits between them (`fabric_api.py`, `retrying.py`). A long-running operation is followed for up to 300 seconds.
