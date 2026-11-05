"""The artifact capability registry (Git-native Phase 2): which Fabric item types work with Fabric Git,
deployment pipelines, item definitions and KratosForge, and nothing guessed for the rest."""

import json

from fabric_desktop import artifacts, server
from fabric_desktop.service import Service

# The Git status API's ItemType values (Microsoft Learn, Core > Git > Get Status, 2026-11-03).
STATUS_ITEM_TYPES = {
    "Dashboard", "Report", "SemanticModel", "PaginatedReport", "Datamart", "Lakehouse", "Eventhouse", "Environment",
    "KQLDatabase", "KQLQueryset", "KQLDashboard", "DataPipeline", "Notebook", "SparkJobDefinition", "MLExperiment",
    "MLModel", "Warehouse", "Eventstream", "SQLEndpoint", "MirroredWarehouse", "MirroredDatabase", "Reflex",
    "GraphQLApi", "MountedDataFactory", "SQLDatabase", "CopyJob", "VariableLibrary", "Dataflow", "ApacheAirflowJob",
    "WarehouseSnapshot", "DigitalTwinBuilder", "DigitalTwinBuilderFlow", "MirroredAzureDatabricksCatalog", "Map",
    "AnomalyDetector", "UserDataFunction", "GraphModel", "GraphQuerySet", "SnowflakeDatabase", "OperationsAgent",
    "CosmosDBDatabase", "Ontology", "EventSchemaSet", "DataAgent", "MirroredCatalog", "PolicySet", "AppBackend",
    "OrgApp", "OrgAppAudience", "DataBuildToolJob", "AzureDatabricksStorage", "Plan", "Monitoring", "DeploymentPlan",
}


def test_every_registered_type_is_a_type_fabric_names():
    assert set(artifacts.REGISTRY) <= STATUS_ITEM_TYPES


def test_levels_are_only_yes_preview_or_no():
    for cap in artifacts.REGISTRY.values():
        assert cap.git in ("yes", "preview", "no"), cap.item_type
        assert cap.deployment_pipeline in ("yes", "preview", "no"), cap.item_type
        assert cap.round_trip in ("yes", "preview", "no"), cap.item_type
        assert cap.known


def test_the_items_microsoft_lists_as_generally_available():
    for t in ("Notebook", "DataPipeline", "Lakehouse", "Warehouse", "Environment", "SparkJobDefinition",
              "VariableLibrary", "CopyJob", "Dataflow"):
        cap = artifacts.capability(t)
        assert (cap.git, cap.deployment_pipeline) == ("yes", "yes"), t


def test_power_bi_items_are_preview_not_claimed_as_supported():
    for t in ("SemanticModel", "Report", "PaginatedReport"):
        assert artifacts.capability(t).git == "preview", t
    assert artifacts.capability("Dashboard").git == "no"


def test_round_trip_needs_git_definitions_and_local_editing():
    assert artifacts.capability("Notebook").round_trip == "yes"
    assert artifacts.capability("DataPipeline").round_trip == "yes"
    assert artifacts.capability("Warehouse").round_trip == "yes"
    # Supported by Fabric Git, but KratosForge doesn't edit it here.
    assert artifacts.capability("Lakehouse").round_trip == "no"
    assert artifacts.capability("SemanticModel").round_trip == "no"
    for cap in artifacts.REGISTRY.values():
        if cap.round_trip != "no":
            assert cap.local_edit and cap.definition_api and cap.git != "no", cap.item_type


def test_data_never_goes_through_git_is_said_where_it_matters():
    for t in ("Lakehouse", "Warehouse", "Eventhouse", "KQLDatabase"):
        assert any("never go" in limit for limit in artifacts.capability(t).limits), t


def test_an_unknown_type_is_not_supported_and_says_why():
    for t in ("Datamart", "SQLEndpoint", "SomethingNew", ""):
        cap = artifacts.capability(t)
        assert not cap.known
        assert (cap.git, cap.deployment_pipeline, cap.definition_api, cap.round_trip) == ("no", "no", False, "no")
        assert "doesn't guess" in cap.limits[0]


def test_an_unknown_type_name_is_cut_to_a_label():
    cap = artifacts.capability("X" * 500)
    assert len(cap.label) == 80


def test_registry_lists_every_type_once_with_its_sources():
    out = artifacts.registry()
    types = [i["item_type"] for i in out["items"]]
    assert len(types) == len(set(types)) == len(artifacts.REGISTRY)
    assert out["checked"] == artifacts.SOURCES_CHECKED
    assert all(s.startswith("https://learn.microsoft.com/") for s in out["sources"])
    json.dumps(out)  # the page gets it as JSON
    labels = [i["label"].lower() for i in out["items"]]
    assert labels == sorted(labels)


def test_the_rpc_answers_without_signing_in(tmp_path):
    svc = Service(root=tmp_path)
    assert "fabric_item_support" in server.METHODS
    whole = svc.fabric_item_support()
    assert any(i["item_type"] == "Notebook" for i in whole["items"])
    one = svc.fabric_item_support("Notebook")["item"]
    assert one["round_trip"] == "yes" and one["limits"]
    assert svc.fabric_item_support("Datamart")["item"]["known"] is False
