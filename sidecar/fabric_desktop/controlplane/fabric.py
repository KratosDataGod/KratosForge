"""The Fabric provider: finding KratosForge Environments (CP-004).

Discovery only reads. It lists the workspaces the signed-in person can open, then each lakehouse in a
chosen workspace, and for each lakehouse reads one small file, ``Files/_kratosforge/environment.json``.
A lakehouse with a valid manifest is an **environment**. One with KratosForge's central tables
(``fdopt_schema_version``) but no manifest is **adoptable**: an existing central lakehouse that can
become an environment by writing the manifest (CP-006). Anything else is left out; one the person
can't read is reported as **unreadable** with the reason, never guessed.

Nothing here creates, changes or initializes anything. The manifest decides what a lakehouse is, so a
renamed lakehouse is still found, and a lakehouse named ``KratosForgeLakehouse`` without a manifest is
not taken for an environment.
"""

from __future__ import annotations

from typing import Any, Callable

from ..optimize import schema
from ..optimize.setup import denied
from ..redact import describe
from . import manifest as mf
from .providers import Found, StoreRef
from .store import ManifestStore

ENVIRONMENT, ADOPTABLE, UNREADABLE = "environment", "adoptable", "unreadable"
CORE_VERSION_TABLE = schema.table("schema_version")  # fdopt_schema_version, as optimize_discover checks
# A workspace with more lakehouses than this is cut short, said so, so one call stays bounded.
MAX_LAKEHOUSES = 200


class FabricDiscovery:
    """``fabric`` is the app's FabricClient (list_workspaces, list_lakehouses). ``manifest_store`` and
    ``table_names`` open a lakehouse: in the app, through OneLake with the person's storage token."""

    provider = "fabric"

    def __init__(self, fabric: Any, manifest_store: Callable[[str, str], ManifestStore],
                 table_names: Callable[[str, str], list[str]]):
        self.fabric = fabric
        self._manifest_store = manifest_store
        self._table_names = table_names
        self.truncated = False

    def list_namespaces(self) -> list[dict[str, str]]:
        """Workspaces this person can open: id and name, sorted by name."""
        return sorted(({"id": w["id"], "display_name": w.get("displayName", "")} for w in self.fabric.list_workspaces()),
                      key=lambda w: w["display_name"].lower())

    def _one(self, workspace_id: str, lh: dict[str, Any]) -> Found | None:
        ref = StoreRef("fabric", workspace_id, lh["id"], lh.get("displayName", ""))
        try:
            got = self._manifest_store(workspace_id, lh["id"]).read()
        except mf.ManifestError as exc:
            return Found(ref, UNREADABLE, reason=f"Its KratosForge manifest is damaged: {exc}")
        except Exception as exc:  # noqa: BLE001 - one lakehouse that can't be read doesn't stop the rest
            return Found(ref, UNREADABLE, reason="No access" if denied(exc) else f"Couldn't be read ({describe(exc)})")
        if got is not None:
            return Found(ref, ENVIRONMENT, manifest=got.manifest)
        try:
            names = self._table_names(workspace_id, lh["id"])
        except Exception as exc:  # noqa: BLE001
            return Found(ref, UNREADABLE, reason="No access" if denied(exc) else f"Couldn't be read ({describe(exc)})")
        return Found(ref, ADOPTABLE) if CORE_VERSION_TABLE in names else None

    def find_environments(self, namespace_id: str) -> list[Found]:
        """Environments and adoptable central lakehouses in one workspace: environments first."""
        lakehouses = self.fabric.list_lakehouses(namespace_id)
        self.truncated = len(lakehouses) > MAX_LAKEHOUSES  # the caller says the list was cut short
        found = [f for f in (self._one(namespace_id, lh) for lh in lakehouses[:MAX_LAKEHOUSES]) if f is not None]
        order = {ENVIRONMENT: 0, ADOPTABLE: 1, UNREADABLE: 2}
        return sorted(found, key=lambda f: (order[f.state], f.ref.display_name.lower()))
