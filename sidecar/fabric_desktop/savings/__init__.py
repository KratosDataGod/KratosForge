"""Enterprise Savings Intelligence: the parts no other engine has (docs/product/enterprise-savings-intelligence-plan.md).

``attribution``: the waterfall that splits one run's avoided Fabric CU between the optimizations in
effect and running on a PC, never more than the run's baseline (ESI-303).

``recurring``: period figures (groupings of those per-run accruals) and the recurring monthly
benefit, a projection that is always Estimated and never written (ESI-304).

``pricing``: versioned pricing scenarios and the capacity value of avoided CU under one, priced at
the version in effect when each run happened; always a Scenario figure, never savings (ESI-401).

``overhead``: what one run on a PC still cost (transfer seconds, bytes, OneLake read and write
operations, and its share of the sync that carried it), one measurement per figure, Unknown where
not recorded, and the Fabric CU overhead the waterfall subtracts for net: Unknown unless a measured
or estimated CU figure is supplied, since nothing on a PC measures it (ESI-202).

``corrections``: corrections and reversals that fix ledger rows without editing them, each pointing at
what it fixes through ``corrects``, and the reconciliation report that rebuilds totals from the
ledger and compares them with the latest attribution, fixing nothing (ESI-404).

``baselines``: versioned baselines of a workload's Fabric CU per run (the median of comparable runs:
same workload, definition version and input scope) and the before and after check that moves an
optimization from Validating to Verified, or keeps it Validating; estimates and times never verify (ESI-302).

``priorities``: what to optimize next. Open opportunities (proposed, validating or approved) are
ranked by the benefit their evidence shows: measured CU first, then estimated CU, then run time
when CU isn't known, then scenario figures, then unknown last with None. Kinds are never sorted
together. Within a kind: bigger first, then the action order, then id (ESI-603).

Pure: nothing here reads or writes a lakehouse, Fabric or this PC. Figures come in and go out as
``measurement.py`` measurements.
"""
