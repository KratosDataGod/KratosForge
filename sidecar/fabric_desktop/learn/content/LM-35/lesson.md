# Monitoring and incidents

Users notice two things: data that is wrong, and data that is late. Alert on those, not on every
warning in a log.

## Two alerts that matter

- **Failed**: the job's latest run failed. An earlier failure that a later run fixed isn't news.
- **Stale**: the last successful run is older than the data's promise (LM-30), even if nothing
  failed: a job that stopped being scheduled fails silently.

Fabric's Monitor hub and KratosForge's Monitor page list runs; the rule is the same anywhere.

## When one fires

1. **Acknowledge** so others know someone has it.
2. **Contain**: pause downstream loads so bad data doesn't spread, and tell the users affected.
3. **Fix** the cause, then rerun; idempotent loads (LM-21) make that safe.
4. **Review** afterwards: what would have caught it sooner?

## Try it

Work out which jobs need an alert right now.
