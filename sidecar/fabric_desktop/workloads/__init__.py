"""Workloads: what an engineer builds and runs, wherever it runs, as one manageable list.

A workload is a reference to a definition that already exists (a metadata entity, a notebook
on this PC, a Fabric item), the places it is deployed, how its runs went and how fresh that
knowledge is. Nothing here copies a definition or runs anything itself: actions go through
the services that already run each kind. See docs/workloads/plan.md.

    model.py      ids, kinds, capabilities, run states, observation states
    store.py      workloads.sqlite: assignments, links, the action log, collection cache
    inventory.py  adapters that turn each native source into workload records
    health.py     health and readiness findings, each with its evidence
    query.py      views, search, grouping and paging
"""
