# SQLite stores

The SQLite files the engine and the agent keep on this PC, what each one holds, and whether a committed row must survive a power cut (QA-022). Every store opens its file with `sqlitefile.connect` or `sqlitefile.transaction` (`sidecar/fabric_desktop/sqlitefile.py`). Both set `synchronous=full`, so a commit returns only once its row is on the disk.

`sidecar/tests/test_sqlite_durability.py` enforces this in two ways:

- It fails on a `sqlite3.connect` anywhere else in the engine.
- It checks each store's connections commit with `synchronous=full`, even on a SQLite built to default to something less durable.

Paths are relative to the app's data folder, or to the agent's home for the agent's files.

| File | Opened by | Holds | Must a commit survive a power cut? | A file SQLite can't read |
|---|---|---|---|---|
| `queue.sqlite3` (agent) | `jobqueue.JobQueue` | Jobs, their claims and outcomes (run history), schedule state | Yes: a job that says succeeded must stay succeeded, or it runs twice | Moved aside, starts empty |
| `integrations/integrations.sqlite` | `integrations.store.IntegrationStore` (and `integrations.audit`) | Connected AI tools, their grants and approvals | Yes: an approval or a revoked grant must not come back changed | Moved aside, starts empty (tools connect again) |
| `platform/platform-<env>.sqlite`, `platform/keys.sqlite` | `central.client.LocalPlatform` | Platform keys this PC issued, admins and rules | Yes: a key issued or revoked must stay that way | Refused, kept as it is |
| `workloads.sqlite` | `workloads.store.ManagementStore` | Who owns each workload, change history, actions, audit entries not yet sent, collection cache | Yes, except the cache: history and unsent audit entries can't be rebuilt | Refused, kept as it is |
| `alerts.sqlite3`, `job_alerts.sqlite3` (agent), `alerts_outbox.sqlite`, `schedule_alerts.sqlite3` | `workloads.notify.Outbox` | Teams notices waiting to be sent, each route's backoff | Yes: a notice waiting must not be lost, and one sent must not go again | Moved aside, starts empty |
| `platform_outbox.sqlite`, `shortcut_alerts.sqlite3` (agent) | `workloads.notify.Outbox` and `workloads.platform_notify.Ledger` | Platform notices waiting, and which were sent and which breaks are open | Yes: a sent notice must stay marked sent | Moved aside, starts empty |
| `optimize/outbox.sqlite` | `optimize.outbox.Outbox` | Optimize and X-ray results waiting for the central lakehouse | Yes: results waiting must not be lost | Moved aside, starts empty |

Not stores, so they may open SQLite themselves (the test's `ALLOWED` list):

- `backup.py` reads the stores above read-only to copy them into a backup, and checks a restored file.
- `engines/local_sql.py` runs the person's own SQL against a database they chose.
- The Academy exercises under `academy/packs/` use an in-memory database.

Journal modes still differ: `queue.sqlite3` and `integrations.sqlite` use WAL, and the rest use SQLite's default rollback journal. With `synchronous=full`, both modes keep every commit through a power cut.

## Cost

These were measured on Linux on the build machine, not on Windows hardware:

- The extra pragma adds about 5 µs to each connection (opening and closing went from 24.5 µs to 29.1 µs).
- A WAL commit with `full` took 158 µs, against 12 µs with `normal`.
- `JobQueue.enqueue` took about 1.3 ms.

CPython's own SQLite builds already default to `full`: this build reports `DEFAULT_SYNCHRONOUS=2` and `DEFAULT_WAL_SYNCHRONOUS=2`. On those builds the change only makes the setting explicit. The 158 µs commit cost applies only where a build would have defaulted to `normal`.
