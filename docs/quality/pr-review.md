# PR quality gate

How every pull request to Fabric Desktop by KratosBI gets an engineering quality decision. It sits beside the hardening release gates (`docs/hardening/readiness-checklist.md`, PR #137) and the usability gates (`docs/ux/release-review.md`, PR #166) and doesn't repeat them: those ask "is it safe" and "is it usable"; this asks "does this code deserve to exist in this form". Release management decides merges and promotes; this review recommends, and never merges or deploys.

## Scoring

Each category is scored 0 to 10 from evidence in the diff, the tests and the [standards](architecture.md#standards-the-code-follows).

| Category | Weight | A 9 looks like | A 5 looks like |
| --- | --- | --- | --- |
| Functional correctness | 20% | Does what it says on every path, including failures; nothing reports success after a failure | Happy path works; some failure path guesses, or reports success |
| Architectural consistency | 15% | Uses the existing helper for each concern (client factory, `fsutil`, `sqlite_util`, `retrying`, `redact`, `timeutil`, `confirmDialog`, `attempt`) | Adds a new copy of something the repo already has |
| Code quality and maintainability | 15% | Small, named for what it does, no dead code, no broad silent `except` | Long functions, silent handlers, unexplained `type: ignore` |
| UX consistency and simplicity | 15% | Uses shared components, fits the navigation budget, no new configuration | Bespoke dialog or tab bar, a new setting that could be automatic |
| Testing and verification | 15% | A failing-first test for every behavior change, failure paths tested, test names match what they prove | Tests only assert what a fake was told to return |
| Security | 10% | Hardening gates hold; no new scope without `docs/permissions.md` | Unreviewed token, permission or file handling |
| Performance and efficiency | 5% | No new polling or repeated remote calls; local work stays local | New loop or repeated lookup with no limit |
| Documentation | 5% | The doc section exists, matches behavior and says what is local, compatible, simulated, remote or unsupported | Feature names repeated without behavior |

**Decision:** 9.0 and up PASS; 8.0 to 8.9 CONDITIONAL PASS (named follow-up, verified by Release management before the stated point); below 8.0 FAIL.

## Automatic failures

Any one of these is FAIL whatever the score, and can't be conditional:

1. A critical security vulnerability.
2. A known data-loss defect, including an overwrite or delete wider than the user asked for.
3. Functionality presented as working that isn't: an ignored argument, a hard-coded "measured" value, a success message after a failure.
4. A claim of test success that wasn't run.
5. A broken critical workflow.
6. A material architecture change without agreement (a new process, framework, store format or pattern for a concern the repo already handles).
7. Missing authorization control on a write.
8. A known high-severity regression.

## Investigation triggers

These start a closer look; they aren't failures on their own.

| In the diff | Check |
| --- | --- |
| `except Exception` | Does it tell "not set up" from "failed"? Does a failure reach the user? (QA-013) |
| `write_text(json.dumps(...))`, `sqlite3.connect`, `datetime.now`, `re.compile` of secret patterns | Use the shared helper (QA-011, QA-022, QA-024, QA-016) |
| `FabricClient(`, `FileStore(`, `._token(`, `requests.` | Use `Service.fabric` / `files` / `token` (QA-014) |
| A new name in `server.METHODS` | Is it called from the UI or tagged? Is `audit.READ_ONLY` right? (QA-015, QA-017) |
| A new `*_service.py` mixin | Could it be a module called by an existing mixin? |
| `showDialog` with a "…?" title, a `tab` button, a `pill-s` span, `setTimeout` loop | Use the shared UI helper (QA-019) |
| "no capacity", "identical to Fabric", "measured" | Is it verified on this execution path? (QA-008) |
| A test with `monkeypatch.setattr(svc, ...)` or a new `FakeFabric` | Does the test prove the code, or the fake? (QA-018) |
| A Fabric-writing button | Is it under a Preview tag? (QA-020) |

## Report format

One block per PR, pasted into the release record by Release management.

```
PR: #<number> <title>
Quality decision: PASS | CONDITIONAL PASS | FAIL
Score: <overall> (correctness x, architecture x, code x, UX x, tests x, security x, performance x, docs x)
Automatic failures: <none, or which and why>
Findings: <QA-id or new id, severity, file:line, one line each>
AI slop: <duplication, needless complexity, incomplete or simulated behavior, or none>
Required remediation: <what must change, or the condition for CONDITIONAL>
Tests: executed and passed | executed and failed | not executed | not verifiable without Fabric
Release decision: <merge now | merge after X | hold>
```

Every review says which tests were actually run in this review. "Static review only" when none were.

## Reviews

PR reviews start after this baseline merges and are added below, newest first.
