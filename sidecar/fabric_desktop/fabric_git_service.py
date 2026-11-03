"""Fabric workspace Git awareness (Git-native Phase 2, docs/git-integration-plan.md).

Fabric's own Git integration ties a workspace to a branch and folder; this is where KratosForge
shows that relationship. ``fabric_item_support`` is the artifact capability registry
(``artifacts.py``): which item types Fabric Git, deployment pipelines, item definitions and
KratosForge handle. It reads nothing from Fabric.
"""

from __future__ import annotations

from typing import Any

from . import artifacts


class FabricGitMixin:
    def fabric_item_support(self, item_type: str | None = None) -> dict[str, Any]:
        """The whole registry, or one type's entry (an unknown type comes back as not supported)."""
        if item_type is None:
            return artifacts.registry()
        return {"checked": artifacts.SOURCES_CHECKED, "item": artifacts.capability(str(item_type)).as_dict()}
