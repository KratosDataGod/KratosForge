"""Workload list edges found by running every mutant of workloads/query.py (mutation batch 321,
docs/quality/mutation-testing.md): a list row keeps the workload's owners."""

from fabric_desktop.workloads import query
from fabric_desktop.workloads.model import HEALTH, HEALTH_LABELS


def _w(**extra):
    return {
        "id": "x",
        "name": "x",
        "kind": "notebook",
        "kind_label": "Notebook",
        "group": {"id": "p", "label": "p"},
        "health": "healthy",
        "health_label": HEALTH_LABELS["healthy"],
        "health_rank": HEALTH.index("healthy"),
        "readiness": {"state": "ready"},
        "unowned": False,
        "partial": False,
        "capabilities": [],
        "findings": [],
        "deployments": [{"location": {"kind": "this_pc", "label": "This PC"}}],
        **extra,
    }


def test_a_row_keeps_its_owners_and_reads_missing_owners_as_empty():
    owners = {"team": {"value": "Sales"}}
    assert query.row(_w(owners=owners))["owners"] == owners
    assert query.row(_w())["owners"] == {}
