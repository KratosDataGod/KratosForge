# Apache Ossie Microsoft converter (vendored)

Converts between a Power BI / Fabric semantic model in TMSL (`model.bim`) and an
[Apache Ossie](https://ossie.apache.org/) semantic model. KratosForge uses it for
Export to Ossie and Import from Ossie on the Semantic model tab (`model_ossie.py`).

- Source: https://github.com/apache/ossie-converters, folder `microsoft/src/ossie_microsoft`
- Commit: `82b9eff619c5b701de720b1bb0f02b23ea2c64bf` (2026-10-22), package version 0.2.0.dev0
- License: Apache License 2.0 (`LICENSE`, `NOTICE` in this folder)
- It is not on PyPI yet, so it is vendored rather than installed. Dependencies: PyYAML and
  sqlglot, both already engine dependencies.

## Changes from the source

- Left out `tom.py` (needs Microsoft's proprietary Tabular Object Model through pythonnet),
  `engine.py` (deploys models to Fabric for live validation) and `cli.py`.
- `__init__.py` exports only the converters.
- The two places that called `tom.py` for TMDL text raise `ValueError` instead. KratosForge
  reads and writes models as TMSL through Fabric's getDefinition and updateDefinition.

To update: copy those five modules from a newer commit, re-apply the two `# KratosForge:`
edits, update the commit above, and run `tests/test_model_ossie.py`.
