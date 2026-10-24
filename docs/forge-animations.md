# Motion and effects

KratosForge shows a few small animations when work changes state. They add a little of the wolf
and forge identity without getting in the way, and you choose how much of it you see.

Nothing else changes with them: labels, times, outputs, progress and errors are the same at every
level. No effect delays a run, takes keyboard focus, catches the pointer, moves other content or
covers code, tables or errors. Effects only follow work that has already happened; they never start,
wait for or change it, and none of them calls Fabric.

## Choose how much moves

**Settings > Appearance > Motion & effects**. Changes apply at once, even during a run, and are kept.

| Level | What you see |
| --- | --- |
| **0 Off** | No animation at all |
| **1 Minimal** | Quick page and panel transitions only (a page slides 6 px in the direction you moved on the rail, 0.16 s) |
| **2 Refined** (default) | Subtle sparks, embers and the wolf on real progress |
| **3 Expressive** | The same moments with more character: a halftone ring and an offset outline on the anvil strike, a touch of depth on page changes |

At Refined and Expressive, a switch for each family turns it off:

| Switch | Effects |
| --- | --- |
| Wolf animations | The wolf on Run all progress, its arrival, the Wolf Run after long work, the navy wolf on runs in Fabric |
| Forge sparks | Forge Spark, Anvil Strike, the cooling spark, the failure ember |
| Running indicators | The glow on a running cell, the flow into a running pipeline step, the ring when Run all starts |

**Preview effects** plays each effect on a sample, and runs a three-step sample pipeline on this PC
(two short waits and a variable) to show Energy Flow. That run is kept in this PC's local pipeline
run history like any local run; nothing goes to Fabric.

There is no sound.

### Windows' reduced motion

When Windows' **Settings > Accessibility > Visual effects > Animation effects** is off, KratosForge
shows no motion at all, whatever the level. To keep your level anyway, untick **Respect Windows'
reduced-motion setting**.

### For IT

Every key can be fixed in the policy file ([Azure setup](azure-setup.md#4-push-settings-to-everyone)):
`"motion_level": 0` to `3`, `"motion_wolf"`, `"motion_sparks"`, `"motion_achievements"` (off by default),
`"motion_ambient"` and `"motion_follow_system"` (`true` or `false`). The earlier
`"forge_animations": false` still locks motion Off unless the policy also sets `motion_level`.

## Every effect and when it plays

| Event | Effect | Where | Length |
| --- | --- | --- | --- |
| A cell you ran succeeds | **Forge Spark**: a tiny orange spark leaves **Succeeded** | the cell's status | 0.4 s |
| A cell you ran succeeds after 10 s or more | **Wolf Run** instead of the spark: the wolf crosses a small lane at the right of the status bar, leaving a few embers | status bar | 0.9 s |
| A cell fails | **Controlled Failure**: a small ember beside **Failed** dims and goes out; the red label and the error are there from the first frame | the cell's status | 0.45 s |
| Run all (or Run above, Run below) starts | **Pulse**: one orange ring off the Run all button | the button | 0.35 s |
| Run all takes longer than 0.6 s | The status bar says **Cell 3 of 8 on this PC**, with the wolf at the share of cells done and embers rising behind it | status bar | until the run ends |
| Run all finishes | **Wolf Arrival**: the wolf glows once at the end and the track goes | status bar | 0.55 s |
| A job the app started in Fabric is running | The bell and Home show the wolf trotting in place in Kratos navy on an empty track, with **Running in Fabric** beside it. Fabric reports no measurable progress, so there is no fill, percentage or time estimate; with the wolf off, the plain indeterminate bar shows | the bell, Home | while running |
| A cell is running | A small breathing ember on **Running** (forge orange, because notebook cells run on this PC) | the cell's status | while running |
| A notebook runs in Fabric (Runs on: Fabric, Run all) | **Pulse** on Run all once Fabric has the run; the status bar shows the navy wolf trotting in place with "Running in Fabric" and Fabric's state, no fill or percentage; the status bar's "Last Fabric run" glows once when it completes, or shows the failure ember when it fails. Cancelled plays nothing | Run all, then the status bar | 0.35 s, while running, 0.55 s |
| A SQL query succeeds after more than 2 s | **Forge Spark** on **Succeeded**. Quicker queries show nothing | the Results band | 0.4 s |
| A SQL query fails or times out | **Controlled Failure**: the ember beside **Failed**; the error is there from the first frame | the Results band | 0.45 s |
| You start a pipeline run (Pipelines > Run on this PC) | **Pulse** on **Stop** as the run view opens | the run's Stop button | 0.35 s |
| A pipeline activity runs | **Energy Flow** above the run's time line: the link into the running activity carries a moving dash; each activity says Queued, In progress, Succeeded, Failed, Skipped or Cancelled in words | pipeline flow | while running |
| A pipeline activity finishes | It settles with one glow (succeeded) or the failure ember, once, while you watch | the activity | 0.45 s |
| A pipeline run you're watching ends | **Anvil Strike** when it succeeds; a run with mocks isn't a real success and gets none. A failed run shows the one ember of the activity that failed. A run opened after it ended plays nothing | the flow | 0.65 s |
| A Validate clean run passes (Ready, or Ready with warnings) | **Cooling spark**: the verdict glows forge orange, then cools to the success colour. A run that isn't ready gets the failure ember beside its verdict; a cancelled run plays nothing | the Validate verdict | 0.5 s |
| Prove finds both runs made the same results | **Cooling spark** on the **Same** verdict; **Differs** or a failed proof gets the failure ember | the Prove verdict | 0.5 s |
| Publish to Fabric, once Fabric gives back the notebook item it saved | **Cooling spark** on the "Published" message; a failed publish gets the failure ember beside the error | beside the message | 0.5 s |
| A platform deployment succeeds (Source control) | **Anvil Strike**: a small anvil drops onto the "Deployed" message and two sparks fly. No shake, no flash | beside the message | 0.65 s |
| A platform deployment fails (Source control) | **Controlled Failure**: the ember beside the note saying where the deploy stopped, or beside the error when the engine refused it. A cancelled sign-in plays nothing | beside the note or error | 0.45 s |
| You save a notebook yourself (**Save**, or **Ctrl+S**) | **Forge Spark** once the save is written: on **Save**, or on the "Saved" message for Ctrl+S. At most one every 5 s; autosave and the saves other actions make first (Publish, Run in Fabric, closing) never spark | the Save button or the message | 0.4 s |
| Profile awards you an achievement | **Achievement Reveal**: a ring and an offset outline reveal the message naming it, once per award (never again for the same award, even after a restart). Off unless you turn on **Achievement celebrations**, shown while Preview features are on | the achievement message | 0.9 s |
| A dialog opens or closes (Minimal and above) | The dialog rises and fades in; focus is inside it from the first frame. On close it is gone at once and a blank card fades where it was. **Escape** closes with no fade | the dialog | 0.2 s in, 0.16 s out |
| The Activity tray or a side pane opens (Explorer, Variables, Comments, Runs, Prove, Validate; Minimal and above) | It slides 16 px in from its edge and fades in. A page that opens with its panes doesn't animate them, and redraws never replay it. The tray leaves like a dialog | the tray or pane | 0.22 s |

During Run all, cells don't spark one by one; the track shows the progress instead. Stop and
Cancel show nothing, on a notebook or a SQL query. Activities inside ForEach and Until are counted on their parent
("In progress · 4 of 9"), never animated one by one. A pipeline flow always says where it runs:
**Local run on this PC (not run in Fabric)** for a local run.

## Screen readers

Effects are hidden from screen readers. Instead the app says once when a cell you ran succeeds or
fails, once when a run of several cells finishes, fails or is stopped ("Run finished: 8 cells
succeeded", "Run stopped: cell 3 failed", "Run stopped"), and once when the sample pipeline ends.
The run track is a progress bar with its value.

## Performance

Effects are CSS `transform` and `opacity` animations on one small element each, so the browser's
compositor draws them without layout. The engine keeps them cheap:

- At most three effects play at once; more are skipped, never queued. Each kind has a minimum gap
  (a spark at most every 0.3 s, a Wolf Run every 5 s), and an element never has two at once.
- Nothing is made when the window is hidden, for an element that is off screen or gone, or below
  the effect's level. Ambient loops pause while the app isn't in front.
- If the app's main thread is busy (long tasks adding up to 0.5 s within 5 s), effects drop one
  level for 30 s.
- Every effect removes itself when it ends; a redraw of the page removes any still playing.

Measured with `app/tests/browser/motion-benchmark.cjs` (headless Chromium 141, Linux build machine,
2026-10-20; compare against your own runs rather than reading these as a promise for a given PC):

| Measure | Off (0) | Expressive (3) |
| --- | --- | --- |
| Run all, 20 cells, real engine (three runs) | 380, 344, 291 ms | 365, 324, 307 ms |
| Frame time during 200 events in 10 s: median / 95th / worst | 16.7 / 16.7 / 16.8 ms | 16.7 / 16.8 / 33.3 ms |
| Long tasks during the burst | 0 | 0 |
| Effect elements left afterwards | 0 | 0 |
| Main-thread time per `forgeEvent()` call | 0.10 ms | 0.23 ms |

Run all times overlap between levels (the spread is the engine, not the effects). One frame in the
Expressive burst took two vsyncs; no long task was recorded.

## For developers

`app/ui/motion.js` is the motion engine. Features don't animate on their own: they report what
happened, and the engine decides whether and how it shows.

- `forgeEvent(name, anchor)`: something finished. `FORGE_EVENTS` maps the name to an effect
  kind; unknown names play nothing. Call it only after the state change is real (a status the
  engine returned), never to predict one.
- `fx(anchor, kind)`: plays one kind over an element. Returns the element, or `null` (and
  `motionDiagnostics().skipped` says why: level, family, gone, busy, budget, offscreen, gap).
- `MOTION`: each kind's length (`ms`), minimum gap, lowest level, family and optional `build`
  for extra parts (the anvil, the running wolf).
- `wolfTrack(where, label, unit, engine)`: step progress with the wolf, real fractions only.
- `remoteProgress(label, where)`: an indeterminate bar for a run elsewhere (in Fabric), with the
  navy wolf trotting in place and the words saying where it runs. No value, no fill.
- `pipelineFlow(container)`: `update(status)` draws a `pipeline_run_status` result as linked
  activities and fires the activity events.
- `viewTransition(view)`: called by `setView`; animates only when the page changes.
- `motionLevel()`, `motionOn(family)`, `applyMotion()`, `announce(text)`, `motionDiagnostics()`.

To add an effect: add a kind to `MOTION` with a family and level, map an event to it in
`FORGE_EVENTS`, write its CSS in `styles.css` under "Forge animations" or "Motion & effects"
(keyframes named `fx-`, transform and opacity only, `pointer-events: none`), and give it a row in
the Preview effects window. Tests in `app/tests/ui.test.cjs` check every event names a real kind,
every kind has a family and a level of 2 or more, keyframes animate only transform and opacity, and
no more than three effects play at once. `app/tests/browser/motion-effects.test.cjs` checks focus,
pointer, reduced motion, settings and a real pipeline run.
