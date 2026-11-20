# Apache Ossie JSON Schema (vendored)

The JSON Schema (draft 2020-12) of an [Apache Ossie](https://ossie.apache.org/) semantic model
document, at two pinned commits. KratosForge checks an Ossie file against the schema for the
version the file declares (`ossie_validate.py`). The schema is data: it is read from this folder
and never fetched at run time, and the validator resolves no `$ref` outside it.

| File | Version | Source |
| --- | --- | --- |
| `ossie-schema-0.2.0.dev0.json` | `0.2.0.dev0` | https://raw.githubusercontent.com/apache/ossie/21d14d7a4044e71a19ea46d024c974a57323174a/core-spec/ossie-schema.json |
| `ossie-schema-0.3.0.dev0.json` | `0.3.0.dev0` | https://raw.githubusercontent.com/apache/ossie/aa500d37894c3d62c96cdce4192cbecea4d7f06a/core-spec/ossie-schema.json |

- Repository: https://github.com/apache/ossie, file `core-spec/ossie-schema.json`
- 0.2.0.dev0: commit `21d14d7a4044e71a19ea46d024c974a57323174a` (2026-11-09), the last commit before
  `c1a33181c815852f059ce7986729612332240ff8` ("chore: move the in-development version to
  0.3.0.dev0 (#691)") changed the version. The schema file there was last changed by
  `21e849da3bc207c03e79efa2a0f21b325fe3bf86` (#633). This is the version the vendored converter
  (`../ossie_microsoft`, commit `82b9eff`) reads and writes, and the one Fabric Semantic Views take.
- 0.3.0.dev0: commit `aa500d37894c3d62c96cdce4192cbecea4d7f06a` (2026-11-15).
- Fetched: 2026-11-19
- License: Apache License 2.0 (`LICENSE`, `NOTICE` in this folder, from the same commits; they are
  the same at both)
- Upstream's example semantic models at the same commits (`examples/flights.semantic_model.yaml`,
  `renamed_fields.yaml`, `tpcds_semantic_model.yaml`) are test fixtures in
  `sidecar/tests/fixtures/ossie_schema/<version>/`. Upstream's other examples are ontology and
  mapping documents, which have schemas of their own, so they are left out.

## SHA-256

`tests/test_ossie_schema.py` checks these against the files.

| File | SHA-256 |
| --- | --- |
| `ossie-schema-0.2.0.dev0.json` | `ea513794167c8d67e29fb7a7c537c9e2c33e718fb1e50becbe1ac389884ae662` |
| `ossie-schema-0.3.0.dev0.json` | `8f8616e3e658e76d4c39091e4d2a046501209cd67d87e1e922e1cf5145a4a0ba` |
| `LICENSE` | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` |
| `NOTICE` | `cdd61e3d225833b912985567730c9043394ae21e3ecfe619ed63cff5f170c59a` |
| `tests/fixtures/ossie_schema/0.2.0.dev0/flights.semantic_model.yaml` | `83e73c40d47f8476e01b3fa09ad0deb668576fed87a6d19dc837741a1ae47ca5` |
| `tests/fixtures/ossie_schema/0.2.0.dev0/renamed_fields.yaml` | `a2f4103c0f8facc55b1a26accf8a7ce613c6889699afdcc6abb46dd7814726b0` |
| `tests/fixtures/ossie_schema/0.2.0.dev0/tpcds_semantic_model.yaml` | `c668366ef9009eddfc82db60d3ea6a298e5489c3118a1e5df0c7febb49c2944d` |
| `tests/fixtures/ossie_schema/0.3.0.dev0/flights.semantic_model.yaml` | `5f50e53809f1ecd3983ae43a38ecdf85a9a8f7703cdcf2794e57dd121b153d45` |
| `tests/fixtures/ossie_schema/0.3.0.dev0/renamed_fields.yaml` | `fa277392596e8c5694b4edc596ad378f46091c78bf142bf59fad7b7435749ef1` |
| `tests/fixtures/ossie_schema/0.3.0.dev0/tpcds_semantic_model.yaml` | `14761a808311f50c8ca8ec68c1e55757207aa39d69aa922c36e866ea32d8cae2` |

The fixture paths are relative to `sidecar/`.

## Changes from the source

None: each file is byte for byte as published, renamed by version.

To update: download `core-spec/ossie-schema.json` and the examples from a newer commit, add or
replace the file for its version, update the commits and hashes above, and run
`tests/test_ossie_schema.py`. A version with no file here gets a named diagnostic, never a pass.
