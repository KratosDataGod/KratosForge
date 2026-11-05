# Incident: late and broken data

At 10:50 the finance team says yesterday's revenue looks too high, and the morning dashboard was
late. You have the load log and the sales table. Work it like an incident (LM-35): find what
happened, measure it, and report the right number.

## What to look for

- **Late loads**: compare each batch's arrival with its deadline. Report how late, not just
  whether: 5 minutes and 5 hours are different conversations.
- **Doubled rows**: a retried batch that reloads rows already loaded doubles them, unless the load
  replaces by key (LM-21). The tell is the same business key under two batches.
- **The corrected figure**: count each sale once, and say which batches you excluded and why.

Then fix the cause: make the load replace by key, so the next retry can't double anything.

## The lab

First the late batches, then the corrected revenue.
