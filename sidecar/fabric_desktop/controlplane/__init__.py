"""The KratosForge Environment: one identity over the central stores (docs/product/central-control-plane-plan.md).

- :mod:`.manifest`   the environment manifest: model, validation, no secrets (CP-001)
- :mod:`.store`      reading and writing the manifest with conditional writes, and this PC's cached copy (CP-002)
- :mod:`.providers`  provider contracts and honest capability declarations (CP-003)

"Environment" here is the KratosForge Environment (ids ``kfenv_...``). Dev, Test and Prod stay
*deployment environments* (meta/project.ENVIRONMENTS); see CP-ADR-002.
"""
