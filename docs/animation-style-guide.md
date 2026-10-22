# Animation style guide

This guide says how much KratosForge's animations may move and when each kind is allowed. What they
look like is fixed by the [visual style guide](design/KRATOSFORGE_VISUAL_STYLE_GUIDE.md) and its
canonical storyboard; this guide is the motion half of that identity. It sits above
[Motion and effects](forge-animations.md), which lists every effect that ships today and how people
turn them down, and above the motion engine in `app/ui/motion.js`. Every new effect gets a tier from
this guide before it gets any CSS.

The identity in one line: **Kratos strikes, the wolf runs, the forge glows, winter cools.** Power,
speed, heat and calm, drawn in KratosBI navy and forge orange.

## The four tiers

Effects are grouped by how much of the screen they take and how rarely they may play. A tier is about
scale. It is separate from the person's intensity level (Off, Minimal, Refined, Expressive), which
decides whether a tier plays at all.

| Tier | What it is for | Footprint | Length | How often | Lowest level |
| --- | --- | --- | --- | --- | --- |
| **Ember** | A state that is true right now: running, waiting, connected | Inside one control; at most 8 px of moving mark | Loops while the state lasts; one cycle 1.2 to 1.8 s | Any number on screen, at most 3 animating | Refined (2) |
| **Spark** | Feedback that one thing you did has finished | Beside one status label; at most 24 px | 0.3 to 0.5 s, once | Many per minute, with a minimum gap per kind | Refined (2) |
| **Forge** | A hero moment: work that took a while, or mattered, is done | One region (a status bar, a card, a dialog header); at most 160 by 48 px | 0.6 to 1.2 s, once | A few per session, gap of 1.5 s or more | Refined (2), with more character at Expressive (3) |
| **Titan** | A milestone worth stopping for: first deploy to Prod, a level reached, a release shipped | The whole window, and past its edges onto the desktop (see [Past the window](#past-the-window)) | 1.5 to 2.5 s, skippable at any moment | At most once per milestone, never twice in 10 minutes | Expressive (3), and only when the person has turned on celebrations |

### Ember: the forge is lit

Ember is ambient. It tells you something is alive without asking you to look.

- **Motifs:** a breathing coal, heat shimmer along a line, a single ember drifting up, frost slowly
  forming on something that is waiting in Fabric.
- **Motion:** slow, symmetrical, `ease-in-out`, alternate direction. Never a bounce or a flash.
- **Today:** the breathing ember on a running cell, Energy Flow on a running pipeline link, the
  heat glow behind the wolf on the run track.
- **Rule:** an Ember stops the moment the state stops. It never outlives what it describes, and it
  pauses when the window is in the background.

### Spark: the hammer rang

Spark is the reply to an action. It is quick enough that you see it in the corner of your eye.

- **Motifs:** two or three sparks thrown off a label, a ring off the button you pressed, an ember
  that goes dark on a failure, a puff of frost when something is cancelled.
- **Motion:** fast out, slow fade. `cubic-bezier(.2,.7,.3,1)`. Sparks fly up and away from the
  text, never across it.
- **Today:** Forge Spark, Pulse, Settle, Controlled Failure.
- **Rule:** the result (the word Succeeded, the red Failed, the error) is on screen from the first
  frame. The spark decorates a fact; it never reveals one.

### Forge: the blade is finished

Forge is a small story with a beginning and an end: something drops, strikes, runs or rises.

- **Motifs:** the anvil strike, the wolf running a lane and leaving embers, the wolf arriving at the
  end of its track, a badge revealed with a ring. At Expressive, comic-book touches: a halftone
  burst, an offset ink outline, two or three speed lines behind the wolf.
- **Motion:** a short anticipation, a heavy landing, then a release. Strikes use
  `cubic-bezier(.3,1.4,.5,1)` for weight; runs use `cubic-bezier(.4,0,.2,1)`.
- **Today:** Anvil Strike, Wolf Run, Wolf Arrival, Achievement Reveal.
- **Rule:** stays inside its region and never covers code, tables or errors. No screen shake and no
  full-window flash at this tier.

### Titan: the gods are watching

Titan is the cover of the book. It is the only tier allowed to take over the window, so it is the
one with the strictest rules.

- **Motifs:** the storyboard's panels 5, 6 and 7 in sequence: Kratos the smith at the anvil, the
  hammer coming down with a dome of embers and an orange ink splash, the wolf howling in on an ice
  splash, the title in heavy condensed italic. Painted panels cut by slanted wipes, impact frames,
  speed lines, a camera that creeps, hits and settles, film grain and a vignette over all of it.
- **Motion:** built like a shot in a game trailer: anticipation, impact, settle, title, out. About
  6.5 s with a hard cut to black at the end and a fade back to the desktop.
- **Today:** none in the app. The first-deployment sequence is built as a sample in
  `docs/design/examples/forge-motion-samples.html` (beats in [examples](design/examples.md)) and
  waits on full-size art; step 2 of this project ships it.
- **Rules:**
    - Plays only when nothing is running, no dialog is open and no error is showing, right after the
      milestone is confirmed by the engine.
    - Any key, click or scroll ends it at once and gives the window back exactly as it was. Focus
      never moves.
    - The milestone text is in the app before the Titan starts, so skipping loses nothing.
    - Off unless the person turns on **Achievement celebrations** (policy key
      `motion_achievements`), and only at Expressive.
    - It is the one exception to "no effect covers content" in [Motion and effects](forge-animations.md),
      and that page must say so when the first Titan ships.

### Past the window

A Titan may break out of KratosForge's window: the wolf leaps out of the app and across the desktop,
or embers from the anvil spill over the screen. This is drawn in a second, transparent window that
the app opens only for the length of the Titan.

- The overlay is click-through, never takes focus, never shows in the taskbar or Alt+Tab, and closes
  itself when the Titan ends or is skipped. Input anywhere still goes to whatever is under it.
- It covers only the monitor KratosForge is on, and the art stays anchored to the app window: it
  starts inside the window, travels out, and returns or fades. Nothing plays on a monitor where the
  app isn't.
- It plays only when KratosForge is the window in front. If the app is minimised, behind another
  window or in full-screen presentation mode, the Titan plays inside the window or not at all.
- It has its own switch, **Let celebrations leave the window** (off unless the person turns it on),
  and its own policy key, `motion_desktop` (`true` or `false`), so IT can turn it off everywhere.
  Off means the same Titan plays inside the window only.
- Nothing is captured from the desktop or other apps. The overlay draws only its own art.

## Colour

Every effect uses the KratosBI palette from `app/ui/styles.css`. Fire and winter are built from the
two brand colours so the app never looks like it has two brands.

### Brand

| Token | Value | Use |
| --- | --- | --- |
| `--accent` | `#1b3664` KratosBI navy | The wolf, ink outlines, the emblem, the run track |
| `--accent-dark` | `#12264a` | Shadows and deepest ink in Forge and Titan art |
| `--accent-soft` | `#e9eef7` | Halos behind navy marks on light surfaces |
| `--brand-orange` | `#f8991c` KratosBI orange | Fire: sparks, embers, rings, the anvil |

### Fire (from `--brand-orange`)

| Token (proposed) | Value | Use | In use today |
| --- | --- | --- | --- |
| `--fx-spark` | `#ffd08a` | Spark tips, the hottest point | yes, as a literal |
| `--fx-ember` | `#ffc46b` | Floating embers and their glow | yes, as a literal |
| `--fx-heat` | `#ffb347` | Inner glow of a heat source | yes, as a literal |
| `--fx-flame` | `#f8991c` | Body of fire (same as `--brand-orange`) | yes |
| `--fx-coal` | `#c4560f` | Deep coal, shadow side of flames (Forge and Titan only) | new |
| `--fx-ash` | `#8a8886` | An ember that has gone out | yes, as a literal |

### Winter (from `--accent`)

| Token (proposed) | Value | Use |
| --- | --- | --- |
| `--fx-frost` | `#e9eef7` | Frost haze (same as `--accent-soft`) |
| `--fx-ice` | `#a9c4ea` | Ice crystals, frost edges, snow |
| `--fx-glacier` | `#2f6fd6` | Cold light, the blue in a winter Titan (the docs site's accent) |
| `--fx-storm` | `#12264a` | Night sky and storm clouds (same as `--accent-dark`) |

### What the colours mean

Colour carries meaning, so effects stay honest:

- **Orange is work on this PC.** A running local cell already breathes orange. Fire belongs to local
  compute, success and making things.
- **Blue and frost is Fabric and waiting.** A job queued or running in Fabric, a cooldown, a schedule
  that hasn't started yet. When steel is quenched it goes from fire to frost; the same move marks work
  handed from this PC to Fabric.
- **Ash is failure.** A failure is an ember going out, never an explosion. The red text is the
  message; the effect only softens it.
- **Green is never animated.** `#107c10` is for success borders and labels only.
- Never put moving orange on `--danger` red, and never make red glow.

Contrast: navy on white is about 12:1, so a mark that carries meaning (the wolf on the track) is
navy. Orange on white is only about 2.2:1, so orange is never the only sign of anything; the word
beside it is. Embers, sparks and frost are decoration and may be lighter still.

## Motifs

| Motif | Stands for | Shape language | Motion language |
| --- | --- | --- | --- |
| **Kratos** (power) | Weight, strength, finality: a deploy, a release, a milestone | The anvil, the hammer, chains, the Parthenon columns from the KratosBI mark, heavy horizontal lines | Anticipation, then a heavy landing. Strikes downward. Shockwaves ring outward |
| **Wolf** (speed) | Progress and pace: runs, tracks, long work done | The wolf's head from the Wolf Forge emblem (`WOLF_PATH` in `motion.js`), streaks, paw-mark dots | Always left to right, ease-out, with a trail. The wolf never stops mid-run on screen; it arrives or leaves |
| **Forge** (making) | Building and success | Embers, sparks, flame tongues from the emblem, glowing metal | Upward, flickering, fading as it cools |
| **Winter** (calm) | Waiting, Fabric, cooling, cancel | Frost, ice crystals, snow, breath | Slow drift down or inward, crystal growth from an edge |

Kratos is drawn as a bearded blacksmith at the anvil, never as a fighter. Painted art follows the
[visual style guide](design/KRATOSFORGE_VISUAL_STYLE_GUIDE.md) and the
[wolf character sheet](design/wolf-character-sheet.md), and every piece is listed in the
[asset manifest](design/assets.md). Brand marks come from `docs/brand/`: the Wolf Forge emblem
(`wolf-forge-emblem-2048.png`), the KratosBI logo and mark (SVG), and the KratosForge wordmark.
Small effects trace these into one-path SVGs like `WOLF_PATH`; Forge and Titan effects compose the
painted panels beside the UI, as the storyboard does, never over it.

## Timing and easing tokens

Step 3 adds these as CSS custom properties. Until then, use these values so effects feel like one
family.

| Token | Value | For |
| --- | --- | --- |
| `--fx-t-flick` | 120 ms | Stagger between sparks in one burst |
| `--fx-t-spark` | 400 ms | Spark tier default |
| `--fx-t-forge` | 650 ms | Forge tier default (Anvil Strike) |
| `--fx-t-run` | 900 ms | Wolf runs |
| `--fx-t-titan` | 2000 ms | Titan default; never above 2500 ms |
| `--fx-t-breath` | 1400 ms | One Ember cycle |
| `--fx-ease-out` | `cubic-bezier(.2,.7,.3,1)` | Sparks, rings, anything thrown |
| `--fx-ease-strike` | `cubic-bezier(.3,1.4,.5,1)` | Landings with weight |
| `--fx-ease-run` | `cubic-bezier(.4,0,.2,1)` | Wolf movement |
| `--fx-ease-breath` | `ease-in-out` | Ember loops |

## Tiers and intensity levels

| Level | Ember | Spark | Forge | Titan |
| --- | --- | --- | --- | --- |
| **0 Off** | none | none | none | none |
| **1 Minimal** | none (static state colour only) | none | none | none |
| **2 Refined** | yes | yes | yes, plain | none |
| **3 Expressive** | yes | yes, one extra spark | yes, with halftone, offset outline and speed lines | yes, if celebrations are on |

When the app is under load the engine already drops one level for 30 s, so a Titan never plays on a
busy machine and Forge loses its Expressive extras.

## Reduced motion and fallbacks

Every effect has a still version, because Off and Windows' reduced-motion setting must lose nothing
but movement.

| Tier | Still version |
| --- | --- |
| Ember | The state's colour and word with no movement: an orange dot beside **Running**, frost blue beside **Queued in Fabric** |
| Spark | Nothing extra. The label already says Succeeded or Failed |
| Forge | Nothing extra, or a still icon in the status (the anvil, the wolf) for no longer than the effect would have lasted |
| Titan | The milestone as a normal in-app message with the emblem as a still image. No overlay |

Screen readers hear the result once through `announce()`, never the effect.

## Where tiers live in the code

Today every kind in `MOTION` (`app/ui/motion.js`) has a `level` and a `family`. Step 2 adds a `tier`
to each so tests can enforce the limits above:

| Kind | Family | Tier |
| --- | --- | --- |
| running-cell ember, Energy Flow, wolf heat glow | ambient, wolf | Ember |
| `spark`, `fail`, `pulse`, `settle` | sparks, ambient | Spark |
| `strike`, `wolfrun`, `arrive`, `achieve` | sparks, wolf, achievements | Forge |
| first Titan (step 2) | achievements | Titan |

The existing rules stay: features report events with `forgeEvent()`, never animate on their own;
Ember, Spark and Forge animate `transform` and `opacity` only, with `pointer-events: none`; at most
three effects play at once. Titan is the only tier that may use a canvas or a separate layer, so it
is drawn by its own lazily loaded module and never loaded at levels below 3. Step 3, the
implementation guide, sets the measured performance budget for each tier.

## Do and don't

**Do**

- Tie every effect to a real, finished state change from the engine.
- Keep the outcome readable from the first frame.
- Reuse a motif before inventing a new one. A new effect is a new event mapped to an existing kind
  where possible.
- Check it at Off, Minimal, Refined and Expressive, and with Windows' animation effects off.

**Don't**

- Animate to fill time, predict a result or celebrate something that hasn't happened.
- Shake the window, flash the whole screen or strobe (nothing flashes more than three times a
  second).
- Move layout, take focus or catch the pointer.
- Use sound. KratosForge has none.
- Add a Titan for routine work. If it can happen ten times a day, it is a Spark or a Forge.

## Open questions

1. **Dark theme.** The app has no dark theme yet. When it gets one, fire tokens stay the same and the
   navy ink becomes `--fx-ice` so the wolf stays visible.
2. **Artwork for Titan.** The sample uses crops of the storyboard as stand-ins. The full-size wolf
   and Kratos pieces in the [asset manifest](design/assets.md) marked `needed` or `placeholder`
   must exist before the first Titan ships to Stable.
