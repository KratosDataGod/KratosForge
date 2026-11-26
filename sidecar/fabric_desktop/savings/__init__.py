"""Enterprise Savings Intelligence: the parts no other engine has (docs/product/enterprise-savings-intelligence-plan.md).

``attribution``: the waterfall that splits one run's avoided Fabric CU between the optimizations in
effect and running on a PC, never more than the run's baseline (ESI-303).

``accruals``: one run's CU avoided as ledger events, the ledger's only accrual grain. Each layer of
the attribution becomes one accrual (or regression) shaped as the ``fdopt_savings`` columns, with
the run's gross, overhead and net. The same run gives the same event ids on every PC, so a resync
adds nothing, and corrections chain to these ids. Rows are writable only when the workload's
baseline is Measured (decision of 2026-11-06); otherwise they are shown on this PC and the reason
is given. Nothing is written here (ESI-203).

``recurring``: period figures (groupings of those per-run accruals) and the recurring monthly
benefit, a projection that is always Estimated and never written (ESI-304).

``pricing``: versioned pricing scenarios and the capacity value of avoided CU under one, priced at
the version in effect when each run happened; always a Scenario figure, never savings (ESI-401).

``compare``: what one workload used on this PC, Fabric, Databricks and Snowflake, each in its own unit
(time, CU, DBU, credits), never converted into each other. Money only under one pricing version, a
Scenario figure, and a closing line that compares, never recommends (Preview).

``headroom``: capacity headroom (measure B), always Estimated: per capacity and per imported timepoint,
used CU against the CU limit of the SKU in effect then, as observed and with the runs' CU avoided added
back, and peak and p95 of both with the headroom gained. A timepoint with no data is Unknown, never 0 or an
average; timepoints that don't cover the period make the result Incomplete with the missing windows listed;
capacities are never mixed. Estimated capacity value (measure C) prices the same CU avoided with
``pricing.value_runs`` under a named scenario: a Scenario figure, never savings (ESI-402).

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

``realized``: realized savings (measure E) only from a Cost Management bill for one capacity over two
periods of the same length, with a person's confirmed action that links the change to optimizations.
A SKU change also needs its ``fdcap_change`` row. Only the part of the bill's drop that the change
explains is claimed, at the lower billed rate; the rest is reported as not claimed. Without that
evidence the result is Unknown, value None, with the reason. Lower utilization alone shows value and
headroom, not savings. Net benefit (measure F) subtracts every attributable cost, and is Unknown when
one is missing, Unknown or a Scenario (ESI-403).

``links``: an assessment finding (FEA) or an opportunity (OE) becomes a proposed optimization record,
keyed by its fingerprint. The ids come from the fingerprint alone, so a re-run or a second PC gives the
same record. A repeat detection links to the existing record and makes no new one. Two records for one
fingerprint are refused. A finding whose control changed version is returned for a person to review,
neither linked nor recorded again. With a matches table (FEA-604), a finding and an opportunity on the
same item share one record, so nothing is counted twice. An opportunity is verified by a Verified
validation. A finding is resolved only by a Verified validation and the assessment's own re-check
outcome verified_fixed (FEA-602). Anything else leaves it open. Records hold fingerprints and ids,
never code or evidence text.
Writing them, version-aware updates and the responsible user and device wait on ESI-301 (ESI-601).

``planner_inputs``: optimization demand changes shaped for the capacity planner (OE-040, OE-041), which
isn't built yet. Per capacity and per workload there are two inputs, verified first, never summed.
``verified`` holds Measured accruals of optimizations whose latest validation is Verified. ``estimated``
holds Estimated accruals and optimizations still Validating, Regressed or not validated. Regressions
count as increases and are labelled. A change that isn't known is None with the reason, never 0. Every
output says it isn't enough to right-size a capacity: that needs peak, concurrency and throttling
evidence from the planner's own inputs. Nothing recommends a SKU (ESI-602).

``exports``: CSV text for the eight reports in the brief (executive summary, workspace, capacity,
developer contribution, monthly statement, audit history, estimated versus verified, capacity
readiness), read from the ledger's chains (each layer as its chain's head says) and the results of
``realized``, ``pricing``, ``recurring`` and ``baselines``. Every figure has its classification in the
next column, and different classifications are never added into one cell; Unknown is an empty cell,
never 0; a price is a Scenario, never savings; text cells that start like a formula get an apostrophe.
Developer contribution is per device: per-person reporting waits on D3 and D5. Capacity readiness
shows evidence and recommends nothing; right-sizing belongs to the planner (OE-040). Text only: the
download screen waits on the ESI-502 mockup (ESI-507).

Pure: nothing here reads or writes a lakehouse, Fabric or this PC. Figures come in and go out as
``measurement.py`` measurements.
"""
