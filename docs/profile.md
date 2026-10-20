# Profile (Preview)

Your profile is a private record of work KratosForge has verified, what it adds up to, and a
review-ready report you can take to a performance or development conversation. Open it from the
account button (top right) > **Profile**, or search for **Profile** in the command bar (Ctrl+K).

It is off until you turn it on, it is stored only on this PC for the account you're signed in
with, and nothing in it is sent anywhere.

## What gets recorded

In this first version, one thing: a notebook that passes [Validate](validate-notebook.md) with a
clean run (Ready, or Ready with warnings) and reads or writes at least one lakehouse table. Each one
is recorded as a line that says what passed, when, and the id of the Validate result.

- It never stores code, cell outputs, table names or rows, passwords or tokens.
- Validating the same code again adds nothing. Changing the notebook and validating it records a
  new revision.
- KratosForge can't tell who wrote a notebook, so it records what was validated, not who authored it.
- **Settings > Include past validations** adds notebooks that passed before you turned recording
  on (once), dated when they ran.

The file is `%LOCALAPPDATA%\FabricDesktop\profile\<id>\profile.json`, where `<id>` is a hash of
your sign-in name. Backups include it; support bundles don't. If the file is ever damaged, it is set
aside beside the new one and the Profile page says so; recording stays off until you turn it on again.

## Levels and achievements

Levels and achievements are for fun. They show how far you've come in KratosForge and are not a
skill rating. **Settings > Developer progression** turns them off; your accomplishments and the
report stay.

| Achievement | Earned when | XP |
|---|---|---:|
| First Flame | Your first notebook passes Validate with a clean run | 50 |
| Steady Hand | Five different notebooks have each passed Validate with a clean run | 100 |

After the first, each **different** notebook that passes for the first time earns 10 XP, for up to
three notebooks a day. New revisions of a notebook already counted earn no XP. Nothing rewards
hours, run counts, lines of code or compute used.

Levels (rules version 1): reaching level L takes `round(100 × (L − 1)^1.5)` XP, so level 2 is 100
XP, level 5 is 800, level 10 is 2,700 and level 50 is 34,300. Titles: 1-4 Apprentice, 5-9 Forge
Initiate, 10-14 Iron Forger, 15-19 Steel Forger, 20-29 Master Forger, 30-39 Forge Architect, 40-49
Wolf Guardian, 50+ Legend of the Forge. A later rules version won't change what you've already
earned.

## Skills

Skills show **evidence coverage**, never a score: Insufficient evidence (fewer than 3 items, or all
within 7 days), Emerging (3+ items across 2+ notebooks over 7+ days), Demonstrated (6+ items across
3+ artifacts over 14+ days, with a measured result or a receipt) and Established (12+ items across 5+
artifacts over 60+ days, of 2+ kinds). Not seen yet and Not tracked yet mean exactly that; neither is
a low rating. This version tracks Data Engineering and Engineering Quality.

## Excluding an accomplishment

**Exclude** on an accomplishment takes it out of your XP, achievements, skills and report.
**Include** puts it back.

## Professional report

**Profile > Professional report** puts your verified accomplishments for a period into sections:
executive summary, key contributions, engineering quality, performance and efficiency, technical
competencies and an evidence appendix. Every line says where it came from:

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
