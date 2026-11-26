"""Fabric Excellence Assessment engine (FEA Phase 1): evidence, versioned controls, scoring, snapshots.

Pure: nothing here reads Fabric, OneLake or files. Services gather evidence with the readers the app
already has and hand it in; the same code then runs on a live read, a stored snapshot and a test
fixture. The plan is docs/product/fabric-excellence-assessment-plan.md.

- :mod:`.evidence`  typed evidence records with a confidence class and a content hash
- :mod:`.controls`  versioned controls, outcomes, and the registry
- :mod:`.profiles`  assessment profiles: dimension weights, severity overrides, thresholds
- :mod:`.scoring`   dimension and overall scores, coverage, assured score, rating, Critical Risk
- :mod:`.findings`  the finding record built from a failed control
- :mod:`.assess`    one assessment run, its snapshot, rescoring and comparison
- :mod:`.exceptions` accepted risk: owner, reason and expiry, shown on a failure, never changing it

Rules every part keeps: a control that couldn't be checked is *not evaluated*, never passed; a pass or
a fail must cite the evidence it rests on; inferred evidence is advisory unless a control says why it
may count; ratings are KratosForge product ratings, not Microsoft certifications.
"""
