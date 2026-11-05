# Executive summary: enterprise engineering audit

Baseline: `main` at `b75dcf5`, 2026-11-02. Full scorecard: [Enterprise readiness](ENTERPRISE_READINESS.md).

## Where KratosForge stands

**Score: 54 out of 100. Not enterprise-ready yet.**

The engineering underneath is stronger than that number suggests:

- About 8,700 engine tests, 315 UI tests, and browser, Rust and accessibility suites all run on every change.
- The local API, approvals, the SQL sandbox, deep links and the token cache held up in a security review. No serious security defect was found on any inbound surface.
- CI pins every action, scans dependencies, and publishes SBOMs and provenance attestations.

Three things hold the score down:

1. **Two release-critical proofs are missing.** The builds aren't signed yet, which waits on the Trusted Signing identity check. And nothing has been run against a live Fabric tenant. Until both are done, no category can be rated enterprise-ready.
2. **Data integrity has confirmed gaps.** Appending to a table can quietly change values to fit the column types: 2.75 becomes 2, and a timestamp loses its time. Some local calculations also give Fabric different results or different column types: casts round instead of truncating, and sums are written as decimals. An overwrite can keep rows another writer added during the upload.
3. **Enterprise packaging is incomplete.** A per-user MSI now ships next to the exe, but it is unsigned and hasn't been tried in an Intune or Configuration Manager tenant yet. There is no MSIX package, and no published privacy statement or support terms.

## Top risks

| Risk | Why it matters | Next step |
| --- | --- | --- |
| Unsigned builds (EA-009) | App Control and EDR block or flag the app; updates are only as trustworthy as GitHub | Finish the identity check (the maintainer) |
| No live tenant evidence (EA-009) | Every Fabric integration is proven only against fakes | One harness run on the non-production test workspace, with the maintainer's go |
| Appends convert values silently (EA-001) | Numbers and dates in shared tables change with no error | Fix routed to the OneLake write path owner |
| Local results differ from Fabric (EA-003, EA-004) | A notebook that passes locally can write different numbers or a different schema | Fixes routed to the parity owner |
| Concurrent and partial writes (EA-005, EA-006, EA-007) | A table or file can end up different from what was reported | Fixes routed to the OneLake write path owner |

## Completed so far

- Baseline checks run and recorded. Seven audit documents, including a backlog of 30 findings (29 open, one withdrawn), each with evidence, an owner and a priority.
- **First remediation PR:** notebook Parquet and CSV file writes now follow Spark's save mode. Before, `append` silently replaced the file. A non-hermetic test was also fixed.

## Next actions

1. Owners fix EA-001, EA-003, EA-004 and EA-005, each with a regression test that checks the table afterwards.
2. Reliability fixes: a scheduled run that was killed must not block the next one (EA-008), and the crash log must be kept (EA-010).
3. Supply-chain tightening (EA-020), through Release management.
4. The maintainer's steps: signing, then the live harness run.

The scorecard is re-scored at the start of each audit cycle. A score rises only with new evidence: a merged fix with its test, or a recorded live run.
