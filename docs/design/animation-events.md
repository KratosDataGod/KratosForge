# Animation event catalog

Every decorative animation is the answer to an event. Features report the event through
`forgeEvent(name, anchor)` in `app/ui/motion.js`; the engine decides whether and how it shows,
based on the person's level, the family switches, the budget of three live effects and the gaps
below. This catalog is the contract between the nine categories in the
[visual style guide](KRATOSFORGE_VISUAL_STYLE_GUIDE.md#animation-categories), the four tiers in the
[animation style guide](../animation-style-guide.md) and the kinds in `MOTION`.

Columns: **Event** is the name passed to `forgeEvent`. **Kind** is the `MOTION` entry it plays
today (`planned` means the event or kind does not exist yet and is proposed here). **Region** is
the largest area the effect may touch. **Lowest level** is the first intensity level at which it
plays (1 Minimal, 2 Refined, 3 Expressive). **Reduced motion** is what plays instead when the OS
or the person asks for less.

## Application launch

| Event | Kind | Tier | Region | Length | Lowest level | Reduced motion |
| --- | --- | --- | --- | --- | --- | --- |
| `app.launched` | planned `launch` | Forge | The launch screen only, before the first page; never after the engine is ready | 1.6 s, cut short the moment the engine reports ready | 2; at 1 a 300 ms fade | The wordmark and phase words, no fire, no bar animation |

The launch never delays readiness: the progress bar is driven by real engine start-up states, and
a ready engine ends the sequence immediately.

## Notebook execution

| Event | Kind | Tier | Region | Length | Lowest level | Reduced motion |
| --- | --- | --- | --- | --- | --- | --- |
| `notebook.run.started` | `pulse` | Spark | One ring off the Run button | 350 ms, gap 1 s | 2 | none |
| (while running) | `wolfTrack` | Ember | The progress track beside the status, 8 px of moving mark | Loops while the state lasts | 2 | A static wolf mark at the real fraction |
| `notebook.cell.succeeded` | `spark` | Spark | Beside the cell's status, 24 px | 400 ms, gap 300 ms | 2 | none |
| `notebook.cell.failed` | `fail` | Spark, restrained | Beside the cell's status | 450 ms, gap 300 ms | 2 | none |
| `notebook.run.finished` | `arrive` | Forge | The end of the track | 550 ms | 2 | none |
| `notebook.long.succeeded` | `wolfrun` | Forge | The status bar lane, 160 by 48 px | 900 ms, gap 5 s | 2 (more character at 3) | The arrival still |

## SQL execution

| Event | Kind | Tier | Region | Length | Lowest level | Reduced motion |
| --- | --- | --- | --- | --- | --- | --- |
| `sql.started` | planned, reuse `pulse` | Spark | The Run button | 350 ms | 2 | none |
| `sql.succeeded` | planned `splash` (sparks and an orange ink splash behind the result card) | Spark | The result card's header, sparks thrown up and right, never over the grid | 500 ms | 2; splash at 3 only | The result card, no splash |
| `sql.failed` | `fail` | Spark, restrained | The error panel's edge | 450 ms | 2 | none |

## Pipeline execution

| Event | Kind | Tier | Region | Length | Lowest level | Reduced motion |
| --- | --- | --- | --- | --- | --- | --- |
| `pipeline.started` | `pulse` | Spark | The Run button | 350 ms | 2 | none |
| (while running) | `pipelineFlow` | Ember | Only the links whose activity is actually running | Loops while running | 2 | Static highlighted link |
| `pipeline.activity.succeeded` | `settle` | Spark | The activity node | 450 ms | 2 | none |
| `pipeline.activity.failed` | `fail` | Spark, restrained | The activity node | 450 ms | 2 | none |
| `pipeline.succeeded` | `strike` | Forge | The run's status region | 650 ms, gap 1.5 s | 2 | The anvil still |
| `pipeline.failed` | `fail` | Spark, restrained | The status region | 450 ms | 2 | none |

Energy flows along actual execution paths with accurate states; the flow never runs ahead of
what the engine reported.

## Deployment

| Event | Kind | Tier | Region | Length | Lowest level | Reduced motion |
| --- | --- | --- | --- | --- | --- | --- |
| `deploy.succeeded` | `strike` | Forge | The deployment panel's status region: a compact anvil strike, a short spark burst, then the success indicator | 650 ms, gap 1.5 s | 2 | The anvil still |
| `deploy.failed` | `fail` | Spark, restrained | The status region | 450 ms | 2 | none |
| `deploy.first.prod` | planned, `achievement.unlocked` | Titan | The window, then the desktop overlay | 6.5 s, skippable | 3, with celebrations on | The emblem still with the title for 2 s |

## Optimization

| Event | Kind | Tier | Region | Length | Lowest level | Reduced motion |
| --- | --- | --- | --- | --- | --- | --- |
| `optimize.applied` | planned, reuse `strike` | Forge | The result card | 650 ms | 2 | The anvil still |
| `optimize.estimated` | none | | | | | |

Only a validated improvement gets the strike. Savings shown beside it are labelled estimated or
measured; an estimate never celebrates.

## Achievements

| Event | Kind | Tier | Region | Length | Lowest level | Reduced motion |
| --- | --- | --- | --- | --- | --- | --- |
| `achievement.unlocked` (badge) | `achieve` | Forge | The profile card or toast, 160 by 48 px | 900 ms, gap 3 s | 2 | The badge, no ring |
| `achievement.unlocked` (milestone) | planned `titan` | Titan | The whole window and the desktop overlay, only when "Let celebrations leave the window" is on | 6.5 s, any key or click ends it | 3, celebrations on, at most once per milestone and never twice in 10 minutes | The emblem still with the title |

Milestones are the ones the profile marks as such (first deployment to Prod, a level reached, a
release shipped). Everything else is a badge.

## Error states

| Event | Kind | Tier | Region | Length | Lowest level | Reduced motion |
| --- | --- | --- | --- | --- | --- | --- |
| any `*.failed` | `fail` | Spark, restrained | The edge of the error panel; the message is on screen from the first frame | 450 ms | 2 | none |
| (panel art) | still | none | The snarling wolf still beside the panel at Expressive, crimson and orange, never animated | | 3 | the same still |

No celebration, no shake, no splash, no sound in an error state. The diagnostic text is never
covered.

## Navigation

| Event | Kind | Tier | Region | Length | Lowest level | Reduced motion |
| --- | --- | --- | --- | --- | --- | --- |
| `setView` | `viewTransition` | Spark | The page content, only when the page changes | 200 to 300 ms | 2 | A cut |
| `rail.selected` | planned `streak` | Spark | The rail's active marker and a 24 px streak along the rail | 500 ms | 2; the wolf flash at 3 only | The marker moves without the streak |

## Rules the catalog enforces

- Unknown event names play nothing. Add the event here and in `FORGE_EVENTS` in the same PR.
- An event fires only after the state change is real: a status the engine returned, never a
  prediction.
- Every kind has a family (`wolf`, `sparks`, `ambient`, `achievements`) so the person can turn it
  off, and a level of 2 or more. Tests in `app/tests/ui.test.cjs` check both.
- At most three effects are live at once; the engine drops the fourth and records why in
  `motionDiagnostics()`.
- A planned kind is added to `MOTION` with a `tier` field from the
  [animation style guide](../animation-style-guide.md#where-tiers-live-in-the-code), a row in the
  Preview effects window, and a row in this catalog.
