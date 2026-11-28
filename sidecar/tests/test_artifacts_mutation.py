"""Mutation-testing follow-up for artifacts.py (docs/quality/mutation-testing.md, batch 54).

These sets come from the Microsoft lists in artifacts.SOURCES. When Microsoft changes a list, update
the registry and these sets together, after checking the source."""

import dataclasses

import pytest

from fabric_desktop.artifacts import REGISTRY, capability

# Types Microsoft lists without Get/Update Item Definition.
NO_DEFINITION_API = {
    "AnomalyDetector", "ApacheAirflowJob", "CosmosDBDatabase", "Dashboard", "DataAgent", "DigitalTwinBuilder",
    "GraphQuerySet", "MLExperiment", "MLModel", "Map", "Ontology", "OperationsAgent", "Plan", "SQLDatabase",
    "UserDataFunction",
}
GIT_PREVIEW = {
    "AnomalyDetector", "ApacheAirflowJob", "CosmosDBDatabase", "DataBuildToolJob", "DeploymentPlan",
    "DigitalTwinBuilder", "EventSchemaSet", "MLExperiment", "MLModel", "Ontology", "OperationsAgent",
    "PaginatedReport", "Plan", "Report", "SemanticModel",
}
PIPELINE_PREVIEW = GIT_PREVIEW | {"Dashboard", "OrgApp"}


def test_which_types_have_a_definition_api():
    assert {k for k, c in REGISTRY.items() if not c.definition_api} == NO_DEFINITION_API
    assert len(REGISTRY) == 42


def test_which_types_fabric_git_and_deployment_pipelines_mark_preview():
    assert {k for k, c in REGISTRY.items() if c.git == "preview"} == GIT_PREVIEW
    assert {k for k, c in REGISTRY.items() if c.git == "no"} == {"Dashboard"}
    assert {k for k, c in REGISTRY.items() if c.deployment_pipeline == "preview"} == PIPELINE_PREVIEW
    assert {k for k, c in REGISTRY.items() if c.deployment_pipeline == "no"} == set()


def test_only_the_types_edited_here_round_trip():
    assert {k: c.round_trip for k, c in REGISTRY.items() if c.round_trip != "no"} == {
        "Notebook": "yes", "DataPipeline": "yes", "Warehouse": "yes"}


def test_a_capability_cannot_be_changed_once_made():
    with pytest.raises(dataclasses.FrozenInstanceError):
        capability("Notebook").git = "no"  # type: ignore[misc]
