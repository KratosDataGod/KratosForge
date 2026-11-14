"""The optimization engine: find work that doesn't need to run, runs twice, or could run better (OE-001).

A workspace (or every workspace on a capacity) is read once: its items, the definitions of its
notebooks and pipelines, and their Fabric job history. Deterministic analyzers look for waste in
the order the brief sets (does it need to exist, is it done elsewhere, can it be done better), and
each finding becomes an *opportunity* with its evidence, confidence, impact, next step, validation
and rollback.

- ``model``: the opportunity record, its categories, actions and statuses.
- ``inventory``: the workloads, from what Fabric already says about them. Pure.
- ``analyzers``: the analyzers. Code is parsed, never run.
- ``impact``: what an opportunity is worth, as measurement.py figures (Unknown when not known).
- ``assess``: the whole assessment: analyzers, overlap-aware totals, ranking, the next action.

No AI is needed for any of it, nothing is written to Fabric and no Fabric job starts.
See docs/optimize/engine.md.
"""
