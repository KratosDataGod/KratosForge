# Executive summary: enterprise engineering audit

Baseline: `main` at `b75dcf5`, 2026-11-02. Re-scored on `main` at `1ca71e3`, 2026-11-05. Full scorecard: [Enterprise readiness](ENTERPRISE_READINESS.md).

## Where KratosForge stands

**Score: 64 out of 100, up from 54. Not enterprise-ready yet.**

Since the baseline:

- **Every confirmed data-integrity and Fabric-parity defect is fixed on `main`, each with a test.** Appends that would change values are refused. Overwrites, lost leases and failed uploads no longer leave a table or file different from what was reported. Casts, sums, decimal arithmetic and the other parity differences now match Spark. The audit's 17 repro tests for these defects all pass.
- The reliability fixes are merged: killed scheduled runs, kept and bounded logs, agent job leases, and saves flushed to disk.
- The supply-chain gaps are closed: SHA-256 checks, hash-locked engine packages, and a gated Preview publish.
- Engine tests grew from about 8,700 to 10,736, and they all pass. There is a start-up, notebook and SQL benchmark, and the app window has performance budgets in CI.

Two things hold the score down:

1. **Two release-critical proofs are missing.** The builds aren't signed yet, which waits on the Trusted Signing identity check. And nothing has been run against a live Fabric tenant. Until both are done, no category can be rated enterprise-ready.
2. **Enterprise packaging is incomplete.** A per-user MSI now ships next to the exe, but it is unsigned and hasn't been tried in an Intune or Configuration Manager tenant yet. There is no MSIX package, and no published privacy statement or support terms.

## Top risks

| Risk | Why it matters | Next step |
| --- | --- | --- |
| Unsigned builds (EA-009) | App Control and EDR block or flag the app, and updates are only as trustworthy as GitHub | The maintainer finishes the identity check |
| No live tenant evidence (EA-009) | Every Fabric integration, and every fix above, is proven only against fakes and local Delta tables | One harness run on the non-production test workspace, with the maintainer's go |
| MSI untried in a managed tenant | IT can't yet rely on Intune or Configuration Manager installs | An Intune or Configuration Manager test install once builds are signed |
| Update rollback after a failed engine start (EA-024) | A build that opens but whose engine never starts can't go back to the previous build | A Windows check of the draft fix |
| Copied plumbing (EA-028) | Fixes to atomic writes, retries and redaction must be made in several places | Keep moving callers to the shared helpers |

## Completed so far

- Baseline checks run and recorded. There are seven audit documents and a backlog of 30 findings, one of them withdrawn. Each finding has evidence, an owner and a priority.
- 24 findings are fixed and merged (see the Progress table in the [remediation backlog](REMEDIATION_BACKLOG.md)). EA-027 and EA-028 are under way. EA-009 waits on the maintainer, EA-024 waits on a Windows check, and EA-030 needs a Windows PC.

## Next actions

1. The maintainer's steps: signing, then the live harness run. They are the gate to any category reaching 4.
2. A Windows check of the update rollback (EA-024), and the Narrator pass (EA-030).
3. Keep closing EA-027 (lint rules) and EA-028 (shared plumbing).
4. Publish the privacy statement and support terms.

The scorecard is re-scored at the start of each audit cycle. A score rises only with new evidence: a merged fix with its test, or a recorded live run.
