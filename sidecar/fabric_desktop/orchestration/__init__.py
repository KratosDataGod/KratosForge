"""Fabric Data Factory pipelines on this PC (ORCH-001 onwards): docs/orchestration/plan.md.

    definition   read a pipeline definition in any supported format, a typed view over it, export
    expressions  find what an ``@`` expression refers to, by reading it (never evaluating it)
    validate     diagnostics: missing fields, duplicate names, bad dependencies, cycles, references
    registry     what this version can do with each activity type, and pipeline compatibility reports
    activities   the registry's entries (``default_registry()``)
    evaluate     expressions evaluated for a local run, with a fixed list of functions
    adapters     the activities a local run executes (control flow, child pipelines)
    runner       ``PipelineRun``: a local run on this PC, with mocks for everything else
    runs         where local run records are kept

Nothing here calls Fabric. Only ``runner`` runs anything (control flow, on this PC), and only
``runs`` writes (run records in the app's data folder).
"""

from .definition import DEFINITION_VERSION, DefinitionError, ImportedPipeline, export, load, loads
from .validate import Diagnostic, validate

__all__ = ["DEFINITION_VERSION", "DefinitionError", "Diagnostic", "ImportedPipeline", "export", "load", "loads",
           "validate"]
