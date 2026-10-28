# Smoke test results, <date>

- **App version:** <Settings › About>
- **Run by:** <role, not a name>
- **Workspace:** <test workspace name> (marked `fabric-desktop-live-test`, Trial or non-production capacity)
- **Command:** `fabric-desktop smoke-test --workspace <id> [--run-in-fabric] [--platform [--platform-upgrade]]`
- **Smoke run id:** <from summary.md> · **Live test run id:** <from summary.md>

| # | Flow | Automated | By hand | Result | Issue |
| --- | --- | --- | --- | --- | --- |
| 1 | Install and start the engine | | | | |
| 2 | Sign in, no app registration | | | | |
| 3 | Workspaces and the catalog | | | | |
| 4 | Create a lakehouse; refuse an invalid name | | | | |
| 5 | Notebook on the PC writes to OneLake | | | | |
| 6 | Publish and Run in Fabric; Monitor | | | | |
| 7 | Prove the notebook | n/a | | | |
| 8 | Metadata project: Dev on the PC, promote to Test | n/a | | | |
| 9 | The live test | | | | |
| 10 | Report a problem and the support bundle | | | | |
| 11 | Platform: Finish or repair once from empty | | | | |
| 12 | Platform: again, or from the Stable build (`--platform-upgrade`) | | | | |
| 13 | Platform: register a data product | | | | |
| 14 | Platform: surrogate keys | | n/a | | |
| 15 | Platform: policy exception | | | | |
| 16 | Platform: restrict a central lakehouse family | | | | |

Flows 11 to 16 only run with `--platform`; leave them out otherwise. Record whether the UDF connection was added by hand, and anything the platform ledger left behind.

Result is **Passed** only when the automated part passed and every by-hand box is ticked.

## Recorded, not counted

- Azure SQL token (warehouse queries): <passed or the error, with no token>
- Kusto token (warehouse Monitoring): <passed or the error, with no token>

## Notes

<anything surprising, with no tokens, tenant ids or personal data>

## Inventory changes

<feature id: old verification → new verification, only for features whose every flow passed>
