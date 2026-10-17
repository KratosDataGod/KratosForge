# Release readiness

What must be true before Fabric Desktop by KratosBI goes to an enterprise pilot, and where each piece stands. Updated as items in the [enterprise backlog](enterprise-backlog.md) land.

Last updated 2026-10-17.

## Verdict today

**Not ready for an enterprise pilot.** Nothing has been verified against a live tenant with the current sign-in, the app and its engine aren't code signed, and the update path checks downloads only for corruption. The gates below say what changes that.

## Pilot gates

| Gate | Needs | State |
| --- | --- | --- |
| G1 Live verification | FD-101 harness run on a non-production workspace with every check passing, results attached here | Harness not built |
| G2 Signed builds | Azure Trusted Signing account (D-C3), signing in CI (PR #28) | Blocked on Chris |
| G3 Update integrity | FD-102a (checksums enforced), FD-103 (provenance, SBOM) | Not started |
| G4 Admin controls | FD-105 policies, FD-104 deployment guide | Partial: policy file exists |
| G5 Data protection | FD-131a redaction, FD-138 safe support report | Not started |
| G6 Audit | FD-125 audit trail | Partial: run log and receipts only |
| G7 Documentation | Capability matrix, security architecture, known limitations, troubleshooting (this page plus linked guides) | Partial |

## Supported capability matrix

"Supported" means tested on a live tenant and covered in CI. Today nothing is in that column; the [docs home](../README.md#what-is-tested-and-what-isnt) has the detailed list.

| Area | Level |
| --- | --- |
| Notebooks and SQL on this PC (local Delta tables) | Tested in CI on Windows and Linux |
| Writing tables to OneLake from the PC | Used on the maintainer's tenant with an own app registration |
| Sign-in with no app registration | Not yet tried on a tenant |
| Run in Fabric, schedules, side by side, Prove | Preview (built to documented APIs, untested) |
| Metadata framework against OneLake | Preview |
| Headless agent | Preview |

## Known limitations

- Windows only for the desktop app; the engine and agent also run on Linux.
- Not code signed: Smart App Control can block the engine.
- Spark runs on the PC through DuckDB's PySpark API and Spark SQL translation; not every Spark API works. The compatibility check says which cells won't.
- Concurrent writers to one table are protected by leases and version-checked commits, untested on OneLake.

## Pilot acceptance checklist

To be completed with the pilot organization:

1. Tenant settings and sign-in per [azure-setup.md](../azure-setup.md) confirmed.
2. The live test harness passes on the pilot's non-production workspace.
3. The policy file is deployed with the organization's channel and support link.
4. Ten core flows from the launch review pass on the pilot tenant.
5. Support route agreed (Report a problem target, response time).
6. Rollback plan: how to return every PC to the previous build.
