# OpenLineage JSON schemas (test fixtures)

Copied unchanged from https://github.com/OpenLineage/OpenLineage/tree/main/spec on 2026-11-17, at the
versions the export names in its `schemaURL` and `_schemaURL` values:

| File | `$id` |
| --- | --- |
| OpenLineage.json | https://openlineage.io/spec/2-0-2/OpenLineage.json |
| SchemaDatasetFacet.json | https://openlineage.io/spec/facets/1-2-0/SchemaDatasetFacet.json |
| ColumnLineageDatasetFacet.json | https://openlineage.io/spec/facets/1-2-0/ColumnLineageDatasetFacet.json |
| JobTypeJobFacet.json | https://openlineage.io/spec/facets/2-0-4/JobTypeJobFacet.json |
| ProcessingEngineRunFacet.json | https://openlineage.io/spec/facets/1-1-1/ProcessingEngineRunFacet.json |

OpenLineage is licensed under the Apache License 2.0. `tests/test_openlineage.py` validates every event the
export writes against them, offline.
