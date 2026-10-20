# Forge animations

KratosForge shows a few small animations when notebook work changes state. They add a little of the
wolf and forge identity without getting in the way, and you can turn them off.

| When | What you see | How long |
| --- | --- | --- |
| A cell you ran succeeds | A tiny orange spark leaves the **Succeeded** label | 0.4 s |
| You run several cells (Run all, Run above, Run below) and it takes longer than 0.6 s | A track in the status bar says **Cell 3 of 8**, with the wolf at the share of cells done. When every cell has run, the wolf glows once at the end and the track goes | Until the run ends |
| A cell fails | A small ember beside **Failed** dims and goes out. The red label and the error appear at once | 0.45 s |

Nothing else changes: the labels, times, outputs and errors are the same with animations on or off,
and the animations never delay a run or move anything on the page. During Run all, cells don't
spark one by one; the wolf track shows the progress instead. Stop and Cancel show no animation.

## Turn them off

**Settings > Display > Forge animations**. The change applies at once, including during a run, and
is kept for next time. The progress track still shows during a run, without the wolf.

Windows' **Settings > Accessibility > Visual effects > Animation effects** (reduced motion) turns them
off too, whatever this setting says.

Your organization can fix the setting with `"forge_animations": false` in the policy file
([Azure setup](azure-setup.md#4-push-settings-to-everyone)).

## Screen readers

Animations are hidden from screen readers. Instead, the app says once when a cell you ran succeeds or
fails, and once when a run of several cells finishes or stops ("Run finished: 8 cells succeeded").
The progress track is a progress bar with its value.

## For developers

`app/ui/motion.js` holds the effects: `fx(anchor, kind)` plays one effect over an element,
`wolfTrack()` draws step progress, `motionOn()` says whether decorative motion is wanted, and
`announce()` speaks to screen readers. A new effect is a kind in `MOTION` plus its CSS in
`styles.css` under "Forge animations", not animation code of its own. Effects use CSS transform and
opacity only, are `aria-hidden`, take no focus, and are never made while motion is off.
