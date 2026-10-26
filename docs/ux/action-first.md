# Action-first experience

Every screen in KratosForge should make the best next action obvious without making any other permitted action harder. This page holds the standard a screen is reviewed against, the measured baseline, and the action-first backlog. The shared code is `app/ui/actions.js`; how to use it is in [UI guidelines](../ui-guidelines.md#action-first-standard).

## The standard

A major screen answers five questions: where am I, what can I do, what should I do, why, and what happens next. In practice it has four things:

| Part | What it means | How it's built |
| --- | --- | --- |
| Primary action | The most valuable action for where the person is, chosen from their context, not hard-coded. One solid button | `actionBar(ctx)`, or the action marked `primary` |
| Recommended next action | Shown only when the app holds evidence for it: a failed run, validation warnings, unsaved work. Says why, and whether that's verified or an estimate | `defineRecommendation` and `nextSteps(ctx)` |
| Alternative actions | Every other permitted action stays reachable: the More menu, the command bar (Ctrl+K), shortcuts, right-click | Every `defineAction` is in the command bar |
| Feedback | What happened, what changed, what failed and how to recover, and the next step | `runAction` (Retry when safe) and `doneToast` |

Recommendations never use made-up confidence scores. They are ranked by their reason alone: a failure to fix, then a safety or security issue, a validation warning, unfinished work, a local alternative, then continuing. When the top two share a reason, the screen says "You could" and shows both, rather than claiming one is best.

### Safety

Action-first never means acting unasked. Actions carry a risk level:

| Risk | Examples | What happens |
| --- | --- | --- |
| Low | Open, inspect, preview | Runs at once |
| Moderate | Run on this PC, change a development item | Runs, with feedback and a way to recover |
| High | Delete, overwrite data, deploy to Prod, change permissions | Asks first in a danger dialog (`confirmDialog` with `danger`; `typeToConfirm` for Prod and overwrites) |

`defineAction` refuses a high-risk action that has no confirmation, and an action that spends Fabric capacity without the risk check. The risk check is advice and never blocks; when it can't run, it says so. Permissions are enforced by the engine, never only by what the screen shows: an action that changes Fabric or data asks the engine first, as the Workload page does with `workload_preflight`.

## Review checklist

Every pull request that adds or changes a screen shows, in its description:

- [ ] A clear primary action, chosen from context
- [ ] Every other permitted action still reachable (menu, command bar, keys); none removed
- [ ] Recommendations only from evidence the app holds, labelled Verified or Estimate
- [ ] Unavailable actions say why, where that can be disclosed safely
- [ ] Feedback for every action, with recovery on failure and the next step on success
- [ ] High-risk actions confirmed; capacity-spending ones risk-checked
- [ ] Keyboard and screen reader can reach every action
- [ ] Animations only through `forgeEvent` after a real state change (owned by the motion work)

## Baseline

Measured by `app/tests/browser/action-baseline.cjs` at `main` 0a2f24e on 26 October 2026: signed out, no tenant, a 1440x940 window, the real engine. Run it again after each change and add a column.

| Measure | Baseline | After AF-2 |
| --- | --- | --- |
| Home: solid buttons | 3, all sign-in (Sign in, Sign in, Sign in and scan) | 2 signed out with no notebooks (Sign in, Sign in and scan); Continue is solid once a notebook exists |
| Home: New notebook position | 1,798 px down, not in the first view; outlined | 176 px down, in the first view; outlined |
| Notebook (new, empty): solid buttons | Publish to OneLake, its options arrow, and Add data items in the empty explorer | Unchanged |
| Notebook: Run all | In view, not the solid button | Unchanged |
| Optimize (no shared lakehouse): solid button | Create a lakehouse | Unchanged |
| Command bar finds Run all, Run cell, Validate, Publish, Run in Fabric | No (only Schedule) | Unchanged |

Rates that need real use (recommendation acceptance, failure and recovery) are not measured. They could only come from the opt-in telemetry, which is off by default.

## Backlog

| Id | Package | State |
| --- | --- | --- |
| AF-1 | Shared action framework, this standard and the baseline | Merged (#722) |
| AF-2 | Home: Continue, Start something and Recent notebooks first; one sign-in prompt; a signed-out summary that says Fabric wasn't checked | In review |
| AF-3 | Feedback that leads somewhere: finished actions offer the next step; safe reads offer Retry | After AF-1 |
| AF-4 | Notebook run-first: Run is primary, Publish recommended once there is something to publish; notebook actions in the command bar | Mockup; Publish as primary was a deliberate choice, so this is Chris's call |
| AF-5 | Optimize opportunities that act: top three with evidence and a Fix that opens the X-ray fix; useful before a shared lakehouse exists | Mockup |
| AF-6 | OneLake and Workspaces signed out: say what appears after sign-in instead of a blank pane | Open |
| AF-7 | Monitor failed rows: Run again where the engine already supports it | Open |
| AF-8 | Platform Overview: lead with the first setup step and waiting requests | Open |
| AF-9 | Unavailable buttons say why (start with the notebook's) | Open |
