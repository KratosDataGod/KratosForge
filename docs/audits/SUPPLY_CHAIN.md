# Supply chain: SBOMs, licenses and pinning

This page records what KratosForge ships, which licenses it ships under, and how each part is pinned. It was checked against main on 2026-11-06. License data comes from two sources:
- **Engine packages:** PyPI's metadata for each pinned package. Where the metadata was blank, I read the wheel itself.
- **Shell crates:** `cargo metadata` for the Windows target.

## What ships

| Part | What it is | Where its parts are pinned | SBOM |
| --- | --- | --- | --- |
| Engine | python.org's embeddable Python 3.12.10, the 76 packages the engine installs, and the engine's own package | `sidecar/engine-requirements.txt` holds versions and SHA-256 hashes. `ci.yml` holds the Python zip's MD5 and SHA-256 | `sbom-engine.cdx.json`, made by `cyclonedx-py environment` from the built engine folder |
| Shell | The Tauri app (`app/src-tauri`) and its 243 Rust crates on Windows | `app/src-tauri/Cargo.lock` holds versions and crate checksums | `sbom-app.cdx.json`, made by `cargo cyclonedx` from `Cargo.lock` |
| Notebook editor | CodeMirror, bundled into `app/ui/vendor/codemirror/codemirror.js` | That folder's `package.json` gives exact versions for the direct packages. The built file is committed | Its license is in `LICENSE` there |
| Charts | Vega, Vega-Lite, vega-embed and the d3 packages they bundle, in `app/ui/vendor/vega/vega-frame.js` | That folder's `package.json` gives exact versions for the direct packages. `THIRD-PARTY-NOTICES.md` there lists each bundled package and its version | Its packages and licenses are in `THIRD-PARTY-NOTICES.md` there |
| Model converter | The Apache Ossie Microsoft converter, copied into `sidecar/fabric_desktop/_vendor/ossie_microsoft` | Committed source | Covered by the engine's own package |
| VS Code extension | `vscode/` | `vscode/package-lock.json`. It has no runtime dependencies, only build and test tools | None needed |

Every CI build makes both SBOMs: `sbom-engine` in the engine job, and `sbom-app` in its own job. Both are CycloneDX JSON. The release job attaches them to each Preview and Stable release. The engine and the shell need no new CI job.

## Licenses

**No GPL, LGPL or AGPL code ships, and every component has a known license.** Four things are worth knowing:

- **MPL-2.0, a file-level weak copyleft.** It applies to `certifi` in the engine and to five crates in the shell: `cssparser`, `cssparser-macros`, `dtoa-short`, `option-ext` and `selectors`. All six ship unmodified. MPL-2.0 asks that changes to those files stay under the same license and that their source is available. It doesn't reach the rest of the app.
- **Microsoft's own terms.** Two packages ship Microsoft components under Microsoft's license terms:
  - `mssql-python-odbc` carries Microsoft ODBC Driver 18 for SQL Server and the Visual C++ runtime.
  - `mssql-python` ships its DLLs under the same terms. Its own code is MIT.

  `NOTICE` and `docs/install.md` already say this, and the license files ship in the engine.
- **PSF-style licenses, which are permissive.** The engine's Python, `typing-extensions` and `pywin32` use the PSF license. `matplotlib` uses its own PSF-based license.
- **Blank PyPI metadata.** Two packages have none, so I read their wheels instead. `azure-core`'s wheel says MIT. `mssql-python`'s wheel says MIT, apart from its DLLs.

### Shell crates (Windows target, 243)

| License | Crates |
| --- | --- |
| A choice that includes MIT or Apache-2.0 (dual or triple licensed, sometimes with Zlib, 0BSD, Unlicense or Unicode-3.0) | 170 |
| MIT | 43 |
| Unicode-3.0 | 18 |
| MPL-2.0 | 5 (named above) |
| BSD-3-Clause, alone or with MIT | 4 |
| Zlib | 2 |
| Apache-2.0 | 1 |

The full list, crate by crate, is in each release's `sbom-app.cdx.json`.

### Engine packages (76)

| Package | Version | License | Note |
| --- | --- | --- | --- |
| altair | 6.3.0 | BSD-3-Clause |  |
| annotated-types | 0.8.0 | MIT |  |
| anyio | 4.15.1 | MIT |  |
| arro3-core | 0.9.1 | MIT OR Apache-2.0 |  |
| attrs | 26.1.0 | MIT |  |
| azure-core | 1.41.0 | MIT | PyPI metadata blank; read from the wheel |
| azure-identity | 1.26.0 | MIT |  |
| bokeh | 3.10.1 | BSD-3-Clause |  |
| certifi | 2026.10.19 | MPL-2.0 | Weak copyleft (file-level), shipped unmodified |
| cffi | 2.1.1 | MIT-0 |  |
| charset-normalizer | 3.5.1 | MIT |  |
| click | 8.5.0 | BSD-3-Clause |  |
| contourpy | 1.4.0 | BSD-3-Clause |  |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause |  |
| cycler | 0.12.1 | BSD-3-Clause |  |
| deltalake | 1.6.6 | Apache-2.0 |  |
| deprecated | 3.0.0 | MIT |  |
| duckdb | 1.5.6 | MIT |  |
| fonttools | 4.67.0 | MIT |  |
| h11 | 0.16.0 | MIT |  |
| httpcore2 | 2.13.1 | BSD-3-Clause |  |
| httpx2 | 2.13.1 | BSD-3-Clause |  |
| idna | 3.20 | BSD-3-Clause |  |
| jinja2 | 3.1.6 | BSD-3-Clause |  |
| jsonschema | 4.26.0 | MIT |  |
| jsonschema-specifications | 2025.9.1 | MIT |  |
| kiwisolver | 1.5.1 | BSD-3-Clause |  |
| markupsafe | 3.0.4 | BSD-3-Clause |  |
| matplotlib | 3.11.2 | Matplotlib license (PSF-based) |  |
| mcp | 2.3.0 | MIT |  |
| mcp-types | 2.3.0 | MIT |  |
| msal | 1.39.0 | MIT |  |
| msal-extensions | 1.3.1 | MIT |  |
| mssql-python | 1.16.0 | MIT, except its bundled DLLs | DLLs under Microsoft's terms; PyPI metadata blank |
| mssql-python-odbc | 18.6.2.2 | Microsoft software license terms | ODBC Driver 18 and the Visual C++ runtime, redistributed under Microsoft's terms |
| mssql-python-rs | 0.3.0 | MIT |  |
| narwhals | 2.27.1 | MIT |  |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |  |
| opentelemetry-api | 1.45.1 | Apache-2.0 |  |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |  |
| pandas | 2.3.3 | BSD-3-Clause |  |
| pillow | 13.0.0 | MIT-CMU |  |
| pip | 26.2.2 | MIT |  |
| plotly | 7.1.0 | MIT |  |
| polars | 2.0.0 | MIT |  |
| polars-runtime-32 | 2.0.0 | MIT |  |
| pyarrow | 26.0.0 | Apache-2.0 |  |
| pycparser | 3.0 | BSD-3-Clause |  |
| pydantic | 2.14.1 | MIT |  |
| pydantic-core | 2.50.1 | MIT |  |
| pyjwt | 2.15.0 | MIT |  |
| pymsalruntime | 0.20.7 | MIT |  |
| pyparsing | 3.1.1 | MIT |  |
| python-dateutil | 2.9.0.post0 | Apache-2.0 OR BSD-3-Clause |  |
| python-multipart | 0.0.32 | Apache-2.0 |  |
| pytz | 2026.5 | MIT |  |
| pywin32 | 312 | PSF-2.0 |  |
| pyyaml | 6.0.3 | MIT |  |
| referencing | 0.37.0 | MIT |  |
| requests | 2.34.2 | Apache-2.0 |  |
| rpds-py | 2026.9.1 | MIT |  |
| ruff | 0.17.1 | MIT |  |
| seaborn | 0.13.2 | BSD-3-Clause |  |
| six | 1.17.0 | MIT |  |
| sqlglot | 30.23.0 | MIT |  |
| sse-starlette | 3.5.0 | BSD-3-Clause |  |
| starlette | 1.8.0 | BSD-3-Clause |  |
| tornado | 6.6 | Apache-2.0 |  |
| truststore | 0.10.5 | MIT |  |
| typing-extensions | 4.16.0 | PSF-2.0 |  |
| typing-inspection | 0.4.4 | MIT |  |
| tzdata | 2026.5 | Apache-2.0 |  |
| urllib3 | 2.8.0 | MIT |  |
| uvicorn | 0.54.0 | BSD-3-Clause |  |
| wrapt | 2.5.1 | BSD-2-Clause |  |
| xyzservices | 2026.9.1 | BSD-3-Clause |  |

Some wheels bundle native libraries of their own, for example pyarrow and polars. A package's license notes for those libraries ship inside the package. The SBOM lists the package, not each library it bundles.

### Bundled JavaScript

- **CodeMirror:** MIT.
- **Vega, Vega-Lite and vega-embed:** BSD-3-Clause.
- **The packages the Vega build bundles:** ISC, BSD, MIT and Apache-2.0 licenses, listed one by one in `app/ui/vendor/vega/THIRD-PARTY-NOTICES.md`.

## Pinning

| What | Pinned by | Hash-checked on install |
| --- | --- | --- |
| Engine packages | `engine-requirements.txt` | Yes: `pip install --require-hashes --only-binary=:all:`, then `pip check` |
| Engine Python | `PYTHON_VERSION` in `ci.yml` | Yes: python.org's MD5 and a pinned SHA-256 |
| Shell crates | `Cargo.lock` | Yes: `cargo build --locked` checks each crate's checksum against the lock file |
| GitHub Actions | A commit SHA on every `uses:` line in every workflow (16 actions) | Yes, by the commit SHA |
| CI-only Rust tools (cargo-audit, cargo-cyclonedx) | An exact version, with `cargo install --locked` | Yes, through each tool's own lock file |
| Bundled JavaScript | The built files are committed | They can't change without a PR |

