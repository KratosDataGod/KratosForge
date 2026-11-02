# Profile (Preview)

Your profile is a private record of work KratosForge has verified, what it adds up to, and a
review-ready report you can take to a performance or development conversation. Open it from the
account button (top right) > **Profile**, or search for **Profile** in the command bar (Ctrl+K).

It is on from the start (turn it off in **Profile > Settings**), it is stored only on this PC for the account you're signed in
with, and nothing in it is sent anywhere.

## What gets recorded

Four things:

- A notebook that passes [Validate](validate-notebook.md) with a clean run (Ready, or Ready with
  warnings) and reads or writes at least one lakehouse table.
- A notebook proven with [Prove this notebook](prove/how-proofs-work.md) whose verification finds every table
  the same on this PC and in Fabric. The profile keeps the run receipt's id and the times the proof
  measured on both sides; it starts nothing in Fabric itself.
- A [Learn](learn.md) exercise whose checks all pass on this PC, in a published lesson or lab (draft
  lessons don't count, as they may still change). When it was the lesson's last exercise, finishing
  the lesson is recorded too. The profile keeps the lesson's id, the exercise, the content version,
  which kinds of check passed and, for a speed check, the times measured on this PC; never your
  answer or the lab's data.
- A best-practice fix applied with **Try the fix** after **Compare** ran the query as written and as
  fixed and found the same rows ([Learn](learn.md#beside-a-finding)). Only Apply records it, and only
  when that Compare matched; a fix applied without a Compare, or after one that found different rows,
  isn't recorded. The profile keeps the rule, the two runs' ids, the row count and each run's measured
  time (one run each); never the SQL or its rows. It earns no XP.

Each one is recorded as a line that says what passed, when, and the id of the Validate result, run
receipt or Learn lesson.

- It never stores code, cell outputs, table names or rows, passwords or tokens.
- Validating or proving the same code again adds nothing. Changing the notebook records a new
  revision. Passing the same Learn exercise again adds nothing; after the lesson is updated, it
  records the new content version but earns nothing more.
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
| The First Spark | Your first Learn exercise passes every check on this PC | 50 |
| The Data Smith | Five different Learn exercises have each passed every check | 100 |
| The Query Forger | A Learn exercise with a speed check passes: the same answer, measurably faster on this PC | 100 |
| The Architect's Eye | A Learn exercise with a table shape or referential integrity check passes | 100 |
| The Guardian | A Learn exercise that plants data quality problems passes: your query finds every one | 100 |

Each Learn achievement needs a published lesson or lab with that kind of check. Today LAB-02 has a
speed check, LM-02 and LM-05 have table shape and integrity checks, and LAB-01 plants data quality
problems.

When Validate, Prove or a Learn check earns you an achievement, a short message names it (turn that off with **Settings >
Achievement notifications**). Each achievement is announced once, even if you open the same result
again. With **Settings > Display > Motion & effects > Achievement celebrations** on (off by
default), the message also gets a brief reveal; see [Forge animations](forge-animations.md).

After the first, each **different** notebook that passes Validate for the first time earns 10 XP,
and each different notebook proven after Proof Forged earns 20 XP, for up to three new notebooks a
day. Finishing a Learn lesson or lab for the first time earns 25 XP. Lessons and new notebooks share
the same three a day, so finishing lessons can use a day's slots.
A proof runs the notebook in Fabric, which uses some capacity, so Proof Forged is never needed
to level up. New revisions of a notebook already counted earn no XP. Nothing rewards
hours, run counts, lines of code or compute used.

Levels (rules version 3; version 2 added Learn, version 3 added checked fixes, and both kept the level curve): reaching level L takes `round(100 × (L − 1)^1.5)` XP, so level 2 is 100
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
Optimization, and through Learn also Learning & Growth (every passed exercise) and, by the kind of
check passed, Performance & Optimization (speed), Architecture & Reusability (table shape, integrity)
and Engineering Quality (finding planted data quality problems). A checked fix counts toward Engineering
Quality, and toward its rule's area where there is a skill for it (Performance & Optimization for a
performance rule).

## Excluding an accomplishment

**Exclude** on an accomplishment takes it out of your XP, achievements, skills and report.
**Include** puts it back.

## Professional report

**Profile > Professional report** puts your verified accomplishments for a period into sections:
executive summary, key contributions, engineering quality, performance and efficiency, learning,
technical competencies and an evidence appendix. A proof adds a key contribution, its measured times under
performance and efficiency, and its run receipt in the appendix. Learning lists each Learn lesson or
lab you finished and how many exercises passed. Checked fixes are counted under engineering quality,
with each one's two measured times under performance and efficiency, labeled one run each. The report
says what was checked, never that a skill was mastered. Every line says where it came from:

- **Verified**: from a recorded accomplishment, with its evidence reference (E1, E2, ...).
- **Entered by you**: your own executive summary, if you write one.
- **Generated summary**: fixed wording filled in from verified accomplishments. Nothing is inferred.
- **Not measured**: there is no measurement for it in the period.

The report names the notebooks and warehouses your accomplishments came from, such as a notebook
you validated or the warehouse a checked fix ran on, so read it before you share it. To leave an
accomplishment out, **Exclude** it on Overview.

XP, levels and achievements are left out unless you tick **Include KratosForge achievements**.
**Save Markdown** saves a new file in Downloads (it never replaces one), and **Copy summary**
copies the summary as plain text.

## Your data

**Export my profile** saves the profile as JSON in Downloads. **Delete my profile data** deletes
your recorded accomplishments, profile details and report summary on this PC; Validate history and
run receipts stay. There is no leaderboard, no sharing and no manager view.
