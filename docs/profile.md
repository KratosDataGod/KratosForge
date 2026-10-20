# Profile (Preview)

Your profile is a private record of work KratosForge has verified, what it adds up to, and a
review-ready report you can take to a performance or development conversation. Open it from the
account button (top right) > **Profile**, or search for **Profile** in the command bar (Ctrl+K).

It is on from the start (turn it off in **Profile > Settings**), it is stored only on this PC for the account you're signed in
with, and nothing in it is sent anywhere.

## What gets recorded

Two things:

- A notebook that passes [Validate](validate-notebook.md) with a clean run (Ready, or Ready with
  warnings) and reads or writes at least one lakehouse table.
- A notebook proven with [Prove this notebook](prove/how-proofs-work.md) whose verification finds every table
  the same on this PC and in Fabric. The profile keeps the run receipt's id and the times the proof
  measured on both sides; it starts nothing in Fabric itself.

Each one is recorded as a line that says what passed, when, and the id of the Validate result or run
receipt.

- It never stores code, cell outputs, table names or rows, passwords or tokens.
- Validating or proving the same code again adds nothing. Changing the notebook records a new
  revision.
- KratosForge can't tell who wrote a notebook, so it records what was validated, not who authored it.
- **Settings > Include past validations** adds notebooks that passed Validate or Prove before
  recording was on (once), dated when they ran.

The file is `%LOCALAPPDATA%\FabricDesktop\profile\<id>\profile.json`, where `<id>` is a hash of
your sign-in name. Backups include it; support bundles don't. If the file is ever damaged, it is set
aside beside the new one and the Profile page says so until you dismiss the notice.

## Levels and achievements

Levels and achievements are for fun. They show how far you've come in KratosForge and are not a
skill rating. **Settings > Developer progression** turns them off; your accomplishments and the
report stay.

| Achievement | Earned when | XP |
|---|---|---:|
| First Flame | Your first notebook passes Validate with a clean run | 50 |
| Steady Hand | Five different notebooks have each passed Validate with a clean run | 100 |
| Proof Forged | A proof finds every table the same on this PC and in Fabric | 150 |

When Validate or Prove earns you an achievement, a short message names it (turn that off with **Settings >
Achievement notifications**). Each achievement is announced once, even if you open the same result
again. With **Settings > Display > Motion & effects > Achievement celebrations** on (off by
default), the message also gets a brief reveal; see [Forge animations](forge-animations.md).

After the first, each **different** notebook that passes Validate for the first time earns 10 XP,
and each different notebook proven after Proof Forged earns 20 XP, for up to three new notebooks a
day. A proof runs the notebook in Fabric, which uses some capacity, so Proof Forged is never needed
to level up. New revisions of a notebook already counted earn no XP. Nothing rewards
hours, run counts, lines of code or compute used.

Levels (rules version 1): reaching level L takes `round(100 × (L − 1)^1.5)` XP, so level 2 is 100
XP, level 5 is 800, level 10 is 2,700 and level 50 is 34,300. Titles: 1-4 Apprentice, 5-9 Forge
Initiate, 10-14 Iron Forger, 15-19 Steel Forger, 20-29 Master Forger, 30-39 Forge Architect, 40-49
Wolf Guardian, 50+ Legend of the Forge. A later rules version won't change what you've already
earned.

**Profile > Journey** shows your level, recent milestones (achievements and levels reached), every
badge and what you could earn next. **Profile > Achievements** lists each achievement with exactly
what earns it and the evidence it rests on. Both tabs are hidden while Developer progression is off.

## Skills

Skills show **evidence coverage**, never a score: Insufficient evidence (fewer than 3 items, or all
within 7 days), Emerging (3+ items across 2+ notebooks over 7+ days), Demonstrated (6+ items across
3+ artifacts over 14+ days, with a measured result or a receipt) and Established (12+ items across 5+
artifacts over 60+ days, of 2+ kinds). Not seen yet and Not tracked yet mean exactly that; neither is
a low rating. This version tracks Data Engineering, Engineering Quality and (through proofs) Performance &
Optimization.

## Excluding an accomplishment

**Exclude** on an accomplishment takes it out of your XP, achievements, skills and report.
**Include** puts it back.

## Professional report

**Profile > Professional report** puts your verified accomplishments for a period into sections:
executive summary, key contributions, engineering quality, performance and efficiency, technical
competencies and an evidence appendix. A proof adds a key contribution, its measured times under
performance and efficiency, and its run receipt in the appendix. Every line says where it came from:

- **Verified**: from a recorded accomplishment, with its evidence reference (E1, E2, ...).
- **Entered by you**: your own executive summary, if you write one.
- **Generated summary**: fixed wording filled in from verified accomplishments. Nothing is inferred.
- **Not measured**: there is no measurement for it in the period.

XP, levels and achievements are left out unless you tick **Include KratosForge achievements**.
**Save Markdown** saves a new file in Downloads (it never replaces one), and **Copy summary**
copies the summary as plain text.

## Your data

**Export my profile** saves the profile as JSON in Downloads. **Delete my profile data** deletes
your recorded accomplishments, profile details and report summary on this PC; Validate history and
run receipts stay. There is no leaderboard, no sharing and no manager view.
