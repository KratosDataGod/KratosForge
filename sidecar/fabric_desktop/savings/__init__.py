"""Enterprise Savings Intelligence: the parts no other engine has (docs/product/enterprise-savings-intelligence-plan.md).

``attribution``: the waterfall that splits one run's avoided Fabric CU between the optimizations in
effect and running on a PC, never more than the run's baseline (ESI-303).

``recurring``: period figures (groupings of those per-run accruals) and the recurring monthly
benefit, a projection that is always Estimated and never written (ESI-304).

Pure: nothing here reads or writes a lakehouse, Fabric or this PC. Figures come in and go out as
``measurement.py`` measurements.
"""
