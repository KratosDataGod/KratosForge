# Tenant smoke test

The live test ([Live testing on your tenant](../../live-testing.md)) checks sign-in, the Fabric API, OneLake and Delta. The smoke test (PB-002) goes further. It runs ten core flows of the app on a real tenant, which is the evidence a launch to about 5,000 people needs. Part of each flow runs unattended from the command line, and a person ticks the rest in the app.

It runs on a **workspace set aside for tests**, never on a production workspace or on the capacity that hosts one. It writes only through the live test, so the live test's guards and cleanup all apply (see [Before you start](../../live-testing.md#before-you-start)):

- the workspace's description must contain `fabric-desktop-live-test`;
- names that look like production, and Platform workspaces in the policy file, are refused;
- only `fdlt_<run id>` items it created are deleted.

## Run it

On the PC where KratosForge is installed, with Settings › Advanced › App ID empty:

```powershell
fabric-desktop smoke-test --workspace <workspace id> --dry-run              # what it would do; writes nothing
fabric-desktop smoke-test --workspace <workspace id> --run-in-fabric        # asks "Run it?", then runs
```

- It signs in the way the app does, with Microsoft's Fabric CLI client and the Windows sign-in. If the app isn't signed in, the sign-in window opens.
- `--run-in-fabric` also runs the test notebook in Fabric (flow 6), which spends Spark capacity. Without it, that step is skipped.
- `--yes` skips the question. `--out` picks the results folder (default `smoke-test-results`).
- The exit code is 0 when every automated step passed, 1 when one failed, and 2 when it wasn't run.

The run takes about 10 minutes, or up to 40 with `--run-in-fabric`. The by-hand part takes about an hour.

It writes `smoke-test-results/<run id>/summary.md` and `smoke.json`, with the live test's own results under `live/`. The summary lists every step and every flow, plus the by-hand checklist. None of the files holds a token. They do hold the workspace's name and id, so share them as you would those.

## The ten flows

| # | Flow | Features | Automated | By hand |
| --- | --- | --- | --- | --- |
| 1 | Install from the Preview release and start the engine | FEAT-SHL-01, FEAT-SHL-09 | The engine's version; DuckDB, Polars, delta-rs and PyArrow load | Install the Preview on a clean PC, start the app, check Settings › About |
| 2 | Sign in with Microsoft's Fabric CLI client, no app registration | FEAT-AUTH-01 | No App ID set; tokens for Fabric and OneLake. Tokens for Azure SQL (warehouse queries) and Kusto (warehouse Monitoring) are recorded but don't fail the flow, since those may need an app registration ([permissions](../../permissions.md)) | Sign in and out from the app |
| 3 | List workspaces and open a lakehouse in the catalog | FEAT-FAB-02, FEAT-ONE-01 | The test workspace is in your workspace list; its items are listed | Open a lakehouse in Workspaces |
| 4 | Create a lakehouse; refuse an invalid name | FEAT-FAB-03 | `1 bad name` is refused before Fabric is called; the live test creates and reads a schema-enabled lakehouse | The refusal and suggestion in Create › Lakehouse |
| 5 | Notebook on the PC writes a table to OneLake | FEAT-NB-02, FEAT-ONE-05 | The live test's OneLake file and Delta checks, and two writers at once through the notebook write path | Run a notebook's cells on This PC and write a table |
| 6 | Publish the notebook and Run in Fabric; see it in Monitor | FEAT-FAB-06, FEAT-FAB-07 | Publish a notebook and read its cells back; with `--run-in-fabric`, run it in Fabric | Publish and Run in Fabric from the app; the run shows in Monitor › Runs |
| 7 | Prove the notebook on the PC and in Fabric | FEAT-PRV-01 | Nothing | Prove on the notebook from flow 5 |
| 8 | Metadata project: run Dev on the PC, promote to Test | FEAT-META-02, FEAT-META-09, FEAT-META-14 | Nothing | Run Dev on This PC; promote to Test mapped to the test workspace |
| 9 | The live test: dry run, run, cleanup | FEAT-OPS-04, FEAT-FAB-01 | The whole live test with every opt-in check; it must pass and leave nothing behind | Optional: `live-test --agent-config` as the agent's identity (FEAT-AUTH-04) |
| 10 | Report a problem and save a support bundle; nothing personal in either | FEAT-SHL-10, FEAT-SHL-11 | A support bundle holds no token, user name, tenant id or workspace id | Read the Report a problem draft |

A flow counts as passed only when its automated part passed **and** every by-hand box is ticked. Flows 7 and 8 are by hand only.

## Recording a run

1. Copy [RESULTS-TEMPLATE.md](RESULTS-TEMPLATE.md) to `results-<date>.md` in this folder and fill it in from the run's `summary.md` and the by-hand checks.
2. File each failure as a GitHub issue and link it from the results.
3. In `docs/product/FEATURE_INVENTORY.json`, change a feature's verification only when every flow that names it passed. Cite the results file as evidence, then run `python docs/product/tools/product_data.py all`.

Results never include tokens, tenant ids or personal data.
