"""Fabric Data Factory pipelines on this PC (ORCH-001 onwards): docs/orchestration/plan.md.

    definition   read a pipeline definition in any supported format, a typed view over it, export
    expressions  find what an ``@`` expression refers to, by reading it (never evaluating it)
    validate     diagnostics: missing fields, duplicate names, bad dependencies, cycles, references

Nothing here runs a pipeline, calls Fabric or writes anywhere. Execution (ORCH-003) and the
activity compatibility registry (ORCH-002) build on this model.
"""

from .definition import DEFINITION_VERSION, DefinitionError, ImportedPipeline, export, load, loads
from .validate import Diagnostic, validate

__all__ = ["DEFINITION_VERSION", "DefinitionError", "Diagnostic", "ImportedPipeline", "export", "load", "loads",
           "validate"]
