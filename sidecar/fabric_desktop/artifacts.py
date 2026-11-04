"""Which Fabric item types work with Fabric Git, deployment pipelines, item definitions and KratosForge.

The artifact capability registry. Every answer here comes from Microsoft's own lists (SOURCES,
checked on SOURCES_CHECKED) or, for what KratosForge edits locally, from this repository's code.
A type that isn't listed is never guessed: ``capability()`` answers "not supported" with the
reason, so a page can show it without claiming support Fabric doesn't give.

Support levels: ``"yes"`` (generally available), ``"preview"`` (Microsoft marks it preview) and
``"no"``. Fabric Git and deployment pipelines carry an item's definition only: lakehouse tables
and files, warehouse data and semantic model data never move through them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Support = Literal["yes", "preview", "no"]

SOURCES_CHECKED = "2026-11-03"
SOURCES = (
    "https://learn.microsoft.com/en-us/fabric/cicd/git-integration/intro-to-git-integration",
    "https://learn.microsoft.com/en-us/fabric/cicd/deployment-pipelines/intro-to-deployment-pipelines",
    "https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/item-definition-overview",
)


@dataclass(frozen=True)
class ItemCapability:
    item_type: str  # Fabric's ItemType value, as the Git status API names it
    label: str
    git: Support
    deployment_pipeline: Support
    definition_api: bool  # Get / Update Item Definition and create with a definition
    local_edit: str  # what KratosForge edits on this PC ("" for nothing)
    limits: tuple[str, ...] = ()
    known: bool = True  # False: not in the registry, so treated as unsupported

    @property
    def round_trip(self) -> Support:
        """Can KratosForge take the item out of Fabric, edit it here, track it in Git and send it
        back without losing its definition? Only where Fabric Git (or the definition API) carries
        it and KratosForge edits it; as "preview" when Fabric still marks Git support preview."""
        if not self.local_edit or not self.definition_api or self.git == "no":
            return "no"
        return self.git

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "limits": list(self.limits), "round_trip": self.round_trip}


_DATA_STAYS = "Tables and files never go through Git or deployment pipelines; only the definition does"

# (item type, label, Fabric Git, deployment pipelines, definition API, edited in KratosForge, limits)
_ROWS: tuple[tuple[str, str, Support, Support, bool, str, tuple[str, ...]], ...] = (
    ("Notebook", "Notebook", "yes", "yes", True, "Notebook editor (ipynb)",
     ("The attached lakehouse is stored by id, which differs per workspace",)),
    ("DataPipeline", "Data pipeline", "yes", "yes", True, "Pipelines page (import, check, run here, export)",
     ("Connections are referenced by id, which differs per tenant",)),
    ("SemanticModel", "Semantic model", "preview", "preview", True, "",
     ("KratosForge reads TMDL here (best practices, data dictionary) but doesn't edit it",
      "Not push semantic models, live connections to Analysis Services or model v1",
      "Model data never goes through Git; refresh after an update")),
    ("Warehouse", "Warehouse", "yes", "yes", True, "Warehouse project (SQL files)", (_DATA_STAYS,)),
    ("Lakehouse", "Lakehouse", "yes", "yes", True, "", (_DATA_STAYS,)),
    ("Environment", "Environment", "yes", "yes", True, "",
     ("Publishing staged libraries is a separate step in Fabric",)),
    ("SparkJobDefinition", "Spark job definition", "yes", "yes", True, "", ()),
    ("CopyJob", "Copy job", "yes", "yes", True, "", ()),
    ("Dataflow", "Dataflow Gen2", "yes", "yes", True, "",
     ("Dataflow Gen1 isn't supported by Fabric Git",)),
    ("VariableLibrary", "Variable library", "yes", "yes", True, "", ()),
    ("UserDataFunction", "User data functions", "yes", "yes", False, "", ()),
    ("GraphQLApi", "API for GraphQL", "yes", "yes", True, "", ()),
    ("DataAgent", "Data agent", "yes", "yes", False, "", ()),
    ("MirroredDatabase", "Mirrored database", "yes", "yes", True, "", ("Mirrored data never goes through Git",)),
    ("MountedDataFactory", "Mounted Data Factory", "yes", "yes", True, "", ()),
    ("Reflex", "Activator", "yes", "yes", True, "", ()),
    ("Eventhouse", "Eventhouse", "yes", "yes", True, "", (_DATA_STAYS,)),
    ("Eventstream", "Eventstream", "yes", "yes", True, "", ()),
    ("KQLDatabase", "KQL database", "yes", "yes", True, "", (_DATA_STAYS,)),
    ("KQLQueryset", "KQL queryset", "yes", "yes", True, "", ()),
    ("KQLDashboard", "Real-Time Dashboard", "yes", "yes", True, "", ()),
    ("Map", "Map", "yes", "yes", False, "", ()),
    ("MirroredAzureDatabricksCatalog", "Mirrored Azure Databricks catalog", "yes", "yes", True, "", ()),
    ("SQLDatabase", "SQL database", "yes", "yes", False, "", (_DATA_STAYS,)),
    ("GraphModel", "Graph model", "yes", "yes", True, "", ()),
    ("GraphQuerySet", "Graph queryset", "yes", "yes", False, "", ()),
    ("OrgApp", "Org app", "yes", "preview", True, "", ()),
    ("Report", "Report", "preview", "preview", True, "",
     ("Not reports on Analysis Services models, nor reports that use a semantic model in My workspace",)),
    ("PaginatedReport", "Paginated report", "preview", "preview", True, "", ()),
    ("MLExperiment", "Machine learning experiment", "preview", "preview", False, "", ()),
    ("MLModel", "Machine learning model", "preview", "preview", False, "", ()),
    ("ApacheAirflowJob", "Apache Airflow job", "preview", "preview", False, "", ()),
    ("DataBuildToolJob", "dbt job", "preview", "preview", True, "", ()),
    ("OperationsAgent", "Operations agent", "preview", "preview", False, "", ()),
    ("EventSchemaSet", "Event schema set", "preview", "preview", True, "", ()),
    ("DigitalTwinBuilder", "Digital twin builder", "preview", "preview", False, "", ()),
    ("AnomalyDetector", "Anomaly detection", "preview", "preview", False, "", ()),
    ("CosmosDBDatabase", "Cosmos DB database", "preview", "preview", False, "", (_DATA_STAYS,)),
    ("DeploymentPlan", "Deployment plan", "preview", "preview", True, "", ()),
    ("Ontology", "Ontology", "preview", "preview", False, "", ()),
    ("Plan", "Plan", "preview", "preview", False, "", ()),
    ("Dashboard", "Dashboard", "no", "preview", False, "", ("Fabric Git doesn't carry dashboards",)),
)

REGISTRY: dict[str, ItemCapability] = {
    row[0]: ItemCapability(*row) for row in _ROWS
}

_UNKNOWN_LIMIT = ("KratosForge has no record of this item type, so it's treated as not supported: "
                  "Fabric Git ignores items it doesn't support and KratosForge doesn't guess")


def capability(item_type: str) -> ItemCapability:
    """The registry's entry for this Fabric item type, or an unsupported entry that says so."""
    found = REGISTRY.get(item_type)
    if found is not None:
        return found
    label = str(item_type or "Unknown")[:80]
    return ItemCapability(label, label, "no", "no", False, "", (_UNKNOWN_LIMIT,), known=False)


def registry() -> dict[str, Any]:
    """Every listed type, for the Item support view, with where the answers came from."""
    return {"checked": SOURCES_CHECKED, "sources": list(SOURCES),
            "items": [c.as_dict() for c in sorted(REGISTRY.values(), key=lambda c: c.label.lower())]}
