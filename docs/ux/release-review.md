# Usability release gates and PR reviews

How a pull request that changes what people see gets a UX review, and the gates a release candidate passes. These sit alongside the codebase audit's release gates in `docs/hardening/` (PR #137); they add usability, they don't repeat security or correctness. The Release Manager decides merges and promotes; this review recommends.

## Usability gates for a release candidate

| Gate | Passes when | How it's checked |
| --- | --- | --- |
| UG-1 Help links resolve | Every in-app help link opens an existing page and heading | The help-link test (UX-002) in the `ui` CI job |
| UG-2 Preview is labelled | Every feature not tried on a tenant is in `PREVIEW_FEATURES` and tagged | Review of the PR diff; the generated Preview list (UX-019) |
| UG-3 No raw errors on new screens | New or changed screens route failures through `friendlyError` or `errorPanel` | Review of the diff; grep for `String(err` and `.message` in new UI code |
| UG-4 Navigation budget | No new rail item, no Monitor segment past the agreed set, no Platform tab past nine, without an agreed mockup | Review against [navigation.md](navigation.md) |
| UG-5 Keyboard path | The PR's main action can be done by keyboard | Review; browser test where one exists |
| UG-6 Destructive actions confirm | Deletes and overwrites of data use `confirmDialog` with danger styling and Cancel as the default (UX-018); nothing mutates OneLake without a preview or confirmation | Review (AGENTS.md data rule) |
| UG-7 Docs match | The feature's doc section exists and matches the behavior | Review; docs build in strict mode |

**Blocking for Stable:** UG-1, UG-2 and UG-6 failures block a promote. UG-3, UG-4, UG-5 and UG-7 failures are recorded and become backlog items; they block only when the Release Manager and Chris agree.

## Review format

Each review is one block the Release Manager can paste into the release record.

```
PR: #<number> <title>
UX review: PASS | CONDITIONAL PASS | FAIL
Blocking findings: <none, or UG-n: one line each>
Nonblocking findings: <one line each, with file:line>
Required remediation: <what must change, or the condition for CONDITIONAL>
Evidence: <screenshots, diff lines, test names>
Tests run: <what was actually run, and the result; "static review only" when nothing was run>
Documentation: <complete | gaps listed>
```

- **PASS:** no gate fails.
- **CONDITIONAL PASS:** no blocking gate fails; mergeable now, with a named follow-up before a stated point (the next Stable promote, the next feature in the same area).
- **FAIL:** a blocking gate fails. Merge waits for the fix.

## Reviews of open pull requests, 2026-10-18

Static reviews of the diffs against `main` (`3bee0db`); nothing was run unless stated. PRs that change no UI (#137-#141, #143, #150, #152, #154-#159, #161, #162) need no UX review. #131 comes from outside the project; it is reviewed for UX only.

```
PR: #145 / #160 / #163 Monitor > Queue (workloads 5C) and stacked PRs
UX review: CONDITIONAL PASS
Blocking findings: none
Nonblocking findings: adds a seventh Monitor segment (Queue) after Workloads, Incidents, Agents, Schedules, Cost (app.js panel list); Run as would be the eighth.
Required remediation: agree the Monitor grouping in UX-010 before an eighth segment merges.
Evidence: diff adds preview("queue"), panel === "queue"; one learnMore added; docs/user-guide.md updated.
Tests run: static review only.
Documentation: complete for the feature (user-guide, agent.md, workloads docs).
```

```
PR: #142 / #148 Prove: failure replay bundles
UX review: CONDITIONAL PASS
Blocking findings: none
Nonblocking findings: no help link added on the replay screen (UG-7 note); notebooks.md updated.
Required remediation: add a learnMore or a link to prove/how-proofs-work.md in the replay panel when next touched.
Evidence: diff adds Preview flag "replay" in features.js, aria-labels on new buttons.
Tests run: static review only.
Documentation: notebooks.md and azure-setup.md updated; troubleshooting row missing.
```

```
PR: #151 Roll Test or Prod back to an earlier release (Preview)
UX review: CONDITIONAL PASS
Blocking findings: none found statically; the rollback design question (release record vs project files) is open with Chris and decides what the dialog must warn about.
Nonblocking findings: no help link on the new control.
Required remediation: the rollback confirmation states exactly what changes in the target environment (UG-6) once the design is settled.
Evidence: diff adds preview("rollback"), friendlyError use.
Tests run: static review only.
Documentation: metadata-guide.md updated.
```

```
PR: #153 Import measured CU from a Capacity Metrics export (FINOPS-003)
UX review: PASS
Blocking findings: none
Nonblocking findings: none. It uses a file input (a picker), not a typed path.
Required remediation: none.
Evidence: diff adds preview("usage"), confirmDialog before removing an import; docs/finops/usage-import.md added and linked from README and docs/README.
Tests run: static review only.
Documentation: complete.
```

```
PR: #147 Label Block enforcement as not enforced yet (HD-031)
UX review: PASS
Blocking findings: none
Nonblocking findings: none; the label tells the truth about behavior.
Evidence: governance.js text change; governance.md updated.
Tests run: static review only.
Documentation: complete.
```

```
PR: #144 Monitor > Agents warns when a table is scheduled in two places
UX review: PASS
Blocking findings: none
Nonblocking findings: none for UX.
Tests run: static review only.
Documentation: user-guide and agent.md updated.
```

```
PR: #131 Show approved exceptions and warn before they end (outside contributor)
UX review: PASS (UX only; not a merge recommendation)
Nonblocking findings: none.
Tests run: static review only.
Documentation: metadata-guide.md and user-guide.md updated.
```
