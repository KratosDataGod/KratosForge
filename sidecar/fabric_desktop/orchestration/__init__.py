"""Fabric Data Factory pipelines on this PC (ORCH-001 onwards): docs/orchestration/plan.md.

    definition   read a pipeline definition in any supported format, a typed view over it, export
    expressions  find what an ``@`` expression refers to, by reading it (never evaluating it)
    validate     diagnostics: missing fields, duplicate names, bad dependencies, cycles, references
    registry     what this version can do with each activity type, and pipeline compatibility reports
    activities   the registry's entries (``default_registry()``)

Nothing here runs a pipeline, calls Fabric or writes anywhere. Execution (ORCH-003) builds on
this model and the registry.
"""

from .definition import DEFINITION_VERSION, DefinitionError, ImportedPipeline, export, load, loads
from .validate import Diagnostic, validate

__all__ = ["DEFINITION_VERSION", "DefinitionError", "Diagnostic", "ImportedPipeline", "export", "load", "loads",
           "validate"]
